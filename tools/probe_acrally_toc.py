"""List what AC Rally ships inside its UE5 IoStore containers.

AC Rally's containers are ``Compressed|Indexed`` with no encryption key
(see ``probe_game_packages.py``), so the directory index — the mount
point, the entry counts and the string table every path is built from —
reads in plain text without a key.

This tool reads *names only*; it does not extract or decode any asset.
It exists to answer whether per-car physics is stored in something we
could parse (AC1-style ``.ini`` / ``.lut``) or in engine-native assets.
As of the 2026-08 build the answer is the latter: 41 k path components,
every payload ``.uasset`` / ``.ubulk`` / ``.umap``, with per-car data in
``DA_*Presets`` data assets. Reading those needs Oodle decompression plus
a UE5 property parser bound to the game's struct layouts — a much bigger
job than AC1's ACD, and one that breaks on engine updates.

Usage:

    python tools/probe_acrally_toc.py [path-to-Paks-dir]
"""
from __future__ import annotations

import os
import re
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

# pylint: disable=wrong-import-position
from live_telemetry_evo.sources.ac1_install import (  # noqa: E402
    _steam_library_candidates)

TOC_MAGIC = b"-==--==--==--==-"
FLAGS = ((1, "Compressed"), (2, "ENCRYPTED"), (4, "Signed"), (8, "Indexed"))
RELATIVE = os.path.join("Assetto Corsa Rally", "acr", "Content", "Paks")


def read_header(fh) -> dict:
    """FIoStoreTocHeader — 144 bytes, layout stable since UE5.0."""
    h = fh.read(144)
    if h[:16] != TOC_MAGIC:
        raise ValueError("not an IoStore toc")
    f = {"version": h[0x10]}
    (f["header_size"], f["entries"], f["blocks"], f["block_entry_size"],
     f["method_count"], f["method_len"], f["compression_block_size"],
     f["dir_index_size"], f["partitions"]) = struct.unpack_from("<9I", h, 0x14)
    f["key_guid"] = h[0x40:0x50]
    f["flags"] = h[0x50]
    f["hash_seeds"] = struct.unpack_from("<I", h, 0x54)[0]
    f["no_hash_chunks"] = struct.unpack_from("<I", h, 0x60)[0]
    return f


def dir_index_offset(f: dict) -> int:
    """Everything between the header and the directory index is
    fixed-width, so the offset is arithmetic rather than a seek chain."""
    off = f["header_size"]
    off += f["entries"] * 12               # FIoChunkId
    off += f["entries"] * 10               # FIoOffsetAndLength
    if f["version"] >= 3:                  # PerfectHash
        off += f["hash_seeds"] * 4
    if f["version"] >= 4:                  # PerfectHashWithOverflow
        off += f["no_hash_chunks"] * 4
    off += f["blocks"] * f["block_entry_size"]
    off += f["method_count"] * f["method_len"]
    return off


def parse_dir_index(blob: bytes) -> tuple[str, int, list[str]]:
    """FIoDirectoryIndexResource: mount point, directory entries, file
    entries, then the string table the entries index into."""
    pos = 0
    (n,) = struct.unpack_from("<i", blob, pos)
    pos += 4
    mount = blob[pos:pos + n - 1].decode("utf-8", "replace") if n > 0 else ""
    pos += abs(n) * (2 if n < 0 else 1)
    (dir_count,) = struct.unpack_from("<I", blob, pos)
    pos += 4 + dir_count * 16
    (file_count,) = struct.unpack_from("<I", blob, pos)
    pos += 4 + file_count * 12
    (str_count,) = struct.unpack_from("<I", blob, pos)
    pos += 4
    strings: list[str] = []
    for _ in range(str_count):
        (n,) = struct.unpack_from("<i", blob, pos)
        pos += 4
        if n >= 0:
            strings.append(blob[pos:pos + n - 1].decode("utf-8", "replace"))
            pos += n
        else:
            strings.append(blob[pos:pos + (-n * 2) - 2].decode("utf-16-le",
                                                               "replace"))
            pos += -n * 2
    return mount, file_count, strings


def flag_names(flags: int) -> str:
    return "|".join(name for bit, name in FLAGS if flags & bit) or "None"


def find_paks() -> str | None:
    if len(sys.argv) > 1:
        return sys.argv[1]
    for root in list(_steam_library_candidates()) + [r"C:\Program Files (x86)\Steam"]:
        path = os.path.join(root, "steamapps", "common", RELATIVE)
        if os.path.isdir(path):
            return path
    return None


def main() -> int:
    paks = find_paks()
    if paks is None:
        print("AC Rally not found — pass the Paks directory as an argument")
        return 1

    names: set[str] = set()
    files = encrypted = 0
    for name in sorted(os.listdir(paks)):
        if not name.endswith(".utoc"):
            continue
        with open(os.path.join(paks, name), "rb") as fh:
            try:
                f = read_header(fh)
            except (ValueError, struct.error):
                continue
            if f["flags"] & 2 or f["key_guid"] != bytes(16):
                encrypted += 1
                continue
            if not f["dir_index_size"]:
                continue
            fh.seek(dir_index_offset(f))
            blob = fh.read(f["dir_index_size"])
        try:
            _, file_count, strings = parse_dir_index(blob)
        except (struct.error, IndexError) as exc:
            print(f"{name}: directory index parse failed ({exc})")
            continue
        files += file_count
        names.update(strings)
        print(f"{name:<34} flags={flag_names(f['flags']):<22} "
              f"files={file_count}")

    print(f"\ncontainers with an encryption key: {encrypted}")
    print(f"files indexed: {files}")
    print(f"unique path components: {len(names)}")

    exts: dict[str, int] = {}
    for n in names:
        if "." in n:
            ext = n.rsplit(".", 1)[-1].lower()
            exts[ext] = exts.get(ext, 0) + 1
    print("\nextensions:")
    for ext, count in sorted(exts.items(), key=lambda kv: -kv[1])[:10]:
        print(f"   .{ext:<16} {count}")
    parseable = sum(c for e, c in exts.items()
                    if e in ("ini", "lut", "json", "csv", "xml"))
    print(f"\nAC1-style parseable data files (.ini/.lut/.json/.csv/.xml): "
          f"{parseable}")

    rx = re.compile(r"setup|preset|physic|tyre|tire|suspension", re.I)
    hits = sorted(n for n in names if rx.search(n) and n.startswith(("DA_", "DT_")))
    print(f"\nper-car data assets (DA_/DT_ matching physics/setup): {len(hits)}")
    for h in hits[:12]:
        print(f"   {h}")
    if len(hits) > 12:
        print(f"   … {len(hits) - 12} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
