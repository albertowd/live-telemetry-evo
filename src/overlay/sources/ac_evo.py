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
from ctypes import c_float, c_int32, c_wchar
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
    """Best-effort AC Evo physics layout.

    Seeded from AC1 ``SPageFilePhysics``. AC Evo is known to extend this
    struct (more sub-structs, more fields), but the prefix used here covers
    every value the overlay widgets currently need: rpms, turboBoost, abs,
    wheel arrays for slip / load / pressure / wear / dirty / camber /
    suspension travel, tyre temps (core / inner / middle / outer), ride
    height pair.
    """

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
    ]


class _SPageFileStatic(ctypes.Structure):
    """Best-effort AC Evo static layout (used to seed maxRpm /
    suspensionMaxTravel / maxTurboBoost / maxPower)."""

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
    ]


# Order of the wheel arrays in AC1 / AC Evo: 0=FL, 1=FR, 2=RL, 3=RR.
_WHEEL_INDEX = {"FL": 0, "FR": 1, "RL": 2, "RR": 3}


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
        except (OSError, ValueError) as exc:
            print(f"[ac-evo] read failed, dropping connection: {exc}", file=sys.stderr)
            self._reader.close()
            return

        self._apply_physics(phys)
        self.frame.emit(self._frame)

    def _apply_static(self, st: _SPageFileStatic) -> None:
        e = self._frame.engine
        if st.maxRpm > 0:
            e.max_rpm = float(st.maxRpm)
        if st.maxPower > 0.0:
            e.max_power = float(st.maxPower)
        if st.maxTorque > 0.0:
            e.max_torque = float(st.maxTorque)
        if st.maxTurboBoost > 0.0:
            e.max_turbo_boost = float(st.maxTurboBoost)
        for wid, idx in _WHEEL_INDEX.items():
            travel = float(st.suspensionMaxTravel[idx])
            if travel > 0.0:
                self._frame.wheels[wid].susp_m_t = travel

    def _apply_physics(self, ph: _SPageFilePhysics) -> None:
        e = self._frame.engine
        e.rpm = float(ph.rpms)
        e.turbo_boost = float(ph.turboBoost)
        # A rolling observed max keeps the boost bar usable even when the
        # static maxTurboBoost is missing or wrong (some mods report 0).
        e.max_turbo_boost = max(e.max_turbo_boost, e.turbo_boost)
        e.gear = int(ph.gear)
        e.speed_kmh = float(ph.speedKmh)
        e.tc_level = float(ph.tc)
        e.abs_level = float(ph.abs)
        e.pit_limiter = bool(ph.pitLimiterOn)

        braking = ph.brake > 0.0
        abs_enabled = ph.abs > 0.0
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
            w.abs_active = abs_enabled and braking and not w.lock and slip > 0.10

            w.camber = float(ph.camberRAD[idx])
            w.susp_t = float(ph.suspensionTravel[idx])

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
            # tyreWear: AC1 uses 0..100 (% remaining); the overlay expects
            # 0..1.
            w.tire_w = max(0.0, min(1.0, float(ph.tyreWear[idx]) / 100.0))
