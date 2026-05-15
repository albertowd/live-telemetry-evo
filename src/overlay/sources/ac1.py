"""Assetto Corsa 1 shared-memory telemetry source.

The original AC publishes three named file-mappings on Windows:

    Local\\acpmf_physics    — physics tick (high-rate)
    Local\\acpmf_graphics   — HUD/graphics frame
    Local\\acpmf_static     — once-per-session metadata

The struct layout is the one the AC1 plugin SDK ships and the
``LiveTelemetry`` plugin (D:/projects/live-telemetry) uses — see that
project's ``apps/python/LiveTelemetry/lib/sim_info.py`` for the canonical
reference. AC Evo took this layout and extended it; many AC Evo fields
(``current_bhp``, ``slipRatio``, ``padLife``, normalised temps/pressure,
…) simply don't exist on AC1 and stay at their :class:`TelemetryFrame`
defaults here. The overlay's widgets already render sensible fallbacks
when those fields are "unpublished" (negative / 1.0 / 0.0 by convention),
so the user sees a clean signal on AC1 instead of fake values.

A couple of AC1-specific details worth noting:

* The static block **does** carry the car spec sheet on AC1
  (``maxRpm`` / ``maxPower`` / ``maxTorque`` / ``maxTurboBoost`` /
  ``suspensionMaxTravel`` / ``tyreRadius``) — AC Evo slimmed those out.
  We seed the engine + per-wheel suspension calibration directly from
  the static block, so the bars are correctly scaled from the first
  tick.
* AC1's ``tyreWear`` is **% remaining** (100 = fresh, 0 = bald), the
  exact opposite of AC Evo's documented (but unwritten) "fraction worn"
  scale. We convert to the overlay's ``tire_w`` convention (1.0 = fresh)
  at the apply step.
* Per-face normalised temperatures / pressure don't exist on AC1, so
  we synthesise them by interpolating the same tyre-temp curve the
  synthetic source uses and assuming an ideal cold pressure of 27 psi.
  This keeps the colour bands on the widgets working — they'd otherwise
  stick at "ideal green" regardless of actual temperature/pressure.
* Lock / ABS-active flags aren't published on AC1. We use a simple
  slip-threshold heuristic so the brake-icon blink still fires when the
  driver locks up.
"""
from __future__ import annotations

import ctypes
import math
import sys
from ctypes import c_float, c_int32, c_wchar
from typing import Optional

from PySide6.QtCore import QObject, QTimer

from ..interpolation import (Curve, DEFAULT_BRAKE_TEMP_CURVE,
                              DEFAULT_TIRE_TEMP_CURVE)
from ..telemetry import TelemetryFrame, WHEEL_IDS
from ._win32_mapping import NamedMapping
from .ac1_acd import ACD
from .ac1_install import find_car_dir
from .base import TelemetrySource


# Shared-memory tag names. AC1's plugin SDK uses bare names; Windows
# resolves those to the per-session "Local\" namespace automatically,
# which is where the game publishes them.
PHYSICS_TAG = "Local\\acpmf_physics"
GRAPHICS_TAG = "Local\\acpmf_graphics"
STATIC_TAG = "Local\\acpmf_static"

# Map sizes — AC1's structs are smaller than AC Evo's. We map a
# generously-sized region so trailing reserved bytes / future fields
# don't crash the reader; mapping more than the producer wrote is
# harmless on Windows (zero-filled).
PHYSICS_SIZE = 1024
GRAPHICS_SIZE = 1024
STATIC_SIZE = 1024


# Order of the wheel arrays in AC1 / AC Evo: 0=FL, 1=FR, 2=RL, 3=RR.
_WHEEL_INDEX = {"FL": 0, "FR": 1, "RL": 2, "RR": 3}


