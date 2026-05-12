"""Assetto Corsa Competizione shared-memory telemetry source.

ACC publishes three named file-mappings under the same Windows tags AC1
uses (``acpmf_physics`` / ``acpmf_graphics`` / ``acpmf_static``) — only
one of AC1 / ACC can be running at a time anyway, and the user picks
which struct layout to apply via ``--source acc`` vs ``--source ac1``.

Layout source: Kunos's *ACC Shared Memory Documentation v1.8.12* PDF
(bundled at the repo root). The relationship between the three games:

* **Physics** — ACC's physics block is byte-for-byte the AC Evo 800-byte
  layout. A handful of field names differ (``brakePressure`` vs Evo's
  ``brakeTorque`` at offset 716; ``gVibrations`` vs Evo's
  ``roadVibrations`` at 792); the binary content is the same. We define
  a fresh struct here using ACC's nomenclature so the file reads cleanly
  against the PDF.
* **Graphics** — ACC's graphics block is closer to AC1 (``wchar_t``
  strings for lap times) but adds rain forecasting, MFD pressures, the
  60-car coordinate + ID table, electronics levels (TC/ABS/EngineMap as
  ints), delta/estimated times, global flag state, etc. Different from
  AC Evo's entirely.
* **Static** — AC1-shape with three ACC additions
  (``isOnline``, ``dryTyresName``, ``wetTyresName``).

A few quirks worth noting upstream of the apply step:

* ``tyreTempI/M/O`` are present in the struct but not populated by ACC
  (the PDF marks them ``Not shown in ACC``). We fall back to the core
  temp for the per-face values so the IMO temp grid still has *something*
  to render instead of three permanent-blue cells.
* ``tyreWear`` is in the struct but ACC never writes a meaningful value
  to it (the slot stays at its default). We flip ``has_tire_wear`` False
  so the wear bar hides instead of pinning a stuck-fresh value.
* ``brakeBias`` has a per-car offset that the dash adds before display
  (see Appendix 4 in the PDF). We use the raw value as-is — close enough
  for a 0..1 widget, off by a couple of percent against the in-car
  HUD readout.
* Several physics fields are present in the struct but **ACC never
  populates them** (the PDF colour-codes them as unused; the colour
  is lost in text extraction). Confirmed empirically as flat zero on
  a running game: ``camberRAD``, ``rideHeight``, ``wheelLoad``, the
  per-face ``tyreTempI/M/O``. For these we set sensible fallbacks:
  ride-height defaults to a plausible value so the "below 20 mm"
  warning doesn't latch red; camber stays at 0 (the tire silhouette
  renders upright, contact bars react only to pressure); wheelLoad
  stays at 0 (load circle and contact-bar heights collapse to their
  floor, which reads as "we don't have this data" rather than fake
  data). The PDF mis-types ``currentMaxRpm`` as float — it's actually
  int32 like AC Evo; mistyped reads gave a denormal ~1e-41 that
  pegged the RPM bar at 100 %.
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


# ACC reuses AC1's tag names — the two games never run at once.
PHYSICS_TAG = "Local\\acpmf_physics"
GRAPHICS_TAG = "Local\\acpmf_graphics"
STATIC_TAG = "Local\\acpmf_static"

# Map sizes — ACC's structs are slightly larger than AC1's. We map
# generously and let ctypes consume only what it needs.
PHYSICS_SIZE = 2048
GRAPHICS_SIZE = 4096
STATIC_SIZE = 2048


_WHEEL_INDEX = {"FL": 0, "FR": 1, "RL": 2, "RR": 3}


class _SPageFilePhysics(ctypes.Structure):
    """ACC SPageFilePhysics — same 800-byte layout as AC Evo's physics
    block, ACC's field names per the v1.8.12 PDF."""

    _pack_ = 4
    _fields_ = [
        # AC1-compatible prefix (offsets 0–415).
        ("packetId", c_int32),
        ("gas", c_float),
        ("brake", c_float),
        ("fuel", c_float),
        ("gear", c_int32),
        ("rpm", c_int32),
        ("steerAngle", c_float),
        ("speedKmh", c_float),
        ("velocity", c_float * 3),
        ("accG", c_float * 3),
        ("wheelSlip", c_float * 4),
        ("wheelLoad", c_float * 4),
        ("wheelPressure", c_float * 4),
        ("wheelAngularSpeed", c_float * 4),
        ("tyreWear", c_float * 4),
        ("tyreDirtyLevel", c_float * 4),
        ("tyreCoreTemp", c_float * 4),
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
        ("autoshifterOn", c_int32),
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
        # tyreTempI/M/O exist but ACC never writes them — see apply step.
        ("tyreTempI", c_float * 4),
        ("tyreTempM", c_float * 4),
        ("tyreTempO", c_float * 4),
        # ACC additions (offsets 416–799).
        ("isAIControlled", c_int32),
        ("tyreContactPoint", (c_float * 3) * 4),
        ("tyreContactNormal", (c_float * 3) * 4),
        ("tyreContactHeading", (c_float * 3) * 4),
        ("brakeBias", c_float),
        ("localVelocity", c_float * 3),
        ("P2PActivation", c_int32),
        ("P2PStatus", c_int32),
        # The v1.8.12 PDF declares this as float, but ACC actually writes
        # an int32 here (same binary layout as AC Evo). Reading as float
        # would interpret 7500 (0x00001D4C) as a denormal ~1e-41 and
        # peg the RPM bar at 100 % every tick.
        ("currentMaxRpm", c_int32),
        ("mz", c_float * 4),
        ("fx", c_float * 4),
        ("fy", c_float * 4),
        ("slipRatio", c_float * 4),
        ("slipAngle", c_float * 4),
        ("tcInAction", c_int32),
        ("absInAction", c_int32),
        ("suspensionDamage", c_float * 4),
        ("tyreTemp", c_float * 4),         # core temp duplicate
        ("waterTemp", c_float),
        ("brakePressure", c_float * 4),    # AC Evo names this brakeTorque
        ("frontBrakeCompound", c_int32),
        ("rearBrakeCompound", c_int32),
        ("padLife", c_float * 4),
        ("discLife", c_float * 4),
        ("ignitionOn", c_int32),
        ("starterEngineOn", c_int32),
        ("isEngineRunning", c_int32),
        ("kerbVibration", c_float),
        ("slipVibrations", c_float),
        ("gVibrations", c_float),
        ("absVibrations", c_float),
    ]


