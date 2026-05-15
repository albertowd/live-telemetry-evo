"""Decide AC Rally's physics layout by probing key offsets.

The AC1-compatible prefix (offsets 0..415) is shared by AC1, ACC, AC Evo
*and* — based on the first probe — AC Rally. The question this script
answers: does AC Rally also write the AC Evo / ACC additions
(offsets 416..799)? If yes, we can probably reuse the ACC source class.
If no, AC Rally is more like AC1 and we'd target that struct shape.

Run while in a *driving session* (so values like camber, ride height,
wheel load are non-trivial — those are the fields most likely to
distinguish between layouts).
"""
from __future__ import annotations

import ctypes
import struct
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
_CloseHandle = _KERNEL32.CloseHandle
_CloseHandle.argtypes = [ctypes.c_void_p]


def attach(name: str, size: int) -> bytes:
    h = _OpenFileMappingW(_FILE_MAP_READ, False, name)
    if not h:
        raise SystemExit(f"could not open {name}")
    v = _MapViewOfFile(h, _FILE_MAP_READ, 0, 0, size)
    if not v:
        _CloseHandle(h)
        raise SystemExit(f"MapViewOfFile failed for {name}")
    data = ctypes.string_at(v, size)
    _UnmapViewOfFile(v)
    _CloseHandle(h)
    return data


def f4(buf: bytes, off: int) -> float:
    return struct.unpack_from("<f", buf, off)[0]


def i4(buf: bytes, off: int) -> int:
    return struct.unpack_from("<i", buf, off)[0]


def f4x4(buf: bytes, off: int) -> tuple[float, ...]:
    return struct.unpack_from("<4f", buf, off)


def main() -> int:
    buf = attach("Local\\acpmf_physics", 1024)
    print(f"physics buffer: {len(buf)} bytes")
    print()

    # AC1-compatible prefix — known to work.
    print("=== AC1-compatible prefix (offsets 0..415) ===")
    print(f"  packetId      (0)   = {i4(buf, 0)}")
    print(f"  gas           (4)   = {f4(buf, 4):.3f}")
    print(f"  brake         (8)   = {f4(buf, 8):.3f}")
    print(f"  fuel          (12)  = {f4(buf, 12):.3f}")
    print(f"  gear          (16)  = {i4(buf, 16)}")
    print(f"  rpm           (20)  = {i4(buf, 20)}")
    print(f"  steerAngle    (24)  = {f4(buf, 24):.4f} rad")
    print(f"  speedKmh      (28)  = {f4(buf, 28):.2f}")
    print(f"  wheelSlip     (56)  = {f4x4(buf, 56)}")
    print(f"  wheelLoad     (72)  = {f4x4(buf, 72)}")
    print(f"  wheelsPress   (88)  = {f4x4(buf, 88)}")
    print(f"  tyreCoreTemp  (152) = {f4x4(buf, 152)}")
    print(f"  camberRAD     (168) = {f4x4(buf, 168)}")
    print(f"  suspTravel    (184) = {f4x4(buf, 184)}")
    print(f"  rideHeight    (268) = {struct.unpack_from('<2f', buf, 268)}")
    print(f"  turboBoost    (276) = {f4(buf, 276):.3f}")
    print(f"  brakeTemp     (348) = {f4x4(buf, 348)}")
    print(f"  tyreTempI     (368) = {f4x4(buf, 368)}")
    print(f"  tyreTempM     (384) = {f4x4(buf, 384)}")
    print(f"  tyreTempO     (400) = {f4x4(buf, 400)}")

    # AC Evo / ACC additions — present on AC Evo + ACC, absent on AC1.
    print()
    print("=== Possible AC Evo / ACC additions (416..799) ===")
    print(f"  isAIControlled    (416) int  = {i4(buf, 416)}")
    print(f"  tyreContactPoint  (420) f12  = ", end="")
    print([round(f4(buf, 420 + 4 * j), 3) for j in range(12)])
    print(f"  brakeBias         (564) f    = {f4(buf, 564):.3f}")
    print(f"  localVelocity     (568) f3   = {struct.unpack_from('<3f', buf, 568)}")
    print(f"  P2P (?) bytes     (580) i,i  = {i4(buf, 580)}, {i4(buf, 584)}")
    print(f"  currentMaxRpm     (588) int  = {i4(buf, 588)}")
    print(f"  currentMaxRpm     (588) flt  = {f4(buf, 588)}")
    print(f"  mz                (592) = {f4x4(buf, 592)}")
    print(f"  fx                (608) = {f4x4(buf, 608)}")
    print(f"  fy                (624) = {f4x4(buf, 624)}")
    print(f"  slipRatio         (640) = {f4x4(buf, 640)}")
    print(f"  slipAngle         (656) = {f4x4(buf, 656)}")
    print(f"  tcInAction        (672) int  = {i4(buf, 672)}")
    print(f"  absInAction       (676) int  = {i4(buf, 676)}")
    print(f"  suspensionDamage  (680) = {f4x4(buf, 680)}")
    print(f"  tyreTemp          (696) = {f4x4(buf, 696)}")
    print(f"  waterTemp         (712) = {f4(buf, 712):.2f}")
    print(f"  brakePressure/Trq (716) = {f4x4(buf, 716)}")
    print(f"  frontBrkCompound  (732) int  = {i4(buf, 732)}")
    print(f"  rearBrkCompound   (736) int  = {i4(buf, 736)}")
    print(f"  padLife           (740) = {f4x4(buf, 740)}")
    print(f"  discLife          (756) = {f4x4(buf, 756)}")
    print(f"  ignitionOn        (772) int  = {i4(buf, 772)}")
    print(f"  starterEngineOn   (776) int  = {i4(buf, 776)}")
    print(f"  isEngineRunning   (780) int  = {i4(buf, 780)}")

    # Heuristic verdict.
    print()
    nonzero_in_extras = sum(
        1 for v in (
            i4(buf, 416),
            f4(buf, 564),  # brakeBias
            i4(buf, 588), f4(buf, 588),
            *f4x4(buf, 640),  # slipRatio
            f4(buf, 712),     # waterTemp
            *f4x4(buf, 740),  # padLife
            i4(buf, 780),     # isEngineRunning
        ) if v != 0.0 and v != 0
    )
    print(f"non-zero values in 416..799 area: {nonzero_in_extras}")
    if nonzero_in_extras >= 3:
        print("verdict: AC Rally writes the AC Evo / ACC additions — try --source acc.")
    else:
        print("verdict: AC Rally looks AC1-shaped (extras unwritten) — try --source ac1.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
