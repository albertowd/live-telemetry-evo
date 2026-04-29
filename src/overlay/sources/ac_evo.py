"""Assetto Corsa Evo shared-memory telemetry source.

The game publishes three named shared-memory blocks on Windows:

    Local\\acevo_pmf_physics    — updated every physics step (high-rate)
    Local\\acevo_pmf_graphics   — updated each rendered frame (HUD-rate)
    Local\\acevo_pmf_static     — written once per session

This module opens those blocks via :mod:`mmap`, parses them with
:mod:`ctypes` structs, and emits :class:`TelemetryFrame` snapshots that the
overlay widgets already understand.

NOTE ON STRUCT LAYOUT
=====================

AC Evo's full struct layout is only partially public at the time of writing.
The structs below are a best-effort starting point seeded from:

* the original AC1 ``SPageFilePhysics`` / ``SPageFileGraphic`` /
  ``SPageFileStatic`` (taken from the AC plugin SDK), which AC Evo extends;
* the public Steam guide listing the three named-mapping names and the
  embedded sub-struct names + sizes (TyreState 256 B x4, DamageState 128 B,
  …); and
* the ``acevo-shared-memory`` Rust crate, which confirms specific field
  names and types (``speedKmh: f32``, ``rpms: i32``, ``gear: i32``,
  ``fuel_liter_current_quantity: f32``, etc.).

The fields that are confirmed verbatim in the public sources are wired into
:class:`TelemetryFrame`. Anything still uncertain is read defensively (with
range clamps) so a wrong offset produces a visible-but-bounded value rather
than a crash. Once the user runs the dump tool against a live session
(``python -m overlay.sources.dump``) and confirms real offsets, this file is
the only place that needs adjusting.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import (c_bool, c_byte, c_char, c_float, c_int8, c_int16, c_int32,
                    c_uint8, c_uint16, c_uint32, c_uint64, c_wchar)
from typing import Optional

from PySide6.QtCore import QObject, QTimer

from ..telemetry import TelemetryFrame, WHEEL_IDS
from .base import TelemetrySource


# --- Win32 OpenFileMapping bindings -----------------------------------------
#
# Python's stdlib mmap.mmap(-1, size, tagname=name) will *create* a mapping
# under that name if one doesn't exist, instead of failing. That makes it
# impossible to detect "game not running" — we'd silently attach to a fresh
# empty mapping and read zeros. The Win32 OpenFileMappingW call returns NULL
# when the name is not present, which is what we want.
_FILE_MAP_READ = 0x0004

if sys.platform == "win32":
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _OpenFileMappingW = _KERNEL32.OpenFileMappingW
    _OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int32, ctypes.c_wchar_p]
    _OpenFileMappingW.restype = ctypes.c_void_p

    _MapViewOfFile = _KERNEL32.MapViewOfFile
    _MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                               ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t]
    _MapViewOfFile.restype = ctypes.c_void_p

    _UnmapViewOfFile = _KERNEL32.UnmapViewOfFile
    _UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    _UnmapViewOfFile.restype = ctypes.c_int32

    _CloseHandle = _KERNEL32.CloseHandle
    _CloseHandle.argtypes = [ctypes.c_void_p]
    _CloseHandle.restype = ctypes.c_int32
else:  # pragma: no cover - non-Windows shouldn't import this module
    _KERNEL32 = None


class _NamedMapping:
    """Read-only view of an existing Windows named file-mapping.

    Raises FileNotFoundError when the name does not exist (i.e. the game is
    not running or hasn't loaded yet).
    """

    def __init__(self, name: str, size: int) -> None:
        if sys.platform != "win32":
            raise OSError("AC Evo shared memory is Windows-only")
        handle = _OpenFileMappingW(_FILE_MAP_READ, False, name)
        if not handle:
            err = ctypes.get_last_error()
            # ERROR_FILE_NOT_FOUND == 2: the named mapping doesn't exist.
            if err == 2:
                raise FileNotFoundError(f"named mapping not found: {name}")
            raise OSError(err, ctypes.FormatError(err), name)
        view = _MapViewOfFile(handle, _FILE_MAP_READ, 0, 0, size)
        if not view:
            err = ctypes.get_last_error()
            _CloseHandle(handle)
            raise OSError(err, ctypes.FormatError(err), name)
        self._handle = handle
        self._view = view
        self._size = size

    def read(self) -> bytes:
        return ctypes.string_at(self._view, self._size)

    def close(self) -> None:
        if self._view:
            _UnmapViewOfFile(self._view)
            self._view = None
        if self._handle:
            _CloseHandle(self._handle)
            self._handle = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # pylint: disable=broad-exception-caught
            pass


# Shared-memory tag names. The "Local\" namespace prefix is required on
# Windows; mmap's tagname argument accepts the bare name and prefixes Local\
# automatically, but AC Evo publishes under the explicit prefix and we match
# that to be unambiguous.
PHYSICS_TAG = "Local\\acevo_pmf_physics"
GRAPHICS_TAG = "Local\\acevo_pmf_graphics"
STATIC_TAG = "Local\\acevo_pmf_static"


# Generous upper bounds for each shared-memory segment. The actual structs
# are smaller; mapping with a slightly oversized region is harmless on
# Windows and means we don't have to know the exact size up front.
PHYSICS_SIZE = 4096
GRAPHICS_SIZE = 8192
STATIC_SIZE = 2048


class _SPageFilePhysics(ctypes.Structure):
    """AC Evo physics layout, transcribed from the official shared-memory
    documentation (Steam guide #3707421508).

    Layout matches the AC1 prefix through ``tyreTempO`` (offset 416), then
    extends with new fields: per-wheel contact geometry, brake bias, tyre
    forces/slip-ratio, in-action driver-aid flags, brake pad/disc life, and
    engine/vibration state. Total documented size is 800 bytes.

    Notable semantic change vs AC1: ``tyreWear`` is **0.0 = new, 1.0 =
    fully worn** — opposite of AC1's "0..100 % remaining". Callers must
    invert.
    """

    _pack_ = 4
    _fields_ = [
        # AC1-compatible prefix (offsets 0..416).
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
        ("tyreWear", c_float * 4),
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
        # AC Evo additions (offset 416..800).
        ("isAIControlled", c_int32),
        ("tyreContactPoint", (c_float * 3) * 4),   # [FL,FR,RL,RR][X,Y,Z]
        ("tyreContactNormal", (c_float * 3) * 4),  # road-normal vector per wheel
        ("tyreContactHeading", (c_float * 3) * 4), # tyre heading vector per wheel
        ("brakeBias", c_float),                    # 0.56 = 56 % front
        ("localVelocity", c_float * 3),
        ("P2PActivations", c_int32),
        ("P2PStatus", c_int32),
        ("currentMaxRpm", c_int32),
        ("mz", c_float * 4),                       # self-aligning torque, Nm
        ("fx", c_float * 4),                       # longitudinal tyre force, N
        ("fy", c_float * 4),                       # lateral tyre force, N
        ("slipRatio", c_float * 4),
        ("slipAngle", c_float * 4),                # radians
        ("tcInAction", c_int32),                   # TC currently cutting
        ("absInAction", c_int32),                  # ABS currently modulating
        ("suspensionDamage", c_float * 4),         # 0..1 per corner
        ("tyreTemp", c_float * 4),                 # representative surface temp
        ("waterTemp", c_float),                    # coolant, C
        ("brakeTorque", c_float * 4),              # Nm per wheel
        ("frontBrakeCompound", c_int32),
        ("rearBrakeCompound", c_int32),
        ("padLife", c_float * 4),                  # 0..1 per corner
        ("discLife", c_float * 4),                 # 0..1 per corner
        ("ignitionOn", c_int32),
        ("starterEngineOn", c_int32),
        ("isEngineRunning", c_int32),
        ("kerbVibration", c_float),
        ("slipVibrations", c_float),
        ("roadVibrations", c_float),
        ("absVibrations", c_float),
    ]


class _SPageFileStatic(ctypes.Structure):
    """AC Evo SPageFileStaticEvo — session/track metadata, written once at
    session load.

    Note vs AC1: the static block is no longer car-focused. The AC1 fields
    ``maxRpm`` / ``maxPower`` / ``maxTorque`` / ``suspensionMaxTravel`` /
    ``maxTurboBoost`` are gone. Live engine values now arrive via the
    graphics block (``current_bhp``, ``rpm_percent``, ``max_turbo_boost``,
    ``max_fuel``); per-wheel suspension max travel is calibrated by
    rolling max in physics.
    """

    _pack_ = 4
    _fields_ = [
        ("sm_version", c_char * 15),
        ("ac_evo_version", c_char * 15),
        ("session", c_int32),                       # ACEVO_SESSION_TYPE enum
        ("session_name", c_char * 33),
        ("event_id", c_uint8),
        ("session_id", c_uint8),
        ("starting_grip", c_int32),                 # ACEVO_STARTING_GRIP enum
        ("starting_ambient_temperature_c", c_float),
        ("starting_ground_temperature_c", c_float),
        ("is_static_weather", c_bool),
        ("is_timed_race", c_bool),
        ("is_online", c_bool),
        ("number_of_sessions", c_int32),
        ("nation", c_char * 33),
        ("longitude", c_float),
        ("latitude", c_float),
        ("track", c_char * 33),
        ("track_configuration", c_char * 33),
        ("track_length_m", c_float),
    ]


# Order of the wheel arrays in AC1 / AC Evo: 0=FL, 1=FR, 2=RL, 3=RR.
_WHEEL_INDEX = {"FL": 0, "FR": 1, "RL": 2, "RR": 3}


# --- AC Evo graphics-block substructs ---------------------------------------
#
# AC Evo's SPageFileGraphicEvo embeds several fixed-size substructs we don't
# need to fully decode yet (Damage, Pit, Electronics, Instrumentation, etc.).
# We model them as opaque byte blobs of the documented size so the offsets of
# the fields we *do* care about (current_bhp, rpm_percent, max_turbo_boost,
# tyre states…) line up correctly. Full substruct definitions can land later
# in their own classes without touching the outer struct's layout.
_SMEVO_DAMAGE_STATE_SIZE = 128
_SMEVO_PITINFO_SIZE = 64
_SMEVO_ELECTRONICS_SIZE = 128
_SMEVO_INSTRUMENTATION_SIZE = 128
_SMEVO_SESSION_STATE_SIZE = 256
_SMEVO_TIMING_STATE_SIZE = 256
_SMEVO_ASSISTS_STATE_SIZE = 64
_SMEVO_TYRE_STATE_SIZE = 256


class _SMEvoTyreState(ctypes.Structure):
    """Per-corner tyre snapshot (256 B). Only the documented prefix is
    decoded; the rest is reserved padding for future expansion."""

    _pack_ = 4
    _fields_ = [
        ("slip", c_float),
        ("lock", c_bool),
        ("tyre_pressure", c_float),
        ("tyre_temperature_c", c_float),
        ("brake_temperature_c", c_float),
        ("brake_pressure", c_float),
        ("tyre_temperature_left", c_float),
        ("tyre_temperature_center", c_float),
        ("tyre_temperature_right", c_float),
        ("tyre_compound_front", c_char * 33),
        ("tyre_compound_rear", c_char * 33),
        ("tyre_normalized_pressure", c_float),
        ("tyre_normalized_temperature_left", c_float),
        ("tyre_normalized_temperature_center", c_float),
        ("tyre_normalized_temperature_right", c_float),
        ("brake_normalized_temperature", c_float),
        ("tyre_normalized_temperature_core", c_float),
    ]


# Compute padding so the tyre struct hits exactly 256 B regardless of
# alignment quirks introduced by the bool / char[33] fields.
_TYRE_DOCUMENTED_BYTES = ctypes.sizeof(_SMEvoTyreState)
assert _TYRE_DOCUMENTED_BYTES <= _SMEVO_TYRE_STATE_SIZE, _TYRE_DOCUMENTED_BYTES


class _SMEvoTyreStatePadded(ctypes.Structure):
    """256-byte padded wrapper around the decoded tyre state, so the outer
    graphics struct's offsets are exactly what the docs prescribe."""

    _pack_ = 4
    _fields_ = [
        ("data", _SMEvoTyreState),
        ("_reserved", c_byte * (_SMEVO_TYRE_STATE_SIZE - _TYRE_DOCUMENTED_BYTES)),
    ]


class _SPageFileGraphic(ctypes.Structure):
    """AC Evo SPageFileGraphicEvo — HUD/graphics data, transcribed from the
    official shared-memory documentation (Steam guide #3707421508).

    Only the high-value fields the overlay needs are wired in; the large
    embedded substructs (damage, pit, electronics, instrumentation, session,
    timing, assists) are kept as opaque byte blobs so the outer offsets
    match the documented layout. Full decoding can land later without
    breaking anything that already works.
    """

    _pack_ = 4
    _fields_ = [
        ("packetId", c_int32),
        ("status", c_int32),                # ACEVO_STATUS enum
        ("focused_car_id_a", c_uint64),
        ("focused_car_id_b", c_uint64),
        ("player_car_id_a", c_uint64),
        ("player_car_id_b", c_uint64),
        ("rpm", c_uint16),
        ("is_rpm_limiter_on", c_bool),
        ("is_change_up_rpm", c_bool),
        ("is_change_down_rpm", c_bool),
        ("tc_active", c_bool),
        ("abs_active", c_bool),
        ("esc_active", c_bool),
        ("launch_active", c_bool),
        ("is_ignition_on", c_bool),
        ("is_engine_running", c_bool),
        ("kers_is_charging", c_bool),
        ("is_wrong_way", c_bool),
        ("is_drs_available", c_bool),
        ("battery_is_charging", c_bool),
        ("is_max_kj_per_lap_reached", c_bool),
        ("is_max_charge_kj_per_lap_reached", c_bool),
        ("display_speed_kmh", c_int16),
        ("display_speed_mph", c_int16),
        ("display_speed_ms", c_int16),
        ("pitspeeding_delta", c_float),
        ("gear_int", c_int16),
        ("rpm_percent", c_float),
        ("gas_percent", c_float),
        ("brake_percent", c_float),
        ("handbrake_percent", c_float),
        ("clutch_percent", c_float),
        ("steering_percent", c_float),
        ("ffb_strength", c_float),
        ("car_ffb_multiplier", c_float),
        ("water_temperature_percent", c_float),
        ("water_pressure_bar", c_float),
        ("fuel_pressure_bar", c_float),
        ("water_temperature_c", c_int8),
        ("air_temperature_c", c_int8),
        ("oil_temperature_c", c_float),
        ("oil_pressure_bar", c_float),
        ("exhaust_temperature_c", c_float),
        ("g_forces_x", c_float),
        ("g_forces_y", c_float),
        ("g_forces_z", c_float),
        ("turbo_boost", c_float),
        ("turbo_boost_level", c_float),
        ("turbo_boost_perc", c_float),
        ("steer_degrees", c_int32),
        ("current_km", c_float),
        ("total_km", c_uint32),
        ("total_driving_time_s", c_uint32),
        ("time_of_day_hours", c_int32),
        ("time_of_day_minutes", c_int32),
        ("time_of_day_seconds", c_int32),
        ("delta_time_ms", c_int32),
        ("current_lap_time_ms", c_int32),
        ("predicted_lap_time_ms", c_int32),
        ("fuel_liter_current_quantity", c_float),
        ("fuel_liter_current_quantity_percent", c_float),
        ("fuel_liter_per_km", c_float),
        ("km_per_fuel_liter", c_float),
        ("current_torque", c_float),                  # Nm, live
        ("current_bhp", c_int32),                     # BHP, live
        ("tyre_lf", _SMEvoTyreStatePadded),
        ("tyre_rf", _SMEvoTyreStatePadded),
        ("tyre_lr", _SMEvoTyreStatePadded),
        ("tyre_rr", _SMEvoTyreStatePadded),
        ("npos", c_float),
        ("kers_charge_perc", c_float),
        ("kers_current_perc", c_float),
        ("control_lock_time", c_float),
        ("car_damage", c_byte * _SMEVO_DAMAGE_STATE_SIZE),
        ("car_location", c_int32),                    # ACEVO_CAR_LOCATION enum
        ("pit_info", c_byte * _SMEVO_PITINFO_SIZE),
        ("fuel_liter_used", c_float),
        ("fuel_liter_per_lap", c_float),
        ("laps_possible_with_fuel", c_float),
        ("battery_temperature", c_float),
        ("battery_voltage", c_float),
        ("instantaneous_fuel_liter_per_km", c_float),
        ("instantaneous_km_per_fuel_liter", c_float),
        ("gear_rpm_window", c_float),
        ("instrumentation", c_byte * _SMEVO_INSTRUMENTATION_SIZE),
        ("instrumentation_min_limit", c_byte * _SMEVO_INSTRUMENTATION_SIZE),
        ("instrumentation_max_limit", c_byte * _SMEVO_INSTRUMENTATION_SIZE),
        ("electronics", c_byte * _SMEVO_ELECTRONICS_SIZE),
        ("electronics_min_limit", c_byte * _SMEVO_ELECTRONICS_SIZE),
        ("electronics_max_limit", c_byte * _SMEVO_ELECTRONICS_SIZE),
        ("electronics_is_modifiable", c_byte * _SMEVO_ELECTRONICS_SIZE),
        ("total_lap_count", c_int32),
        ("current_pos", c_uint32),
        ("total_drivers", c_uint32),
        ("last_laptime_ms", c_int32),
        ("best_laptime_ms", c_int32),
        ("flag", c_int32),                            # ACEVO_FLAG_TYPE enum
        ("global_flag", c_int32),
        ("max_gears", c_uint32),
        ("engine_type", c_int32),                     # ACEVO_ENGINE_TYPE enum
        ("has_kers", c_bool),
        ("is_last_lap", c_bool),
        ("performance_mode_name", c_char * 33),
        ("diff_coast_raw_value", c_float),
        ("diff_power_raw_value", c_float),
        ("race_cut_gained_time_ms", c_int32),
        ("distance_to_deadline", c_int32),
        ("race_cut_current_delta", c_float),
        ("session_state", c_byte * _SMEVO_SESSION_STATE_SIZE),
        ("timing_state", c_byte * _SMEVO_TIMING_STATE_SIZE),
        ("player_ping", c_int32),
        ("player_latency", c_int32),
        ("player_cpu_usage", c_int32),
        ("player_cpu_usage_avg", c_int32),
        ("player_qos", c_int32),
        ("player_qos_avg", c_int32),
        ("player_fps", c_int32),
        ("player_fps_avg", c_int32),
        ("driver_name", c_char * 33),
        ("driver_surname", c_char * 33),
        ("car_model", c_char * 33),
        ("is_in_pit_box", c_bool),
        ("is_in_pit_lane", c_bool),
        ("is_valid_lap", c_bool),
        ("car_coordinates", (c_float * 3) * 60),       # XYZ for up to 60 cars
        ("gap_ahead", c_float),
        ("gap_behind", c_float),
        ("active_cars", c_uint8),
        ("fuel_per_lap", c_float),
        ("fuel_estimated_laps", c_float),
        ("assists_state", c_byte * _SMEVO_ASSISTS_STATE_SIZE),
        ("max_fuel", c_float),
        ("max_turbo_boost", c_float),
        ("use_single_compound", c_bool),
    ]


class AcEvoSharedMemoryReader:
    """Opens the three AC Evo shared-memory segments and exposes typed views.

    Designed to be safe to construct even when the game is not running:
    failures during ``open()`` raise :class:`OSError` and the source treats
    them as "not connected yet" — the overlay then keeps showing the last
    frame (or the synthetic fallback if configured).
    """

    def __init__(self) -> None:
        self._physics_mm: Optional[_NamedMapping] = None
        self._graphics_mm: Optional[_NamedMapping] = None
        self._static_mm: Optional[_NamedMapping] = None

    def open(self) -> None:
        """Attach to the three named shared-memory blocks. Windows-only.

        Raises :class:`FileNotFoundError` when the game is not running, so
        the caller can distinguish "not connected yet" from real errors.
        """
        if sys.platform != "win32":
            raise OSError("AC Evo shared memory is Windows-only")
        self._physics_mm = _NamedMapping(PHYSICS_TAG, PHYSICS_SIZE)
        try:
            self._graphics_mm = _NamedMapping(GRAPHICS_TAG, GRAPHICS_SIZE)
            self._static_mm = _NamedMapping(STATIC_TAG, STATIC_SIZE)
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

    def read_raw(self, segment: str) -> bytes:
        """Return the raw bytes of one segment for inspection / debugging."""
        mm = {"physics": self._physics_mm,
              "graphics": self._graphics_mm,
              "static": self._static_mm}.get(segment)
        if mm is None:
            raise ValueError(f"unknown or unopened segment: {segment!r}")
        return mm.read()


def _to_psi(pressure_value: float) -> float:
    """Pressure unit normalisation.

    AC1 ``wheelsPressure`` is already in psi for most cars. AC Evo's units
    are not yet officially documented; if a future capture shows kPa, divide
    by 6.895 here. Kept as a single hook so the fix is one line.
    """
    return pressure_value


class AcEvoTelemetrySource(TelemetrySource):
    """Polls AC Evo shared memory and emits :class:`TelemetryFrame`.

    Connection is best-effort: if the game isn't running, ``start()`` keeps
    polling silently and connects as soon as the SHM blocks appear. The
    overlay widgets keep showing whatever they last saw in the meantime.
    """

    def __init__(self, hz: int = 60, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._reader = AcEvoSharedMemoryReader()
        self._frame = TelemetryFrame()
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
            print("[ac-evo] connected to shared memory")
            return True
        except (OSError, RuntimeError) as exc:
            # File-not-found is the common "game not running" case; surface
            # other errors so misconfiguration is debuggable.
            if not isinstance(exc, FileNotFoundError):
                print(f"[ac-evo] connect failed: {exc}", file=sys.stderr)
            return False

    def _tick(self) -> None:
        if not self._reader.is_open:
            # Throttle reconnect attempts to once a second to avoid spamming
            # logs when the game is closed.
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
            print(f"[ac-evo] read failed, dropping connection: {exc}", file=sys.stderr)
            self._reader.close()
            return

        self._apply_physics(phys)
        self._apply_graphics(graphics)
        self.frame.emit(self._frame)

    def _apply_static(self, st: _SPageFileStatic) -> None:
        # AC Evo's static block is session/track metadata only — the AC1
        # car-spec fields (maxRpm/Power/Torque, suspensionMaxTravel) are
        # gone. The overlay sources its peaks from graphics (current_bhp,
        # rpm_percent, max_turbo_boost) and from rolling-max calibration in
        # physics, so there is nothing to apply here yet. Track name /
        # ambient temperature could feed future widgets.
        del st

    def _apply_physics(self, ph: _SPageFilePhysics) -> None:
        e = self._frame.engine
        e.rpm = float(ph.rpms)
        e.turbo_boost = float(ph.turboBoost)
        # A rolling observed max keeps the boost bar usable even when the
        # static maxTurboBoost is missing or wrong (some mods report 0).
        e.max_turbo_boost = max(e.max_turbo_boost, e.turbo_boost)
        e.gear = int(ph.gear)
        e.speed_kmh = float(ph.speedKmh)
        # tc/abs are 0..1 setting strength. The indicator chips light on
        # any non-zero level; tcInAction/absInAction can drive a separate
        # "currently engaging" highlight in a follow-up.
        e.tc_level = float(ph.tc)
        e.abs_level = float(ph.abs)
        e.pit_limiter = bool(ph.pitLimiterOn)

        braking = ph.brake > 0.0
        # absInAction is true only while ABS is actually modulating the
        # brakes — much more reliable than guessing from slip thresholds.
        abs_modulating = ph.absInAction != 0
        # A locked wheel only matters while the car is actually moving — at a
        # standstill all four wheels have angular_speed ≈ 0 by definition,
        # which would otherwise flag a permanent lock and make the indicator
        # blink forever.
        moving = float(ph.speedKmh) > 3.0

        for wid in WHEEL_IDS:
            idx = _WHEEL_INDEX[wid]
            w = self._frame.wheels[wid]

            slip = float(ph.wheelSlip[idx])
            ang_speed = float(ph.wheelAngularSpeed[idx])
            # Lock heuristic ported from lt_wheel_info.Data.update — wheel
            # locked when braking and either basically not turning or with
            # extreme slip.
            w.lock = (moving and braking and slip > 0.0
                      and (abs(ang_speed) < 1.0 or slip > 0.5))
            # ABS active on this wheel = system is modulating + this wheel
            # has the slip signature that triggered it.
            w.abs_active = abs_modulating and braking and not w.lock and slip > 0.10

            w.camber = float(ph.camberRAD[idx])
            w.susp_t = float(ph.suspensionTravel[idx])
            # Rolling-max calibration with a 5 % headroom: AC Evo no longer
            # publishes the per-car max travel in static, so we infer it
            # from the observed peak compression. Converges within a few
            # hard corners and keeps the colour thresholds (>0.95 / <0.05)
            # meaningful regardless of car class.
            if w.susp_t * 1.05 > w.susp_m_t:
                w.susp_m_t = w.susp_t * 1.05

            # Ride height: AC1 reports per-axle in metres (rideHeight[0]
            # front, [1] rear). Convert to mm.
            axle = idx // 2
            w.height = float(ph.rideHeight[axle]) * 1000.0

            w.tire_d = float(ph.tyreDirtyLevel[idx]) * 4.0
            # AC1 wheelLoad is Newtons; the original Load circle code divides
            # by (5 * g) to get the "5*kgf" pseudo-unit it expected.
            w.tire_l = float(ph.wheelLoad[idx]) / (5.0 * 9.80665)
            w.tire_p = _to_psi(float(ph.wheelsPressure[idx]))
            w.tire_t_c = float(ph.tyreCoreTemperature[idx])
            w.tire_t_i = float(ph.tyreTempI[idx])
            w.tire_t_m = float(ph.tyreTempM[idx])
            w.tire_t_o = float(ph.tyreTempO[idx])
            w.brake_t = float(ph.brakeTemp[idx])
            # tyreWear: AC Evo writes this at the AC1 offset but the
            # semantics are *inverted* — 0.0 = new, 1.0 = fully worn (per
            # the official shared-memory documentation). The overlay treats
            # tire_w as "remaining" (1.0 = fresh, 0.0 = dead), so we flip.
            w.tire_w = max(0.0, min(1.0, 1.0 - float(ph.tyreWear[idx])))

    def _apply_graphics(self, gr: _SPageFileGraphic) -> None:
        """Pull HUD/graphics fields that complement (and in places replace)
        the synthesized values fed from physics+static.

        The big wins here are live engine output (``current_bhp``,
        ``current_torque``) and the documented car-spec maxima
        (``max_turbo_boost``, ``max_fuel``) — AC Evo's static block doesn't
        carry car specs anymore, only session/track metadata.
        """
        e = self._frame.engine
        # Live engine output — far better than interpolating a fictional
        # torque curve. We treat it as the *boosted* HP at the current RPM
        # so engine_view can use it directly without the ``(1+boost)`` hack.
        e.current_bhp = float(gr.current_bhp)
        e.current_torque = float(gr.current_torque)
        # max_turbo_boost from the graphics block trumps the rolling-max
        # heuristic in physics; falls back to the heuristic when the value
        # is missing (mods, AI cars, etc.).
        if gr.max_turbo_boost > 0.0:
            e.max_turbo_boost = float(gr.max_turbo_boost)
        # rpm_percent (0..1) is RPM as a fraction of redline — lets the bar
        # fill correctly even when we never learn the absolute redline.
        rpm_percent = float(gr.rpm_percent)
        if rpm_percent > 0.0:
            e.rpm_percent = rpm_percent
        # Driver-aid booleans straight from the game, no slip heuristics.
        e.tc_in_action = bool(gr.tc_active)
        e.abs_in_action = bool(gr.abs_active)
        e.shift_up_hint = bool(gr.is_change_up_rpm)
        e.shift_down_hint = bool(gr.is_change_down_rpm)

        # Tyre compound names — duplicated across all 4 TyreStates, so we
        # read them once. ctypes c_char arrays come back as null-padded
        # bytes; strip nulls before decoding.
        front_raw = bytes(gr.tyre_lf.data.tyre_compound_front).rstrip(b"\x00")
        rear_raw = bytes(gr.tyre_lf.data.tyre_compound_rear).rstrip(b"\x00")
        front_compound = front_raw.decode("ascii", errors="ignore").strip()
        rear_compound = rear_raw.decode("ascii", errors="ignore").strip()
        self._frame.wheels["FL"].compound = front_compound
        self._frame.wheels["FR"].compound = front_compound
        self._frame.wheels["RL"].compound = rear_compound
        self._frame.wheels["RR"].compound = rear_compound