class _SPageFilePhysics(ctypes.Structure):
    """AC1 SPageFilePhysics, mirroring the plugin SDK's layout."""

    _pack_ = 4
    _fields_ = [
        ("packetId", c_int32),
        ("gas", c_float),
        ("brake", c_float),
        ("fuel", c_float),
        ("gear", c_int32),
        ("rpms", c_int32),
        ("steerAngle", c_float),
        ("speedKmh", c_float),
        ("velocity", c_float * 3),
        ("accG", c_float * 3),
        ("wheelSlip", c_float * 4),
        ("wheelLoad", c_float * 4),
        ("wheelsPressure", c_float * 4),
        ("wheelAngularSpeed", c_float * 4),
        ("tyreWear", c_float * 4),         # % remaining: 100 fresh, 0 bald
        ("tyreDirtyLevel", c_float * 4),
        ("tyreCoreTemperature", c_float * 4),
        ("camberRAD", c_float * 4),
        ("suspensionTravel", c_float * 4),
        ("drs", c_float),
        ("tc", c_float),
        ("heading", c_float),
        ("pitch", c_float),
        ("roll", c_float),
        ("cgHeight", c_float),
        ("carDamage", c_float * 5),
        ("numberOfTyresOut", c_int32),
        ("pitLimiterOn", c_int32),
        ("abs", c_float),
        ("kersCharge", c_float),
        ("kersInput", c_float),
        ("autoShifterOn", c_int32),
        ("rideHeight", c_float * 2),
        ("turboBoost", c_float),
        ("ballast", c_float),
        ("airDensity", c_float),
        ("airTemp", c_float),
        ("roadTemp", c_float),
        ("localAngularVel", c_float * 3),
        ("finalFF", c_float),
        ("performanceMeter", c_float),
        ("engineBrake", c_int32),
        ("ersRecoveryLevel", c_int32),
        ("ersPowerLevel", c_int32),
        ("ersHeatCharging", c_int32),
        ("ersIsCharging", c_int32),
        ("kersCurrentKJ", c_float),
        ("drsAvailable", c_int32),
        ("drsEnabled", c_int32),
        ("brakeTemp", c_float * 4),
        ("clutch", c_float),
        ("tyreTempI", c_float * 4),
        ("tyreTempM", c_float * 4),
        ("tyreTempO", c_float * 4),
        ("isAIControlled", c_int32),
        ("tyreContactPoint", (c_float * 3) * 4),
        ("tyreContactNormal", (c_float * 3) * 4),
        ("tyreContactHeading", (c_float * 3) * 4),
        ("brakeBias", c_float),
        ("localVelocity", c_float * 3),
    ]


class _SPageFileGraphic(ctypes.Structure):
    """AC1 SPageFileGraphic — slim compared to AC Evo's graphics block."""

    _pack_ = 4
    _fields_ = [
        ("packetId", c_int32),
        ("status", c_int32),
        ("session", c_int32),
        ("currentTime", c_wchar * 15),
        ("lastTime", c_wchar * 15),
        ("bestTime", c_wchar * 15),
        ("split", c_wchar * 15),
        ("completedLaps", c_int32),
        ("position", c_int32),
        ("iCurrentTime", c_int32),
        ("iLastTime", c_int32),
        ("iBestTime", c_int32),
        ("sessionTimeLeft", c_float),
        ("distanceTraveled", c_float),
        ("isInPit", c_int32),
        ("currentSectorIndex", c_int32),
        ("lastSectorTime", c_int32),
        ("numberOfLaps", c_int32),
        ("tyreCompound", c_wchar * 33),
        ("replayTimeMultiplier", c_float),
        ("normalizedCarPosition", c_float),
        ("carCoordinates", c_float * 3),
        ("penaltyTime", c_float),
        ("flag", c_int32),
        ("idealLineOn", c_int32),
        ("isInPitLane", c_int32),
        ("surfaceGrip", c_float),
        ("mandatoryPitDone", c_int32),
        ("windSpeed", c_float),
        ("windDirection", c_float),
    ]


