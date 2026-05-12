"""Assetto Corsa Rally shared-memory telemetry source.

AC Rally publishes under the same ``Local\\acpmf_*`` tag names AC1 and
ACC use, with the **exact same 800-byte physics layout** as AC Evo / ACC
(verified via ``tools/probe_rally_layout.py`` against a running game).
We reuse ACC's struct definitions verbatim — only the apply step needs
to differ to handle AC Rally's quirks.

Quirks vs ACC, observed empirically:

* **Temperatures are in Kelvin.** ``tyreCoreTemp``, ``brakeTemp``,
  ``tyreTemp`` (the duplicate at offset 696), ``waterTemp`` all need a
  −273.15 offset before they hit the TelemetryFrame. Sample probe
  values: ``tyreCoreTemp = 363.15`` (=90 °C), ``brakeTemp = 322`` (=49 °C),
  ``waterTemp = 343.60`` (=70.45 °C) — all sensible after conversion.
* **wheelLoad IS published** (unlike ACC). The probe showed plausible
  static loads (~3.7 kN front, ~3.1 kN rear), so the load circle and
  contact-patch bars work normally — leave ``has_wheel_load`` True.
* **camberRAD / rideHeight / tyreTempI/M/O are NOT published** (same as
  ACC). Hide ride-height; per-face IMO falls back to core temp; camber
  stays 0 (tire silhouette upright).
* **padLife / discLife scale unknown.** Probe values at session start
  were ~1.6e-5 / 3.2e-5 — different from ACC's 0..1 fresh scale. The
  brake-wear bars self-calibrate against the per-wheel max observed
  this session, so an unknown scale just means the bar starts full and
  shrinks correctly from there.
* **Static block reads as zeros at session start** (probably written
  later, possibly only when entering a stage). _apply_static guards
  every assignment so a zero static block doesn't clobber sensible
  defaults.
* **currentMaxRpm is int32 like AC Evo** — same denormal trap ACC has
  if mistyped as float. We inherit the c_int32 from the ACC struct.
"""
from __future__ import annotations

import math
import sys
from typing import Optional

from PySide6.QtCore import QObject, QTimer

from ..interpolation import (Curve, DEFAULT_BRAKE_TEMP_CURVE,
                              DEFAULT_TIRE_TEMP_CURVE)
from ..telemetry import TelemetryFrame, WHEEL_IDS
from ._win32_mapping import NamedMapping
from .acc import (_SPageFilePhysics, _SPageFileGraphic, _SPageFileStatic,
                   _ACC_IDEAL_PSI, _LOCK_SLIP_THRESHOLD, _ABS_SLIP_THRESHOLD,
                   PHYSICS_TAG, GRAPHICS_TAG, STATIC_TAG,
                   PHYSICS_SIZE, GRAPHICS_SIZE, STATIC_SIZE)
from .base import TelemetrySource


_WHEEL_INDEX = {"FL": 0, "FR": 1, "RL": 2, "RR": 3}

# AC Rally publishes temperatures in Kelvin; subtract this to land
# Celsius (the unit the rest of the overlay expects).
_KELVIN_OFFSET_C = 273.15
# Anything above this temperature is implausibly hot in °C and almost
# certainly Kelvin. Used as a safety guard in case a future patch
# changes units mid-stream.
_KELVIN_HEURISTIC_C = 200.0


def _k_to_c(k: float) -> float:
    """Convert AC Rally's Kelvin temperatures to Celsius. Defensive: if
    a value already looks like Celsius (under 200 °C) we pass it through
    unchanged so a future units fix doesn't break the overlay."""
    return k - _KELVIN_OFFSET_C if k > _KELVIN_HEURISTIC_C else k


class AcRallySharedMemoryReader:
    """Same shared-memory blocks as ACC; identical struct layout."""

    def __init__(self) -> None:
        self._physics_mm: Optional[NamedMapping] = None
        self._graphics_mm: Optional[NamedMapping] = None
        self._static_mm: Optional[NamedMapping] = None

    def open(self) -> None:
        self._physics_mm = NamedMapping(PHYSICS_TAG, PHYSICS_SIZE)
        try:
            self._graphics_mm = NamedMapping(GRAPHICS_TAG, GRAPHICS_SIZE)
            self._static_mm = NamedMapping(STATIC_TAG, STATIC_SIZE)
        except OSError:
            self.close()
            raise

    def close(self) -> None:
        for mm in (self._physics_mm, self._graphics_mm, self._static_mm):
            if mm is not None:
                mm.close()
        self._physics_mm = self._graphics_mm = self._static_mm = None

    @property
    def is_open(self) -> bool:
        return self._physics_mm is not None

    def read_physics(self) -> _SPageFilePhysics:
        if self._physics_mm is None:
            raise RuntimeError("reader is not open")
        return _SPageFilePhysics.from_buffer_copy(self._physics_mm.read(), 0)

    def read_static(self) -> _SPageFileStatic:
        if self._static_mm is None:
            raise RuntimeError("reader is not open")
        return _SPageFileStatic.from_buffer_copy(self._static_mm.read(), 0)

    def read_graphics(self) -> _SPageFileGraphic:
        if self._graphics_mm is None:
            raise RuntimeError("reader is not open")
        return _SPageFileGraphic.from_buffer_copy(self._graphics_mm.read(), 0)