class _SPageFileGraphic(ctypes.Structure):
    """ACC SPageFileGraphic — different from both AC1 and AC Evo. Per
    the v1.8.12 PDF. wchar_t strings for lap times, 60-car coord +
    ID table, MFD pressures, rain/grip enums."""

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
        ("activeCars", c_int32),
        ("carCoordinates", (c_float * 3) * 60),
        ("carID", c_int32 * 60),
        ("playerCarID", c_int32),
        ("penaltyTime", c_float),
        ("flag", c_int32),
        ("penalty", c_int32),                  # ACC_PENALTY_TYPE
        ("idealLineOn", c_int32),
        ("isInPitLane", c_int32),
        ("surfaceGrip", c_float),
        ("mandatoryPitDone", c_int32),
        ("windSpeed", c_float),
        ("windDirection", c_float),
        ("isSetupMenuVisible", c_int32),
        ("mainDisplayIndex", c_int32),
        ("secondaryDisplayIndex", c_int32),
        ("TC", c_int32),
        ("TCCUT", c_int32),
        ("EngineMap", c_int32),
        ("ABS", c_int32),
        ("fuelXLap", c_float),
        ("rainLights", c_int32),
        ("flashingLights", c_int32),
        ("lightsStage", c_int32),
        ("exhaustTemperature", c_float),
        ("wiperLV", c_int32),
        ("driverStintTotalTimeLeft", c_int32),
        ("driverStintTimeLeft", c_int32),
        ("rainTyres", c_int32),
        ("sessionIndex", c_int32),
        ("usedFuel", c_float),
        ("deltaLapTime", c_wchar * 15),
        ("iDeltaLapTime", c_int32),
        ("estimatedLapTime", c_wchar * 15),
        ("iEstimatedLapTime", c_int32),
        ("isDeltaPositive", c_int32),
        ("iSplit", c_int32),
        ("isValidLap", c_int32),
        ("fuelEstimatedLaps", c_float),
        ("trackStatus", c_wchar * 33),
        ("missingMandatoryPits", c_int32),
        ("Clock", c_float),
        ("directionLightsLeft", c_int32),
        ("directionLightsRight", c_int32),
        ("GlobalYellow", c_int32),
        ("GlobalYellow1", c_int32),
        ("GlobalYellow2", c_int32),
        ("GlobalYellow3", c_int32),
        ("GlobalWhite", c_int32),
        ("GlobalGreen", c_int32),
        ("GlobalChequered", c_int32),
        ("GlobalRed", c_int32),
        ("mfdTyreSet", c_int32),
        ("mfdFuelToAdd", c_float),
        ("mfdTyrePressureLF", c_float),
        ("mfdTyrePressureRF", c_float),
        ("mfdTyrePressureLR", c_float),
        ("mfdTyrePressureRR", c_float),
        ("trackGripStatus", c_int32),
        ("rainIntensity", c_int32),
        ("rainIntensityIn10min", c_int32),
        ("rainIntensityIn30min", c_int32),
        ("currentTyreSet", c_int32),
        ("strategyTyreSet", c_int32),
        ("gapAhead", c_int32),
        ("gapBehind", c_int32),
    ]


