"""Auto-detect which Assetto Corsa game is running.

Combines three cheap signals so we can map ``--source auto`` to the right
reader without asking the user which game they launched:

1. Shared-memory tag presence. AC Evo publishes under its own
   ``Local\\acevo_pmf_*`` namespace; AC1, ACC and AC Rally all share
   ``Local\\acpmf_*``. So the namespace alone tells us "AC Evo or not".
2. Running process names. For the ``acpmf_*`` family we read the process
   list via the Win32 toolhelp snapshot to pick AC1 / ACC / AC Rally.
3. Physics-block content. When process hints can't tell ACC apart from
   AC Rally (their EXE names sometimes overlap on Unreal Engine builds),
   we peek at ``tyreCoreTemp[0]`` — AC Rally publishes that field in
   Kelvin (≈ 290–360), ACC in Celsius (≤ ≈ 110). Anything above 150
   means Kelvin and pins the detector to AC Rally.

The detector is read-only — it opens the named mapping only long enough
to read the first few hundred bytes, then closes the handle. Running the
detector while a reader is also active is safe.
"""
from __future__ import annotations

import ctypes
import struct
import sys
from ctypes import wintypes


_FILE_MAP_READ = 0x0004
_TH32CS_SNAPPROCESS = 0x00000002
# Offset of ``tyreCoreTemp[0]`` inside the AC1-family physics struct.
# Layout: packetId..speedKmh (32 B) + velocity (12) + accG (12) +
# wheelSlip / wheelLoad / wheelPressure / wheelAngularSpeed / tyreWear /
# tyreDirtyLevel (6 × 16 B) = 152 B before the tyreCoreTemp array.
_TYRE_CORE_TEMP_OFFSET = 152
# Any tyreCoreTemp above this is implausibly hot in °C and almost
# certainly Kelvin, so we attribute it to AC Rally rather than ACC.
_KELVIN_DECISION_THRESHOLD = 150.0
# A positive value below the Kelvin threshold is plausibly Celsius — ACC
# writes ambient ~10 °C at session start and ≤ ~110 °C hot. The lower
# bound rules out a fully zero-filled (paused / menu) physics block so
# we can keep polling instead of locking in a wrong guess.
_CELSIUS_DECISION_FLOOR = 5.0
# Win32 returns INVALID_HANDLE_VALUE (a sign-extended -1) as the raw
# integer wrapped in a c_void_p; cast through int so pylint sees a plain
# constant instead of inferring it as a class definition via the .value
# attribute walk.
_INVALID_HANDLE = int(ctypes.c_void_p(-1).value or 0)


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

    _CreateToolhelp32Snapshot = _KERNEL32.CreateToolhelp32Snapshot
    _CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _CreateToolhelp32Snapshot.restype = ctypes.c_void_p
else:
    _KERNEL32 = None


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def _tag_exists(name: str) -> bool:
    """Return True iff a Windows named file-mapping exists under ``name``.

    Opens the mapping for the briefest possible moment and immediately
    closes it; no view is mapped, so this is essentially free.
    """
    if _KERNEL32 is None:
        return False
    handle = _OpenFileMappingW(_FILE_MAP_READ, False, name)
    if not handle:
        return False
    _CloseHandle(handle)
    return True


def _acpmf_physics_tyre_core_temp() -> float | None:
    """Read ``tyreCoreTemp[0]`` from ``Local\\acpmf_physics``.

    Returns the raw float as written by the game, or ``None`` when the
    mapping can't be opened or the value can't be unpacked. The caller
    decides what range constitutes Kelvin vs Celsius vs "no signal yet".
    """
    if _KERNEL32 is None:
        return None
    handle = _OpenFileMappingW(_FILE_MAP_READ, False, "Local\\acpmf_physics")
    if not handle:
        return None
    try:
        view = _MapViewOfFile(handle, _FILE_MAP_READ, 0, 0,
                              _TYRE_CORE_TEMP_OFFSET + 4)
        if not view:
            return None
        try:
            blob = ctypes.string_at(view, _TYRE_CORE_TEMP_OFFSET + 4)
        finally:
            _UnmapViewOfFile(view)
    finally:
        _CloseHandle(handle)
    try:
        return float(struct.unpack_from("<f", blob, _TYRE_CORE_TEMP_OFFSET)[0])
    except struct.error:
        return None