class _SPageFileStatic(ctypes.Structure):
    """AC1 SPageFileStatic — car spec sheet + session metadata."""

    _pack_ = 4
    _fields_ = [
        ("smVersion", c_wchar * 15),
        ("acVersion", c_wchar * 15),
        ("numberOfSessions", c_int32),
        ("numCars", c_int32),
        ("carModel", c_wchar * 33),
        ("track", c_wchar * 33),
        ("playerName", c_wchar * 33),
        ("playerSurname", c_wchar * 33),
        ("playerNick", c_wchar * 33),
        ("sectorCount", c_int32),
        ("maxTorque", c_float),
        ("maxPower", c_float),
        ("maxRpm", c_int32),
        ("maxFuel", c_float),
        ("suspensionMaxTravel", c_float * 4),
        ("tyreRadius", c_float * 4),
        ("maxTurboBoost", c_float),
        ("airTemp", c_float),
        ("roadTemp", c_float),
        ("penaltiesEnabled", c_int32),
        ("aidFuelRate", c_float),
        ("aidTireRate", c_float),
        ("aidMechanicalDamage", c_float),
        ("aidAllowTyreBlankets", c_int32),
        ("aidStability", c_float),
        ("aidAutoClutch", c_int32),
        ("aidAutoBlip", c_int32),
        ("hasDRS", c_int32),
        ("hasERS", c_int32),
        ("hasKERS", c_int32),
        ("kersMaxJ", c_float),
        ("engineBrakeSettingsCount", c_int32),
        ("ersPowerControllerCount", c_int32),
        ("trackSPlineLength", c_float),
        ("trackConfiguration", c_wchar * 33),
        ("ersMaxJ", c_float),
        ("isTimedRace", c_int32),
        ("hasExtraLap", c_int32),
        ("carSkin", c_wchar * 33),
        ("reversedGridPositions", c_int32),
        ("pitWindowStart", c_int32),
        ("pitWindowEnd", c_int32),
    ]


# Synthetic ideal cold pressure used to compute a normalised pressure
# fallback (AC1 doesn't publish a per-compound ideal). Most race / sim
# pressures hover around 27 psi cold, so this is a reasonable default —
# the widget's pressure colour bands then react to deviation from there.
_AC1_IDEAL_PSI = 27.0
# Wheel-slip threshold above which we treat the wheel as locked under
# braking. AC1 doesn't publish a per-wheel lock flag, so we infer.
_LOCK_SLIP_THRESHOLD = 0.40
# ABS-active heuristic: brake input + per-wheel slip above this small
# threshold while not fully locked.
_ABS_SLIP_THRESHOLD = 0.10


class AcSharedMemoryReader:
    """Opens the three AC1 shared-memory blocks."""

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