class _SPageFileStatic(ctypes.Structure):
    """ACC SPageFileStatic — AC1-shape plus isOnline + dry/wet tyre
    names. Carries the car spec sheet (``maxRpm``, ``maxFuel``,
    ``maxTurboBoost``) that AC Evo dropped from its own static block."""

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
        ("deprecated_1", c_float),
        ("deprecated_2", c_float),
        ("penaltiesEnabled", c_int32),
        ("aidFuelRate", c_float),
        ("aidTireRate", c_float),
        ("aidMechanicalDamage", c_float),
        ("allowTyreBlankets", c_float),
        ("aidStability", c_float),
        ("aidAutoClutch", c_int32),
        ("aidAutoBlip", c_int32),
        ("hasDRS", c_int32),
        ("hasERS", c_int32),
        ("hasKERS", c_int32),
        ("kersMaxJ", c_float),
        ("engineBrakeSettingsCount", c_int32),
        ("ersPowerControllerCount", c_int32),
        ("trackSplineLength", c_float),
        ("trackConfiguration", c_wchar * 33),
        ("ersMaxJ", c_float),
        ("isTimedRace", c_int32),
        ("hasExtraLap", c_int32),
        ("carSkin", c_wchar * 33),
        ("reversedGridPositions", c_int32),
        ("pitWindowStart", c_int32),
        ("pitWindowEnd", c_int32),
        ("isOnline", c_int32),
        ("dryTyresName", c_wchar * 33),
        ("wetTyresName", c_wchar * 33),
    ]


# Same fallback constants the AC1 source uses for fields ACC doesn't
# publish: per-compound normalised values, lock/ABS-active heuristics.
_ACC_IDEAL_PSI = 26.0
_LOCK_SLIP_THRESHOLD = 0.40
_ABS_SLIP_THRESHOLD = 0.10


class AccSharedMemoryReader:
    """Opens the three ACC shared-memory blocks."""

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


