"""Snapshot the AC Rally shared memory and hunt for missing fields.

Run twice:

    python tools/inspect_acrally.py parked   # car stopped, in stage
    python tools/inspect_acrally.py driving  # actually moving

Each run writes a JSON dump (snapshot_acrally_<label>.json) and prints:

* every documented physics offset decoded with the ACC layout,
* the same for graphics + static,
* a heuristic scan over the physics buffer that flags non-zero floats
  whose magnitudes match the missing fields (camber ~ ±0.15 rad, ride
  height ~ 0.001..0.2 m, tire face temp 50..120 °C or 320..400 K).

After both runs, run with ``diff`` as the label to compare:

    python tools/inspect_acrally.py diff

— only offsets that *changed* between parked and driving show up. That's
the cleanest way to find the real per-wheel arrays for camber / ride
height / face-temp if AC Rally publishes them at a non-standard offset.
"""
from __future__ import annotations

import ctypes
import json
import struct
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, "src")

from overlay.sources._win32_mapping import NamedMapping  # noqa: E402
from overlay.sources.acc import (_SPageFilePhysics, _SPageFileGraphic,  # noqa: E402
                                  _SPageFileStatic, PHYSICS_TAG, GRAPHICS_TAG,
                                  STATIC_TAG)


PHYSICS_BYTES = 1024
GRAPHICS_BYTES = 4096
STATIC_BYTES = 2048


def attach(name: str, size: int) -> bytes:
    mm = NamedMapping(name, size)
    try:
        return mm.read()
    finally:
        mm.close()


def parse_struct(buf: bytes, struct_t) -> dict[str, Any]:
    """Decode every named field of a ctypes struct from raw bytes."""
    s = struct_t.from_buffer_copy(buf, 0)
    out: dict[str, Any] = {}
    for f in struct_t._fields_:
        name = f[0]
        v = getattr(s, name)
        if hasattr(v, "_length_"):  # array
            try:
                v = list(v)
                if v and hasattr(v[0], "_length_"):
                    v = [list(row) for row in v]
            except TypeError:
                pass
        elif isinstance(v, bytes):
            v = v.rstrip(b"\x00").decode("ascii", errors="ignore")
        out[name] = v
    return out


def scan_physics_floats(buf: bytes) -> list[dict]:
    """Heuristic scan: find every aligned float in plausible ranges for
    the fields we *know* AC Rally probably publishes somewhere."""
    hints: list[dict] = []
    end = min(len(buf), 800) - 4
    for off in range(0, end, 4):
        v = struct.unpack_from("<f", buf, off)[0]
        if v != v or v == 0.0:  # skip NaN and exact zero
            continue
        tags = []
        if -0.20 <= v <= 0.20:
            tags.append("rad?")  # camber, slip angle, small-angle stuff
        if 0.001 <= v <= 0.30:
            tags.append("metres?")  # ride height, susp travel
        if 50.0 <= v <= 130.0:
            tags.append("degC?")  # tire / brake / water in C
        if 280.0 <= v <= 420.0:
            tags.append("Kelvin?")  # same fields in K
        if 1000.0 <= v <= 6000.0:
            tags.append("N?")  # wheel load
        if not tags:
            continue
        hints.append({"offset": off, "value": v, "could_be": tags})
    return hints


def known_offsets_dump(phys: bytes) -> dict[str, Any]:
    """Dump the offsets that should be camber, ride height, face temps
    so we can see at a glance whether they're populated."""
    return {
        "camberRAD@168": list(struct.unpack_from("<4f", phys, 168)),
        "rideHeight@268": list(struct.unpack_from("<2f", phys, 268)),
        "tyreCoreTemp@152": list(struct.unpack_from("<4f", phys, 152)),
        "tyreTempI@368": list(struct.unpack_from("<4f", phys, 368)),
        "tyreTempM@384": list(struct.unpack_from("<4f", phys, 384)),
        "tyreTempO@400": list(struct.unpack_from("<4f", phys, 400)),
        "wheelLoad@72": list(struct.unpack_from("<4f", phys, 72)),
        "speedKmh@28": struct.unpack_from("<f", phys, 28)[0],
        "rpm@20": struct.unpack_from("<i", phys, 20)[0],
        "gear@16": struct.unpack_from("<i", phys, 16)[0],
        "gas@4": struct.unpack_from("<f", phys, 4)[0],
        "brake@8": struct.unpack_from("<f", phys, 8)[0],
        "steerAngle@24": struct.unpack_from("<f", phys, 24)[0],
        "tyreContactNormal@468": [
            list(struct.unpack_from("<3f", phys, 468 + 12 * w)) for w in range(4)
        ],
    }