class AcTelemetrySource(TelemetrySource):
    """Polls AC1 shared memory and emits :class:`TelemetryFrame`."""

    def __init__(self, hz: int = 60, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._reader = AcSharedMemoryReader()
        self._frame = TelemetryFrame()
        # Default curves for the *_norm fields AC1 doesn't publish — used
        # until we successfully load per-compound curves from the ACD.
        self._default_tire_curve = Curve(DEFAULT_TIRE_TEMP_CURVE)
        self._brake_curve = Curve(DEFAULT_BRAKE_TEMP_CURVE)
        # ACD-derived per-car/per-wheel-axle data. ``_acd`` is set on
        # connect; ``_torque_lut`` is the raw rpm->Nm table the .lut
        # ships and powers both current_bhp and current_torque. Per-wheel
        # tyre curves + ideal pressures re-load whenever the compound
        # changes (graphics block re-publishes tyreCompound mid-stint).
        self._acd: ACD | None = None
        self._torque_lut: Curve | None = None
        self._tire_curves: dict[str, Curve] = {}
        self._ideal_pressure_psi: dict[str, float] = {}
        self._loaded_compound: str = ""
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
            print("[ac1] connected to shared memory")
            return True
        except (OSError, RuntimeError) as exc:
            if not isinstance(exc, FileNotFoundError):
                print(f"[ac1] connect failed: {exc}", file=sys.stderr)
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
            print(f"[ac1] read failed, dropping connection: {exc}", file=sys.stderr)
            self._reader.close()
            return

        self._apply_graphics(graphics)
        self._apply_physics(phys)
        self.frame.emit(self._frame)

    def _apply_static(self, st: _SPageFileStatic) -> None:
        """Seed the per-car spec values that AC1 publishes once at session
        load. AC Evo dropped most of these — on AC1 they're authoritative
        so we wire them in directly instead of rolling-max calibrating."""
        e = self._frame.engine
        if st.maxRpm > 0:
            e.max_rpm = float(st.maxRpm)
        if st.maxPower > 0:
            e.max_power = float(st.maxPower)
        if st.maxTorque > 0:
            e.max_torque = float(st.maxTorque)
        if st.maxTurboBoost > 0:
            e.max_turbo_boost = float(st.maxTurboBoost)

        # Per-wheel suspension max travel from the static block — no
        # rolling-max needed (AC Evo had to do that because the field
        # disappeared in the newer game).
        for wid in WHEEL_IDS:
            idx = _WHEEL_INDEX[wid]
            w = self._frame.wheels[wid]
            sm = float(st.suspensionMaxTravel[idx])
            if sm > 0.0:
                w.susp_m_t = sm

        # Try to load the on-disk ACD for this car — gives us the real
        # torque curve, per-compound thermal performance, and per-axle
        # ideal pressures, none of which AC1 publishes via shared memory.
        self._load_acd(getattr(st, "carModel", ""))
        self._refresh_engine_curves()

    def _load_acd(self, car_model: str) -> None:
        """Locate and parse ``content/cars/<car_model>/data.acd``.

        Silent on missing AC install / missing car directory / mod cars
        without ACD: the source falls back to its prior synth-curve
        behaviour. We log once on success so the user can confirm the
        ACD path was found.
        """
        self._acd = None
        car_model = (car_model or "").strip()
        if not car_model:
            return
        car_dir = find_car_dir(car_model)
        if car_dir is None:
            return
        try:
            self._acd = ACD(car_dir)
            print(f"[ac1] loaded ACD for {car_model}")
        except (OSError, ValueError) as exc:
            print(f"[ac1] ACD load failed for {car_model}: {exc}", file=sys.stderr)
            self._acd = None

    def _refresh_engine_curves(self) -> None:
        """Parse engine.ini's POWER_CURVE .lut into a torque LUT we can
        interpolate per-frame. The same LUT feeds both current_torque
        (Nm direct) and current_bhp (Nm × rpm / 5252)."""
        self._torque_lut = None
        if self._acd is None:
            return
        torque_pts = self._acd.get_power_curve()
        if not torque_pts:
            return
        self._torque_lut = Curve(torque_pts)

    def _refresh_compound_curves(self, compound: str) -> None:
        """Reload per-axle thermal-performance + ideal-pressure data when
        the compound changes (or when we first see one). AC1's graphics
        block re-publishes ``tyreCompound`` every frame, so we no-op when
        the compound hasn't actually changed."""
        if compound == self._loaded_compound:
            return
        self._loaded_compound = compound
        self._tire_curves = {}
        self._ideal_pressure_psi = {}
        if self._acd is None or not compound:
            return
        for wid in WHEEL_IDS:
            temp_pts = self._acd.get_temp_curve(compound, wid)
            if temp_pts:
                self._tire_curves[wid] = Curve(temp_pts)
            ideal = self._acd.get_ideal_pressure(compound, wid)
            if ideal is not None and ideal > 0.0:
                self._ideal_pressure_psi[wid] = ideal

    def _apply_physics(self, ph: _SPageFilePhysics) -> None:
        e = self._frame.engine
        e.rpm = float(ph.rpms)
        e.turbo_boost = float(ph.turboBoost)
        e.max_turbo_boost = max(e.max_turbo_boost, e.turbo_boost)
        e.gear = int(ph.gear)
        e.speed_kmh = float(ph.speedKmh)

        # Live BHP / torque from the ACD's torque LUT when available.
        # AC1's shared memory doesn't publish either — the engine widget
        # would otherwise sit on a synthetic curve fitted from the
        # static peaks. With the .lut we interpolate the real torque at
        # the current RPM and convert to HP via the canonical 5252.
        if self._torque_lut is not None and e.rpm > 0.0:
            torque_nm = max(0.0, self._torque_lut.interpolate(e.rpm))
            e.current_torque = torque_nm
            e.current_bhp = torque_nm * e.rpm / 5252.0
        e.tc_level = float(ph.tc)
        e.abs_level = float(ph.abs)
        e.pit_limiter = bool(ph.pitLimiterOn)
        e.brake_bias = float(ph.brakeBias)
        # AC1 publishes drs as a 0..1 deploy value; treat > 0.5 as active.
        e.drs_available = bool(ph.drsAvailable)
        e.drs_enabled = bool(ph.drsEnabled) or float(ph.drs) > 0.5
        e.ers_charging = bool(ph.ersIsCharging)
        # AC1 doesn't expose tcInAction / absInAction — keep them off so
        # the chip strip doesn't flash arbitrarily.
        e.tc_in_action = False
        e.abs_in_action = False
        # The fuel reading still lives on the physics block in AC1.
        e.fuel_liters = float(ph.fuel)

        # Inputs / dynamics.
        i = self._frame.inputs
        i.throttle = max(0.0, min(1.0, float(ph.gas)))
        i.brake = max(0.0, min(1.0, float(ph.brake)))
        i.clutch = max(0.0, min(1.0, float(ph.clutch)))
        i.steering = max(-1.0, min(1.0, float(ph.steerAngle) / (math.pi / 4)))
        i.steering_deg = math.degrees(float(ph.steerAngle))
        i.ffb = max(0.0, min(1.0, abs(float(ph.finalFF))))
        # accG layout is [lateral X, longitudinal Y, vertical Z] —
        # matches AC Evo's official PDF, which we verified live (the
        # earlier claim that AC1 used [lat, vert, long] was wrong).
        i.g_lat = float(ph.accG[0])
        i.g_long = float(ph.accG[1])
        i.g_vert = float(ph.accG[2])
        i.damage = tuple(float(ph.carDamage[k]) for k in range(5))
        i.tyres_out = int(ph.numberOfTyresOut)

        braking = ph.brake > 0.0
        moving = ph.speedKmh > 5.0

        for wid in WHEEL_IDS:
            self._apply_wheel_physics(ph, wid, braking, moving)

    def _apply_wheel_physics(self, ph: _SPageFilePhysics, wid: str,
                              braking: bool, moving: bool) -> None:
        """Per-wheel slice of :meth:`_apply_physics`. Extracted to keep
        the parent function under pylint's statement-count budget; the
        body itself reads as one wheel's worth of work."""
        idx = _WHEEL_INDEX[wid]
        w = self._frame.wheels[wid]

        slip = abs(float(ph.wheelSlip[idx]))
        # AC1 doesn't publish a lock flag — use a slip-magnitude
        # threshold under braking instead. Same idea for abs_active.
        w.lock = bool(braking and moving and slip > _LOCK_SLIP_THRESHOLD)
        w.abs_active = bool(braking and float(ph.abs) > 0.0
                            and not w.lock and slip > _ABS_SLIP_THRESHOLD)

        w.camber = float(ph.camberRAD[idx])
        w.susp_t = abs(float(ph.suspensionTravel[idx]))
        # If static didn't supply a max (mods sometimes leave it 0),
        # fall back to a rolling-max with 5 % headroom — same trick
        # the AC Evo source uses. The outer guard ensures we never
        # divide-or-multiply by a zero reading.
        if w.susp_t > 0.0:
            if w.susp_m_t <= 0.0:
                w.susp_m_t = w.susp_t * 2.0
            elif w.susp_m_t < w.susp_t * 1.05:
                w.susp_m_t = w.susp_t * 1.05

        axle = idx // 2
        raw = float(ph.rideHeight[axle])
        w.height = raw if abs(raw) >= 1.0 else raw * 1000.0

        w.tire_d = float(ph.tyreDirtyLevel[idx]) * 4.0
        w.tire_l = float(ph.wheelLoad[idx])
        w.tire_p = float(ph.wheelsPressure[idx])
        # tire_p_norm: use the real PRESSURE_IDEAL from the ACD's
        # tyres.ini when we have it (per-axle, per-compound), else
        # fall back to a fixed-ideal 27 psi approximation. The
        # ACD-backed norm is accurate enough for the contact-patch
        # heuristic, so flip has_pressure_norm True in that case.
        ideal_psi = self._ideal_pressure_psi.get(wid, 0.0)
        if ideal_psi > 0.0:
            w.tire_p_norm = w.tire_p / ideal_psi
            w.has_pressure_norm = True
        else:
            w.tire_p_norm = w.tire_p / _AC1_IDEAL_PSI if _AC1_IDEAL_PSI > 0 else 1.0
            w.has_pressure_norm = False

        w.tire_t_c = float(ph.tyreCoreTemperature[idx])
        w.tire_t_i = float(ph.tyreTempI[idx])
        w.tire_t_m = float(ph.tyreTempM[idx])
        w.tire_t_o = float(ph.tyreTempO[idx])
        # Per-compound thermal performance curve from the ACD when
        # available; otherwise the synthetic-source default. The
        # ACD curve is temperature -> grip fraction, the same shape
        # tire_t_norm_* expects.
        tire_curve = self._tire_curves.get(wid, self._default_tire_curve)
        w.tire_t_norm_c = tire_curve.interpolate(w.tire_t_c)
        w.tire_t_norm_i = tire_curve.interpolate(w.tire_t_i)
        w.tire_t_norm_m = tire_curve.interpolate(w.tire_t_m)
        w.tire_t_norm_o = tire_curve.interpolate(w.tire_t_o)

        # AC1's brakeTemp slot is never written by the game — it sits
        # at the initial ambient (~12 °C) all session. Mark the signal
        # unavailable so the widget hides the temperature label and
        # the icon entirely instead of misleading.
        w.brake_t = float(ph.brakeTemp[idx])
        w.brake_t_norm = self._brake_curve.interpolate(w.brake_t)
        w.has_brake_temp = False

        # AC1's tyreWear is % remaining (100 fresh, 0 bald). Convert
        # to the overlay's "remaining grip" convention (1.0 fresh).
        wear_pct = float(ph.tyreWear[idx])
        w.tire_w = max(0.0, min(1.0, wear_pct / 100.0))
        # AC1's SDK predates padLife / discLife — the fields aren't in
        # the physics struct at all. Hide the brake-wear bars instead
        # of rendering stuck-fresh values that would mislead the user.
        w.has_pad_wear = False
        w.has_disc_wear = False

    def _apply_graphics(self, gr: _SPageFileGraphic) -> None:
        """Pull the few graphics-block fields the overlay uses on AC1.

        AC1's graphics block is much smaller than AC Evo's: no live
        engine power output, no per-aid state, no tyre-state substruct,
        no max_fuel. Most of what we need lives on physics + static.
        """
        # Tyre compound: AC1 publishes a single string for all four
        # wheels (no per-axle split like AC Evo).
        compound = (gr.tyreCompound or "").strip()
        for wid in WHEEL_IDS:
            self._frame.wheels[wid].compound = compound
        # Compound can change mid-stint (pit stops on track-day cars,
        # tyre swaps in practice). Reload per-wheel ACD curves whenever
        # the string changes; no-op when it hasn't.
        self._refresh_compound_curves(compound)