class AcRallyTelemetrySource(TelemetrySource):
    """Polls AC Rally shared memory and emits :class:`TelemetryFrame`."""

    def __init__(self, hz: int = 60, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._reader = AcRallySharedMemoryReader()
        self._frame = TelemetryFrame()
        self._tire_curve = Curve(DEFAULT_TIRE_TEMP_CURVE)
        self._brake_curve = Curve(DEFAULT_BRAKE_TEMP_CURVE)
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / hz))
        # pylint: disable-next=no-member  # QTimer.timeout is a PySide6 Signal
        self._timer.timeout.connect(self._tick)
        self._reconnect_countdown = 0

    def start(self) -> None:
        self._try_connect()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._reader.close()

    def _try_connect(self) -> bool:
        if self._reader.is_open:
            return True
        try:
            self._reader.open()
            self._apply_static(self._reader.read_static())
            print("[acrally] connected to shared memory")
            return True
        except (OSError, RuntimeError) as exc:
            if not isinstance(exc, FileNotFoundError):
                print(f"[acrally] connect failed: {exc}", file=sys.stderr)
            return False

    def _tick(self) -> None:
        if not self._reader.is_open:
            self._reconnect_countdown -= 1
            if self._reconnect_countdown <= 0:
                self._reconnect_countdown = 60
                if not self._try_connect():
                    return
            else:
                return

        try:
            phys = self._reader.read_physics()
            graphics = self._reader.read_graphics()
        except (OSError, ValueError) as exc:
            print(f"[acrally] read failed, dropping connection: {exc}",
                  file=sys.stderr)
            self._reader.close()
            return

        self._apply_graphics(graphics)
        self._apply_physics(phys)
        self.frame.emit(self._frame)

    def _apply_static(self, st: _SPageFileStatic) -> None:
        """AC Rally writes the static block lazily (zeros at session
        start, populated later). Guard every assignment so a 0 doesn't
        clobber a sensible default."""
        e = self._frame.engine
        if st.maxRpm > 0:
            e.max_rpm = float(st.maxRpm)
        if st.maxPower > 0:
            e.max_power = float(st.maxPower)
        if st.maxTorque > 0:
            e.max_torque = float(st.maxTorque)
        if st.maxTurboBoost > 0:
            e.max_turbo_boost = float(st.maxTurboBoost)

        for wid in WHEEL_IDS:
            idx = _WHEEL_INDEX[wid]
            w = self._frame.wheels[wid]
            sm = float(st.suspensionMaxTravel[idx])
            if sm > 0.0:
                w.susp_m_t = sm

    def _apply_physics(self, ph: _SPageFilePhysics) -> None:
        e = self._frame.engine
        e.rpm = float(ph.rpm)
        e.turbo_boost = float(ph.turboBoost)
        e.max_turbo_boost = max(e.max_turbo_boost, e.turbo_boost)
        e.gear = int(ph.gear)
        e.speed_kmh = float(ph.speedKmh)
        e.tc_level = float(ph.tc)
        e.abs_level = float(ph.abs)
        e.pit_limiter = bool(ph.pitLimiterOn)
        e.brake_bias = float(ph.brakeBias)
        # currentMaxRpm is int32 (same denormal trap as ACC if mistyped
        # as float). The struct from acc.py already declares it int32.
        if ph.currentMaxRpm > 1000:
            e.max_rpm = float(ph.currentMaxRpm)
        e.tc_in_action = ph.tcInAction != 0
        e.abs_in_action = ph.absInAction != 0
        e.drs_available = bool(ph.drsAvailable)
        e.drs_enabled = bool(ph.drsEnabled) or float(ph.drs) > 0.5
        e.ers_charging = bool(ph.ersIsCharging)
        e.fuel_liters = float(ph.fuel)
        # waterTemp is in Kelvin on AC Rally — convert.
        e.water_temp_c = _k_to_c(float(ph.waterTemp))

        i = self._frame.inputs
        i.throttle = max(0.0, min(1.0, float(ph.gas)))
        i.brake = max(0.0, min(1.0, float(ph.brake)))
        i.clutch = max(0.0, min(1.0, float(ph.clutch)))
        i.steering = max(-1.0, min(1.0, float(ph.steerAngle) / (math.pi / 4)))
        i.steering_deg = math.degrees(float(ph.steerAngle))
        i.ffb = max(0.0, min(1.0, abs(float(ph.finalFF))))
        i.g_lat = float(ph.accG[0])
        i.g_vert = float(ph.accG[1])
        i.g_long = float(ph.accG[2])
        i.damage = tuple(float(ph.carDamage[k]) for k in range(5))
        i.tyres_out = int(ph.numberOfTyresOut)

        braking = ph.brake > 0.0

        for wid in WHEEL_IDS:
            idx = _WHEEL_INDEX[wid]
            w = self._frame.wheels[wid]

            slip = abs(float(ph.wheelSlip[idx]))
            w.lock = bool(braking and ph.speedKmh > 5.0
                          and slip > _LOCK_SLIP_THRESHOLD)
            w.abs_active = bool(ph.absInAction and braking
                                and not w.lock and slip > _ABS_SLIP_THRESHOLD)

            # AC Rally doesn't publish camberRAD or rideHeight — same as
            # ACC. Camber stays 0 (tire silhouette renders upright since
            # the rotation handles 0 cleanly). Ride-height icon hides.
            # has_camber=False also hides the contact-patch bars: the
            # bar heights encode camber × pressure × load, so without
            # a camber signal they over-promise what they're showing.
            w.camber = float(ph.camberRAD[idx])
            w.has_ride_height = False
            w.has_camber = False

            w.susp_t = abs(float(ph.suspensionTravel[idx]))
            if w.susp_m_t <= 0.0 and w.susp_t > 0.0:
                w.susp_m_t = w.susp_t * 2.0
            elif w.susp_m_t > 0.0 and w.susp_t * 1.05 > w.susp_m_t:
                w.susp_m_t = w.susp_t * 1.05

            w.tire_d = float(ph.tyreDirtyLevel[idx]) * 4.0
            # AC Rally publishes wheelLoad correctly (unlike ACC) — leave
            # has_wheel_load True so load circle + contact bars work.
            w.tire_l = float(ph.wheelLoad[idx])
            w.tire_p = float(ph.wheelPressure[idx])
            w.tire_p_norm = w.tire_p / _ACC_IDEAL_PSI if _ACC_IDEAL_PSI > 0 else 1.0

            # Temperatures from Kelvin to Celsius across the board.
            w.tire_t_c = _k_to_c(float(ph.tyreCoreTemp[idx]))
            face_i = float(ph.tyreTempI[idx])
            face_m = float(ph.tyreTempM[idx])
            face_o = float(ph.tyreTempO[idx])
            # Per-face I/M/O slots aren't populated on AC Rally — fall
            # back to core temp so the IMO grid renders one uniform colour
            # rather than three permanent-blue cells.
            w.tire_t_i = _k_to_c(face_i) if face_i > 0.0 else w.tire_t_c
            w.tire_t_m = _k_to_c(face_m) if face_m > 0.0 else w.tire_t_c
            w.tire_t_o = _k_to_c(face_o) if face_o > 0.0 else w.tire_t_c
            w.tire_t_norm_c = self._tire_curve.interpolate(w.tire_t_c)
            w.tire_t_norm_i = self._tire_curve.interpolate(w.tire_t_i)
            w.tire_t_norm_m = self._tire_curve.interpolate(w.tire_t_m)
            w.tire_t_norm_o = self._tire_curve.interpolate(w.tire_t_o)

            w.brake_t = _k_to_c(float(ph.brakeTemp[idx]))
            w.brake_t_norm = self._brake_curve.interpolate(w.brake_t)

            # padLife / discLife are published but at an unknown scale
            # (probe showed ~1.6e-5 / 3.2e-5 at session start). The
            # widget's per-wheel rolling-max calibration handles unknown
            # scales correctly: the bar starts full and shrinks.
            w.pad_w = float(ph.padLife[idx])
            w.disc_w = float(ph.discLife[idx])

            # AC Rally's tyreWear slot is in the struct but the game
            # doesn't publish meaningful values (same as ACC). Hide
            # the bar rather than rendering a stuck-fresh value.
            w.has_tire_wear = False

    def _apply_graphics(self, gr: _SPageFileGraphic) -> None:
        """Pull the few graphics-block fields the overlay uses on AC
        Rally. Same struct as ACC's graphics block — most fields will
        be empty when not in a stage, so guard accordingly."""
        e = self._frame.engine
        if gr.TC > 0:
            e.tc_level = float(gr.TC)
        if gr.ABS > 0:
            e.abs_level = float(gr.ABS)
        if gr.exhaustTemperature > 0:
            e.exhaust_temp_c = _k_to_c(float(gr.exhaustTemperature))
        e.valid_lap = bool(gr.isValidLap)

        compound = (gr.tyreCompound or "").strip()
        for wid in WHEEL_IDS:
            self._frame.wheels[wid].compound = compound
