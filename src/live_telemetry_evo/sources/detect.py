"""Auto-detect which Assetto Corsa game is running.

Maps ``--source auto`` to the right reader without asking the user which
game they launched. **The running EXE is the whole signal**: we read the
process list via the Win32 toolhelp snapshot and match it against the
known binary names in :data:`_GAMES`. Only one AC title is ever running
on a machine at a time, so the first match wins.

The detector does not look at shared memory. The ``Local\\acpmf_*``
namespace is shared by AC1, ACC and AC Rally so it can't identify a game
on its own, and a mapping left by a crashed game — or held open by a
tool like Content Manager or SimHub — is indistinguishable from a live
one.

A game therefore counts as running from the moment its process appears,
possibly before it has published any telemetry. That is safe: the readers
open their mapping lazily and retry, treating a not-yet-present mapping
as "not connected yet" rather than an error (see
``AcRallyTelemetrySource._try_connect`` and its siblings), so the overlay
simply shows no data until the game starts publishing.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes


_TH32CS_SNAPPROCESS = 0x00000002
# Win32 returns INVALID_HANDLE_VALUE (a sign-extended -1) as the raw
# integer wrapped in a c_void_p; cast through int so pylint sees a plain
# constant instead of inferring it as a class definition via the .value
# attribute walk.
_INVALID_HANDLE = int(ctypes.c_void_p(-1).value or 0)


if sys.platform == "win32":
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)

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


def is_process_running(names: tuple[str, ...]) -> bool:
    """Return True if any process whose EXE basename matches one of
    ``names`` (case-insensitive, substring match) is currently running.

    Thin public wrapper around the toolhelp snapshot used for game
    detection so other subsystems (e.g. ``overlay.vr.detect``) can probe
    for a process — like SteamVR's ``vrserver.exe`` — without duplicating
    the Win32 plumbing. Returns False on non-Windows or if the snapshot
    fails (callers must treat that as "unknown", not "definitely off").
    """
    procs = _running_processes()
    return any(any(n in p for n in names) for p in procs)


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


# The detection table: ``--source`` name and the EXE basenames that
# identify it. Matching is case-insensitive but **exact** — the process
# basename must equal one of these, not merely contain it. The EXE is the
# only detection signal, so a loose substring match would commit to the
# wrong game outright (``acs.exe`` occurs inside a file named
# ``maracs.exe.old``, and so on) with nothing left to catch the error.
#
# Verified against the shipped Steam installs:
#
#   AC Evo   …\Assetto Corsa EVO\AssettoCorsaEVO.exe
#   AC1      …\assettocorsa\acs.exe          (acs_x86.exe on the 32-bit build)
#   ACC      …\Assetto Corsa Competizione\acc.exe  (launcher) and
#            …\AC2\Binaries\Win64\AC2-Win64-Shipping.exe (the game itself)
#   Rally    …\Assetto Corsa Rally\acr\Binaries\Win64\acr.exe
#
# AC Evo ships a single binary in the install root — it runs on Kunos'
# own engine (RenoirCore), not Unreal, so there is no separate
# ``*-Win64-Shipping.exe`` to match the way ACC has one.
#
# Note "acr.exe" and "acs.exe" carry no trace of the words "rally" or
# "corsa": a descriptive guess like "acrally" matches nothing on a real
# install. Only add a name here after checking it against an actual one —
# a wrong name fails silently, with no second signal to fall back on.
# "acrally.exe" is the single exception, a plausible non-Steam/repack name
# listed exactly so it costs nothing if it never appears.
#
# Order matters only as tie-break insurance; a machine never runs two AC
# titles at once.
_GAMES: tuple[tuple[str, frozenset[str]], ...] = (
    ("ac-evo", frozenset({"assettocorsaevo.exe"})),
    ("acrally", frozenset({"acr.exe", "acrally.exe"})),
    ("ac1", frozenset({"acs.exe", "acs_x86.exe"})),
    ("acc", frozenset({"ac2-win64-shipping.exe", "acc.exe"})),
)


def detect_running_game() -> str | None:
    """Return the matching ``--source`` name, or ``None`` when no
    supported game is running.

    Returns one of ``"ac-evo"`` / ``"ac1"`` / ``"acc"`` / ``"acrally"``,
    decided purely by which EXE is in the process list. A game counts as
    running from the moment its process appears — it may not have
    published shared memory yet, which the readers handle by retrying
    their connection. ``None`` means "keep polling", not "give up".
    """
    procs = set(_running_processes())
    for name, exes in _GAMES:
        if procs & exes:
            return name
    return None


__all__ = ["detect_running_game", "is_process_running"]
