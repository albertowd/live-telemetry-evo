"""Probe for AC-family shared-memory tags on the running session.

Windows doesn't have a public API to enumerate every named file-mapping,
so this script just attempts ``OpenFileMappingW`` against a list of
plausible tag names (AC1, ACC, AC Evo, plus likely AC Rally variants)
and reports which ones the producer is publishing right now.

For each hit we map a small window and dump the first 64 bytes as hex
so the layout family can be eyeballed (AC1/ACC physics start with an
int packetId at offset 0, etc.).

Usage::

    python tools/probe_shm.py
"""
from __future__ import annotations

import ctypes
import sys


_FILE_MAP_READ = 0x0004

if sys.platform != "win32":
    raise SystemExit("Windows-only.")

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


# Known good base names and a bunch of plausible AC Rally guesses.
# Each base gets tried in three combinations: bare, Local\, and Global\.
BASES = [
    # Confirmed AC1 / ACC
    "acpmf_physics", "acpmf_graphics", "acpmf_static",
    # Confirmed AC Evo
    "acevo_pmf_physics", "acevo_pmf_graphics", "acevo_pmf_static",
    # AC Rally — guesses following the Evo / AC1 naming patterns
    "acrally_pmf_physics", "acrally_pmf_graphics", "acrally_pmf_static",
    "acrally_physics", "acrally_graphics", "acrally_static",
    "acr_pmf_physics", "acr_pmf_graphics", "acr_pmf_static",
    "acr_physics", "acr_graphics", "acr_static",
    "acrl_pmf_physics", "acrl_pmf_graphics", "acrl_pmf_static",
    "acrl_physics", "acrl_graphics", "acrl_static",
    "rally_pmf_physics", "rally_pmf_graphics", "rally_pmf_static",
    "ac_rally_pmf_physics", "ac_rally_pmf_graphics", "ac_rally_pmf_static",
    "AssettoCorsaRally_physics", "AssettoCorsaRally_graphics", "AssettoCorsaRally_static",
]

PREFIXES = ["Local\\", "Global\\", ""]


def _try_open(name: str) -> tuple[bool, bytes | None]:
    """Return (exists, first_64_bytes). first_64_bytes is None if open failed."""
    handle = _OpenFileMappingW(_FILE_MAP_READ, False, name)
    if not handle:
        return False, None
    view = _MapViewOfFile(handle, _FILE_MAP_READ, 0, 0, 64)
    if not view:
        _CloseHandle(handle)
        return True, None  # mapping exists but couldn't be viewed
    data = ctypes.string_at(view, 64)
    _UnmapViewOfFile(view)
    _CloseHandle(handle)
    return True, data


def _hexdump(data: bytes) -> str:
    out = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        hexs = " ".join(f"{b:02x}" for b in chunk)
        ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        out.append(f"  {i:04x}  {hexs:<47}  {ascii_}")
    return "\n".join(out)


def main() -> int:
    seen: set[str] = set()
    hits: list[tuple[str, bytes | None]] = []
    for prefix in PREFIXES:
        for base in BASES:
            name = prefix + base
            if name in seen:
                continue
            seen.add(name)
            exists, data = _try_open(name)
            if exists:
                hits.append((name, data))

    if not hits:
        print("no AC-family shared memory found.")
        print(f"  tried {len(seen)} tag names")
        print("  is the game running and publishing telemetry?")
        return 1

    print(f"found {len(hits)} live shared-memory tag(s):\n")
    for name, data in hits:
        print(f"  {name}")
        if data is not None:
            print(_hexdump(data))
        else:
            print("  (open succeeded but MapViewOfFile failed)")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
