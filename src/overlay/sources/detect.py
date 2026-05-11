"""Auto-detect which Assetto Corsa game is running.

Combines two cheap signals so we can map ``--source auto`` to the right
reader without asking the user which game they launched:

1. Shared-memory tag presence. AC Evo publishes under its own
   ``Local\\acevo_pmf_*`` namespace; AC1, ACC and AC Rally all share
   ``Local\\acpmf_*``. So the namespace alone tells us "AC Evo or not".
2. Running process names. For the ``acpmf_*`` family we read the process
   list via the Win32 toolhelp snapshot to pick AC1 / ACC / AC Rally.

The detector is read-only — it opens the named mapping only long enough
to confirm it exists, then closes the handle. It never holds a view, so
running the detector while a reader is also active is safe.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes


_FILE_MAP_READ = 0x0004
_TH32CS_SNAPPROCESS = 0x00000002
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


def detect_running_game() -> str | None:
    """Return the matching ``--source`` name, or ``None`` if no AC game is up.

    Returns one of ``"ac-evo"`` / ``"ac1"`` / ``"acc"`` / ``"acrally"``.
    Falls back to ``"acc"`` when ``acpmf_*`` is present but the process
    snapshot can't disambiguate — ACC is the most common modern title in
    that namespace, and the user can always override with an explicit
    ``--source`` flag.
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
    if any(p in procs for p in _ACC_PROCESS_NAMES):
        return "acc"
    return "acc"


__all__ = ["detect_running_game"]
