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
import struct
import sys
import time

from .ac_evo import AcEvoSharedMemoryReader


def _track_monotonic(reader: AcEvoSharedMemoryReader, segment: str,
                     duration_s: float, lo: float, hi: float,
                     min_drop: float = 0.0005) -> str:
    """Watch a segment over time and report aligned floats that only ever decrease.

    True tire wear is monotonic — it can't recover without a tire change.
    Sample the buffer repeatedly over ``duration_s`` and keep only offsets
    where every sample was <= the previous one, with at least ``min_drop``
    total decrease (filters out noise). Constrain to floats in [lo, hi]
    throughout to skip out-of-range padding.
    """
    samples: list[bytes] = []
    deadline = time.monotonic() + duration_s
    print(f"[track] sampling '{segment}' for {duration_s:.0f} s — drive the car...",
          file=sys.stderr)
    while time.monotonic() < deadline:
        samples.append(reader.read_raw(segment))
        time.sleep(0.5)
    if len(samples) < 3:
        return "[track] need at least 3 samples; increase --duration"

    end = min(len(samples[0]) - 4, 4096)
    candidates: list[tuple[int, float, float]] = []
    for i in range(0, end, 4):
        try:
            values = [struct.unpack_from("<f", s, i)[0] for s in samples]
        except struct.error:
            continue
        if any(v != v for v in values):  # NaN
            continue
        if not all(lo <= v <= hi for v in values):
            continue
        if not all(values[k] >= values[k + 1] for k in range(len(values) - 1)):
            continue
        drop = values[0] - values[-1]
        if drop < min_drop:
            continue
        candidates.append((i, values[0], values[-1]))

    lines = [f"[track] monotonically-decreasing offsets in [{lo}, {hi}] "
             f"with >={min_drop} drop over {duration_s:.0f} s "
             f"({len(samples)} samples):"]
    for off, first, last in candidates:
        lines.append(f"  0x{off:04x} ({off:4d}): {first:.6f} -> {last:.6f}  "
                     f"(Δ {first - last:+.6f})")
    lines.append(f"  total: {len(candidates)}")
    return "\n".join(lines)


def _scan_floats(data: bytes, lo: float, hi: float, max_offset: int = 4096) -> str:
    """List every 4-byte aligned float in the buffer that falls in [lo, hi].

    Useful for hunting unknown field offsets: pick a range tight enough to
    rule out padding (e.g. tire wear is roughly 0.5..1.0 on AC1 scale, brake
    temp is 50..900 C). Run twice — once with a known tire state, once with
    a different state — and the offsets that changed are your candidates.
    """
    lines: list[str] = [f"[scan] aligned floats in [{lo}, {hi}]:"]
    found = 0
    end = min(len(data), max_offset)
    for i in range(0, end - 4, 4):
        v = struct.unpack_from("<f", data, i)[0]
        if v != v or v == 0.0:  # skip NaN and exact zero
            continue
        if lo <= v <= hi:
            lines.append(f"  0x{i:04x} ({i:4d}): {v:.6f}")
            found += 1
    lines.append(f"  total: {found}")
    return "\n".join(lines)


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
        print(f"  tyreWear       {tuple(ph.tyreWear)}")
        print(f"  tyreDirtyLevel {tuple(ph.tyreDirtyLevel)}")
        print(f"  brakeTemp      {tuple(ph.brakeTemp)}")
        print(f"  rideHeight     {tuple(ph.rideHeight)}")
        print(f"  tc             {ph.tc:.3f}")
        print(f"  pitLimiterOn   {ph.pitLimiterOn}")
    elif segment == "static":
        st = reader.read_static()
        print("[static]")
        print(f"  sm_version           {st.sm_version!r}")
        print(f"  ac_evo_version       {st.ac_evo_version!r}")
        print(f"  session              {st.session}")
        print(f"  session_name         {st.session_name!r}")
        print(f"  starting_grip        {st.starting_grip}")
        print(f"  starting_ambient_c   {st.starting_ambient_temperature_c:.2f}")
        print(f"  starting_ground_c    {st.starting_ground_temperature_c:.2f}")
        print(f"  is_static_weather    {st.is_static_weather}")
        print(f"  is_online            {st.is_online}")
        print(f"  nation               {st.nation!r}")
        print(f"  track                {st.track!r}")
        print(f"  track_configuration  {st.track_configuration!r}")
        print(f"  track_length_m       {st.track_length_m:.1f}")
    else:
        print(f"[{segment}] parsed view not implemented yet — use --bytes for a hex dump")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("segment", choices=("physics", "graphics", "static"))
    parser.add_argument("--bytes", type=int, default=128, help="hex dump window size")
    parser.add_argument("--parsed", action="store_true", help="print parsed struct fields")
    parser.add_argument("--scan", nargs=2, type=float, metavar=("LO", "HI"),
                        help="scan aligned floats and list those in [LO, HI]")
    parser.add_argument("--track-monotonic", nargs=3, type=float,
                        metavar=("DURATION_S", "LO", "HI"),
                        help="sample over DURATION_S; report offsets in [LO, HI] "
                             "that only ever decreased (find true wear fields)")
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
            elif args.scan is not None:
                raw = reader.read_raw(args.segment)
                print(_scan_floats(raw, args.scan[0], args.scan[1]))
            elif args.track_monotonic is not None:
                duration, lo, hi = args.track_monotonic
                print(_track_monotonic(reader, args.segment, duration, lo, hi))
                break  # tracking has its own timing loop
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