def acpmf_tag_present() -> bool:
    """Public helper: is the shared ``Local\\acpmf_*`` namespace up at all?

    Lets ``DetectionView`` distinguish "still ambiguous, keep polling"
    from "nothing running at all" so it can apply the ACC fallback only
    when a game in the acpmf family is genuinely present.
    """
    return _tag_exists("Local\\acpmf_static")


def _running_processes() -> list[str]:
    """Return lower-cased EXE basenames of every process currently running.

    Returns an empty list on non-Windows or if the snapshot call fails —
    callers must treat that as "unknown" and not as "nothing is running".
    """
    if _KERNEL32 is None:
        return []
    snap = _CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snap is None or snap == _INVALID_HANDLE:
        return []

    first = _KERNEL32.Process32FirstW
    first.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32W)]
    first.restype = wintypes.BOOL
    nxt = _KERNEL32.Process32NextW
    nxt.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32W)]
    nxt.restype = wintypes.BOOL

    try:
        names: list[str] = []
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        ok = first(snap, ctypes.byref(entry))
        while ok:
            names.append(entry.szExeFile.lower())
            ok = nxt(snap, ctypes.byref(entry))
        return names
    finally:
        _CloseHandle(snap)


# Known EXE names per game. Lower-cased for case-insensitive comparison.
# Listed in priority order — the first match wins inside the acpmf_* family
# so that AC Rally (which may share an Unreal-Engine shipping binary with
# ACC) is picked when the unambiguous Rally process is also present.
_AC_RALLY_PROCESS_HINTS = ("acrally", "ac rally", "assetto corsa rally")
_AC1_PROCESS_NAMES = ("acs.exe",)
_ACC_PROCESS_NAMES = ("ac2-win64-shipping.exe", "acc.exe")


def detect_running_game() -> str | None:  # pylint: disable=too-many-return-statements
    """Return the matching ``--source`` name, or ``None`` when no game is up
    *or* the running game in the ``acpmf_*`` family can't be confidently
    identified yet.

    Returns one of ``"ac-evo"`` / ``"ac1"`` / ``"acc"`` / ``"acrally"``.
    Callers that want a fallback for the ambiguous case (acpmf tags
    present but no process / content signal) should pair this with
    :func:`acpmf_tag_present` and a small timeout — see ``DetectionView``.
    """
    if _tag_exists("Local\\acevo_pmf_static"):
        return "ac-evo"
    if not _tag_exists("Local\\acpmf_static"):
        return None

    procs = _running_processes()
    if any(any(hint in p for hint in _AC_RALLY_PROCESS_HINTS) for p in procs):
        return "acrally"
    if any(p in procs for p in _AC1_PROCESS_NAMES):
        return "ac1"
    # ACC and AC Rally sometimes ship under the same Unreal Engine
    # shipping binary, so process hints can't always tell them apart.
    # Peek at tyreCoreTemp[0]: Kelvin (≈ 290–360) → AC Rally, plausible
    # Celsius (≈ 5–150) → ACC, anything near zero → game is paused or
    # in a menu and the physics block hasn't been written yet. In the
    # last case we deliberately return None so the caller keeps polling
    # rather than mis-categorising a paused AC Rally session as ACC.
    temp = _acpmf_physics_tyre_core_temp()
    if temp is not None:
        if temp > _KELVIN_DECISION_THRESHOLD:
            return "acrally"
        if temp >= _CELSIUS_DECISION_FLOOR:
            return "acc"
    return None


__all__ = ["detect_running_game"]
