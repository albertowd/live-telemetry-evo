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
  synthetic source uses and assuming an ideal cold pressure of 26 psi.
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
# pressures hover around 26 psi cold, so this is a reasonable default —
# the widget's pressure colour bands then react to deviation from there.
_AC1_IDEAL_PSI = 26.0
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
        # Curve fallbacks for the *_norm fields AC1 doesn't publish.
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

    def _apply_physics(self, ph: _SPageFilePhysics) -> None:
        e = self._frame.engine
        e.rpm = float(ph.rpms)
        e.turbo_boost = float(ph.turboBoost)
        e.max_turbo_boost = max(e.max_turbo_boost, e.turbo_boost)
        e.gear = int(ph.gear)
        e.speed_kmh = float(ph.speedKmh)
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
        # AC1 documents accG with the same [lat, vert, long] ordering AC
        # Evo confirmed — match the AC Evo apply step.
        i.g_lat = float(ph.accG[0])
        i.g_vert = float(ph.accG[1])
        i.g_long = float(ph.accG[2])
        i.damage = tuple(float(ph.carDamage[k]) for k in range(5))
        i.tyres_out = int(ph.numberOfTyresOut)

        braking = ph.brake > 0.0
        moving = ph.speedKmh > 5.0

        for wid in WHEEL_IDS:
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
            # the AC Evo source uses.
            if w.susp_m_t <= 0.0 and w.susp_t > 0.0:
                w.susp_m_t = w.susp_t * 2.0
            elif w.susp_m_t > 0.0 and w.susp_t * 1.05 > w.susp_m_t:
                w.susp_m_t = w.susp_t * 1.05

            axle = idx // 2
            raw = float(ph.rideHeight[axle])
            w.height = raw if abs(raw) >= 1.0 else raw * 1000.0

            w.tire_d = float(ph.tyreDirtyLevel[idx]) * 4.0
            w.tire_l = float(ph.wheelLoad[idx])
            w.tire_p = float(ph.wheelsPressure[idx])
            # AC1 doesn't publish a normalised pressure; synthesise one
            # against a fixed-ideal 26 psi reference so the pressure
            # widget's colour bands still react sensibly. Inaccurate
            # against the per-car / per-compound real ideal, but better
            # than pinning to 1.0 = "always green".
            w.tire_p_norm = w.tire_p / _AC1_IDEAL_PSI if _AC1_IDEAL_PSI > 0 else 1.0
            # The synth above is a rough psi/26 approximation: fine for
            # the pressure icon's colour but too inaccurate for the
            # contact-patch heuristic (a 30 % deviation collapses a
            # whole zone). Flag the norm as unreliable so the bars
            # ignore pressure and render off camber + load only.
            w.has_pressure_norm = False

            w.tire_t_c = float(ph.tyreCoreTemperature[idx])
            w.tire_t_i = float(ph.tyreTempI[idx])
            w.tire_t_m = float(ph.tyreTempM[idx])
            w.tire_t_o = float(ph.tyreTempO[idx])
            # No per-compound normalised temps on AC1 — interpolate the
            # default tyre-temp curve so the widget's blue/green/red
            # bands still drive off something meaningful.
            w.tire_t_norm_c = self._tire_curve.interpolate(w.tire_t_c)
            w.tire_t_norm_i = self._tire_curve.interpolate(w.tire_t_i)
            w.tire_t_norm_m = self._tire_curve.interpolate(w.tire_t_m)
            w.tire_t_norm_o = self._tire_curve.interpolate(w.tire_t_o)

            # AC1's brakeTemp slot is never written by the game — it sits
            # at the initial ambient (~12 °C) all session. Mark the signal
            # unavailable so the widget hides the temperature label and
            # falls back to a neutral icon tint instead of misleading.
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
