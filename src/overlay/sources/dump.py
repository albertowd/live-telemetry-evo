"""Dump tool for inspecting AC Evo shared memory against a running game.

Usage::

    python -m overlay.sources.dump physics
    python -m overlay.sources.dump graphics --bytes 256
    python -m overlay.sources.dump static --parsed

Designed for iterating on the struct layout: print a raw hex window, or
parse with the best-effort structs and show the resulting field values, so
known values (RPM you're holding on screen, current gear, etc.) can be
matched to byte offsets.
"""
from __future__ import annotations

import argparse
import sys
import time

from .ac_evo import AcEvoSharedMemoryReader


def _hex_dump(data: bytes, width: int = 16) -> str:
    lines = []
    for i in range(0, len(data), width):
        chunk = data[i:i + width]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{i:04x}  {hex_part:<{width * 3}}  {ascii_part}")
    return "\n".join(lines)


def _print_parsed(reader: AcEvoSharedMemoryReader, segment: str) -> None:
    if segment == "physics":
        ph = reader.read_physics()
        print("[physics]")
        print(f"  packetId       {ph.packetId}")
        print(f"  rpms           {ph.rpms}")
        print(f"  gear           {ph.gear}")
        print(f"  speedKmh       {ph.speedKmh:.2f}")
        print(f"  turboBoost     {ph.turboBoost:.3f}")
        print(f"  brake          {ph.brake:.3f}")
        print(f"  gas            {ph.gas:.3f}")
        print(f"  abs            {ph.abs:.3f}")
        print(f"  wheelSlip      {tuple(ph.wheelSlip)}")
        print(f"  wheelLoad      {tuple(ph.wheelLoad)}")
        print(f"  wheelsPressure {tuple(ph.wheelsPressure)}")
        print(f"  tyreCoreTemp   {tuple(ph.tyreCoreTemperature)}")
        print(f"  rideHeight     {tuple(ph.rideHeight)}")
    elif segment == "static":
        st = reader.read_static()
        print("[static]")
        print(f"  smVersion       {st.smVersion!r}")
        print(f"  acVersion       {st.acVersion!r}")
        print(f"  carModel        {st.carModel!r}")
        print(f"  track           {st.track!r}")
        print(f"  maxRpm          {st.maxRpm}")
        print(f"  maxPower        {st.maxPower}")
        print(f"  maxTurboBoost   {st.maxTurboBoost}")
        print(f"  suspensionMaxTravel {tuple(st.suspensionMaxTravel)}")
    else:
        print(f"[{segment}] parsed view not implemented yet — use --bytes for a hex dump")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("segment", choices=("physics", "graphics", "static"))
    parser.add_argument("--bytes", type=int, default=128, help="hex dump window size")
    parser.add_argument("--parsed", action="store_true", help="print parsed struct fields")
    parser.add_argument("--watch", type=float, default=0.0,
                        help="if > 0, repeat at this interval in seconds")
    args = parser.parse_args(argv)

    reader = AcEvoSharedMemoryReader()
    try:
        reader.open()
    except OSError as exc:
        print(f"could not attach to AC Evo shared memory: {exc}", file=sys.stderr)
        print("is the game running and on this machine?", file=sys.stderr)
        return 1

    try:
        while True:
            print("-" * 60)
            if args.parsed:
                _print_parsed(reader, args.segment)
            else:
                raw = reader.read_raw(args.segment)[:args.bytes]
                print(_hex_dump(raw))
            if args.watch <= 0.0:
                break
            time.sleep(args.watch)
    finally:
        reader.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