def take_snapshot(label: str) -> dict:
    phys_raw = attach(PHYSICS_TAG, PHYSICS_BYTES)
    grap_raw = attach(GRAPHICS_TAG, GRAPHICS_BYTES)
    stat_raw = attach(STATIC_TAG, STATIC_BYTES)

    snap = {
        "label": label,
        "physics_known_offsets": known_offsets_dump(phys_raw),
        "physics_struct": parse_struct(phys_raw[:800], _SPageFilePhysics),
        "graphics_struct": parse_struct(grap_raw[:1588], _SPageFileGraphic),
        "static_struct": parse_struct(stat_raw[:820], _SPageFileStatic),
        "physics_scan_hints": scan_physics_floats(phys_raw),
    }
    return snap


def print_snapshot(snap: dict) -> None:
    print(f"=== snapshot: {snap['label']} ===\n")

    print("known offsets (the fields we suspect aren't published):")
    for k, v in snap["physics_known_offsets"].items():
        print(f"  {k:25s} = {v}")
    print()

    # Highlight scan hints in the missing-field ranges.
    print(f"physics scan: {len(snap['physics_scan_hints'])} non-zero plausible values "
          "(filtering)")
    rad_candidates = [h for h in snap["physics_scan_hints"]
                      if "rad?" in h["could_be"]]
    metres_candidates = [h for h in snap["physics_scan_hints"]
                          if "metres?" in h["could_be"]]
    temp_candidates = [h for h in snap["physics_scan_hints"]
                        if "degC?" in h["could_be"] or "Kelvin?" in h["could_be"]]

    print(f"\n  candidate camberRAD (small radians, |v|<=0.20): "
          f"{len(rad_candidates)} hits")
    for h in rad_candidates[:30]:
        print(f"    @{h['offset']:4d}  {h['value']:+.5f}  ({','.join(h['could_be'])})")

    print(f"\n  candidate ride height / suspension (metres 0.001..0.3): "
          f"{len(metres_candidates)} hits")
    for h in metres_candidates[:30]:
        print(f"    @{h['offset']:4d}  {h['value']:+.5f}  ({','.join(h['could_be'])})")

    print(f"\n  candidate temperatures (50..130 C or 280..420 K): "
          f"{len(temp_candidates)} hits")
    for h in temp_candidates[:40]:
        print(f"    @{h['offset']:4d}  {h['value']:8.2f}  ({','.join(h['could_be'])})")


def diff_snapshots(parked: dict, driving: dict) -> None:
    print("=== diff: parked vs driving ===\n")

    pk_known = parked["physics_known_offsets"]
    dr_known = driving["physics_known_offsets"]
    print("known offsets — only differences:")
    for k in pk_known:
        if pk_known[k] != dr_known.get(k):
            print(f"  {k}")
            print(f"    parked  = {pk_known[k]}")
            print(f"    driving = {dr_known[k]}")
    print()

    pk_hints = {h["offset"]: h["value"] for h in parked["physics_scan_hints"]}
    dr_hints = {h["offset"]: h["value"] for h in driving["physics_scan_hints"]}

    new_in_driving = sorted(set(dr_hints) - set(pk_hints))
    print(f"\nphysics offsets that are non-zero only when driving: "
          f"{len(new_in_driving)}")
    for off in new_in_driving[:40]:
        v = dr_hints[off]
        tags = next(h["could_be"] for h in driving["physics_scan_hints"]
                    if h["offset"] == off)
        print(f"  @{off:4d}  {v:+.4f}  ({','.join(tags)})")

    changed = []
    for off in sorted(set(pk_hints) & set(dr_hints)):
        if abs(pk_hints[off] - dr_hints[off]) > max(0.001, abs(pk_hints[off]) * 0.01):
            changed.append((off, pk_hints[off], dr_hints[off]))
    print(f"\nphysics offsets that changed >1% between snapshots: {len(changed)}")
    for off, pv, dv in changed[:40]:
        tags = next(h["could_be"] for h in driving["physics_scan_hints"]
                    if h["offset"] == off)
        print(f"  @{off:4d}  parked {pv:+.4f}  ->  driving {dv:+.4f}  "
              f"({','.join(tags)})")


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in ("parked", "driving", "diff"):
        print(__doc__)
        return 2

    label = argv[1]
    snap_dir = Path("tools/_acrally_snapshots")
    snap_dir.mkdir(parents=True, exist_ok=True)

    if label == "diff":
        parked_p = snap_dir / "parked.json"
        driving_p = snap_dir / "driving.json"
        if not parked_p.exists() or not driving_p.exists():
            print("need both parked.json and driving.json — re-run with each label first.")
            return 1
        diff_snapshots(json.loads(parked_p.read_text()),
                       json.loads(driving_p.read_text()))
        return 0

    snap = take_snapshot(label)
    out = snap_dir / f"{label}.json"
    out.write_text(json.dumps(snap, default=str, indent=2))
    print(f"wrote {out}\n")
    print_snapshot(snap)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