class AccTelemetrySource(TelemetrySource):
    """Polls ACC shared memory and emits :class:`TelemetryFrame`."""

    def __init__(self, hz: int = 60, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._reader = AccSharedMemoryReader()
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
            print("[acc] connected to shared memory")
            return True
        except (OSError, RuntimeError) as exc:
            if not isinstance(exc, FileNotFoundError):
                print(f"[acc] connect failed: {exc}", file=sys.stderr)
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
            print(f"[acc] read failed, dropping connection: {exc}", file=sys.stderr)
            self._reader.close()
            return

        self._apply_graphics(graphics)
        self._apply_physics(phys)
        self.frame.emit(self._frame)

    def _apply_static(self, st: _SPageFileStatic) -> None:
        """ACC's static block still carries the car spec, just like AC1."""
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
        # currentMaxRpm replaces AC1's static maxRpm when present. Range-
        # check defensively — anything ≤ 1000 is almost certainly a
        # dead-slot read or a type mismatch we should ignore.
        if ph.currentMaxRpm > 1000:
            e.max_rpm = float(ph.currentMaxRpm)
        e.tc_in_action = ph.tcInAction != 0
        e.abs_in_action = ph.absInAction != 0
        e.drs_available = bool(ph.drsAvailable)
        e.drs_enabled = bool(ph.drsEnabled) or float(ph.drs) > 0.5
        e.ers_charging = bool(ph.ersIsCharging)
        e.fuel_liters = float(ph.fuel)
        e.water_temp_c = float(ph.waterTemp)

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
            # ACC doesn't publish a per-wheel lock flag — same heuristic
            # as the AC1 source. absInAction comes from physics directly
            # (ACC, like AC Evo, exposes it).
            w.lock = bool(braking and ph.speedKmh > 5.0
                          and slip > _LOCK_SLIP_THRESHOLD)
            w.abs_active = bool(ph.absInAction and braking
                                and not w.lock and slip > _ABS_SLIP_THRESHOLD)

            # ACC doesn't populate camberRAD (PDF marks it unused). Stays
            # at 0, which keeps the tire silhouette upright and makes the
            # contact bars react purely to pressure — accurate to what
            # ACC actually exposes.
            w.camber = float(ph.camberRAD[idx])
            w.susp_t = abs(float(ph.suspensionTravel[idx]))
            if w.susp_m_t <= 0.0 and w.susp_t > 0.0:
                w.susp_m_t = w.susp_t * 2.0
            elif w.susp_m_t > 0.0 and w.susp_t * 1.05 > w.susp_m_t:
                w.susp_m_t = w.susp_t * 1.05

            axle = idx // 2
            raw = float(ph.rideHeight[axle])
            w.height = raw if abs(raw) >= 1.0 else raw * 1000.0
            # ACC doesn't write rideHeight, wheelLoad, or camberRAD (PDF
            # marks them unused). Tell the widget to hide the
            # corresponding indicators rather than showing stuck-zero
            # values that look like a bug. has_camber being False also
            # hides the contact-patch bars (which need a camber signal
            # to be informative — see WheelData docstring).
            w.has_ride_height = False
            w.has_wheel_load = False
            w.has_camber = False

            w.tire_d = float(ph.tyreDirtyLevel[idx]) * 4.0
            w.tire_l = float(ph.wheelLoad[idx])
            w.tire_p = float(ph.wheelPressure[idx])
            w.tire_p_norm = w.tire_p / _ACC_IDEAL_PSI if _ACC_IDEAL_PSI > 0 else 1.0

            w.tire_t_c = float(ph.tyreCoreTemp[idx])
            # ACC doesn't populate the per-face I/M/O slots — fall back
            # to the core temp so the IMO temp grid renders a sensible
            # uniform colour instead of three permanent-blue cells.
            face_i = float(ph.tyreTempI[idx])
            face_m = float(ph.tyreTempM[idx])
            face_o = float(ph.tyreTempO[idx])
            w.tire_t_i = face_i if face_i > 0.0 else w.tire_t_c
            w.tire_t_m = face_m if face_m > 0.0 else w.tire_t_c
            w.tire_t_o = face_o if face_o > 0.0 else w.tire_t_c
            w.tire_t_norm_c = self._tire_curve.interpolate(w.tire_t_c)
            w.tire_t_norm_i = self._tire_curve.interpolate(w.tire_t_i)
            w.tire_t_norm_m = self._tire_curve.interpolate(w.tire_t_m)
            w.tire_t_norm_o = self._tire_curve.interpolate(w.tire_t_o)

            w.brake_t = float(ph.brakeTemp[idx])
            w.brake_t_norm = self._brake_curve.interpolate(w.brake_t)

            # Brake-pad / disc life mirror AC Evo's "1.0 = fresh" scale.
            w.pad_w = float(ph.padLife[idx])
            w.disc_w = float(ph.discLife[idx])

            # ACC's ph.tyreWear slot is present in the struct but the
            # game doesn't actually publish wear values — verified in
            # session, the field stays at its default. Hide the bar
            # rather than rendering a stuck-fresh value.
            w.has_tire_wear = False

    def _apply_graphics(self, gr: _SPageFileGraphic) -> None:
        """ACC graphics — pull lap state + electronics + tyre compound."""
        e = self._frame.engine
        # ACC publishes TC / ABS / EngineMap as int *levels* (0..N
        # selector index), not as 0..1 strength like AC1's physics tc/abs.
        # The chip-strip logic just checks "is the value non-zero", so
        # this still drives the chip visibility correctly.
        e.tc_level = float(gr.TC)
        e.abs_level = float(gr.ABS)
        e.exhaust_temp_c = float(gr.exhaustTemperature)
        e.valid_lap = bool(gr.isValidLap)

        compound = (gr.tyreCompound or "").strip()
        for wid in WHEEL_IDS:
            self._frame.wheels[wid].compound = compound
