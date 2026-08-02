"""Identify how each installed AC title packages its car data, and whether
that package can be read without a key.

Answers the question "can we do for ACC / AC Rally / AC Evo what
``ac1_acd.py`` does for AC1?" — read per-car physics (torque curves, ideal
pressures, thermal curves) straight off disk.

Findings as of the 2026-08 builds (re-run to re-check after a patch):

* **ACC** — UE4 pak v11 with ``bEncryptedIndex = 1``. The file index is
  AES-encrypted, so nothing can be enumerated, let alone extracted. The
  per-car data we wanted (dash brake-bias offset, steering lock) is in
  the shipped documentation instead — see ``gen_acc_car_table.py``.
* **AC Rally** — UE5 IoStore. Containers are ``Compressed|Indexed`` with
  a zero encryption-key GUID, so the directory index reads in plain text
  (see ``probe_acrally_toc.py``). Every payload is ``.uasset`` /
  ``.ubulk`` though: extracting values needs Oodle plus a UE5 property
  parser tied to the game's own struct layouts.
* **AC Evo** — one 67 GB ``content.kspkg`` in an undocumented Kunos
  format. No plaintext magic; long padding runs repeat on an 8-byte
  period while the head is structured-but-obfuscated, which reads as an
  8-byte block cipher in ECB. Closed for our purposes.

Usage:

    python tools/probe_game_packages.py [steam-library-root]

Without an argument the script scans the Steam libraries registered on
this machine.
"""
from __future__ import annotations

import collections
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

# pylint: disable=wrong-import-position
from live_telemetry_evo.sources.ac1_install import (  # noqa: E402
    _steam_library_candidates)

UE_PAK_MAGIC = 0x5A6F12E1
GAME_DIRS = {
    "ACC": os.path.join("Assetto Corsa Competizione", "AC2", "Content",
                        "Paks", "AC2-WindowsNoEditor.pak"),
    "AC Rally": os.path.join("Assetto Corsa Rally", "acr", "Content", "Paks"),
    "AC Evo": os.path.join("Assetto Corsa EVO", "content.kspkg"),
}


def library_roots(argv: list[str]) -> list[str]:
    if len(argv) > 1:
        return [argv[1]]
    roots = list(_steam_library_candidates())
    # _steam_library_candidates filters to libraries that declare AC1;
    # the newer titles may live elsewhere, so add the plain default too.
    roots.append(r"C:\Program Files (x86)\Steam")
    return roots


def find_game(roots: list[str], relative: str) -> str | None:
    for root in roots:
        path = os.path.join(root, "steamapps", "common", relative)
        if os.path.exists(path):
            return path
    return None


def hexdump(data: bytes, base: int = 0, width: int = 16) -> None:
    for off in range(0, len(data), width):
        chunk = data[off:off + width]
        hexs = " ".join(f"{b:02x}" for b in chunk).ljust(width * 3 - 1)
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"    {base + off:08x}  {hexs}  |{text}|")


def probe_ue_pak(path: str) -> None:
    """UE4 .pak footer: EncryptionKeyGuid, bEncryptedIndex, then magic."""
    print(f"\n=== ACC — UE4 pak: {os.path.basename(path)} "
          f"({os.path.getsize(path) / 1024**3:.1f} GB)")
    with open(path, "rb") as fh:
        fh.seek(-221, os.SEEK_END)
        foot = fh.read(221)
    guid, encrypted = foot[:16], foot[16]
    magic, version = struct.unpack_from("<II", foot, 17)
    if magic != UE_PAK_MAGIC:
        print("    unexpected footer layout — pak version changed?")
        return
    print(f"    version           = {version}")
    print(f"    EncryptionKeyGuid = {guid.hex()}")
    print(f"    bEncryptedIndex   = {encrypted}"
          f"  -> {'INDEX IS AES-ENCRYPTED' if encrypted else 'index readable'}")


def probe_iostore_dir(path: str) -> None:
    """UE5 IoStore: the container flags say whether a key is needed."""
    print(f"\n=== AC Rally — UE5 IoStore: {path}")
    tocs = sorted(f for f in os.listdir(path) if f.endswith(".utoc"))
    encrypted = 0
    for name in tocs:
        with open(os.path.join(path, name), "rb") as fh:
            head = fh.read(144)
        if head[:16] != b"-==--==--==--==-":
            continue
        flags, guid = head[0x50], head[0x40:0x50]
        if flags & 0x02 or guid != bytes(16):
            encrypted += 1
    print(f"    containers        = {len(tocs)}")
    print(f"    encrypted         = {encrypted}"
          f"  -> {'keys needed' if encrypted else 'all readable, see probe_acrally_toc.py'}")


def probe_kspkg(path: str) -> None:
    """AC Evo's own container — undocumented, look for structure."""
    size = os.path.getsize(path)
    print(f"\n=== AC Evo — {os.path.basename(path)} ({size / 1024**3:.1f} GB)")
    with open(path, "rb") as fh:
        head = fh.read(1 << 20)
        fh.seek(-65536, os.SEEK_END)
        tail = fh.read(65536)
    print("    first 64 bytes (no plaintext magic):")
    hexdump(head[:64])
    period = next((p for p in range(1, 65)
                   if all(tail[i] == tail[i + p] for i in range(4096))), None)
    if period:
        print(f"    tail padding repeats every {period} bytes: "
              f"{tail[:period].hex()}")
        print("    -> identical plaintext blocks map to identical ciphertext:"
              " block cipher in ECB")
    counts = collections.Counter(head)
    top = counts.most_common(1)[0]
    print(f"    head histogram: {len(counts)} distinct values, most common "
          f"0x{top[0]:02x} x{top[1]} (uniform would be ~{len(head) // 256})")


def main() -> int:
    roots = library_roots(sys.argv)
    print(f"Steam libraries: {roots}")
    acc = find_game(roots, GAME_DIRS["ACC"])
    rally = find_game(roots, GAME_DIRS["AC Rally"])
    evo = find_game(roots, GAME_DIRS["AC Evo"])
    if acc:
        probe_ue_pak(acc)
    if rally:
        probe_iostore_dir(rally)
    if evo:
        probe_kspkg(evo)
    if not any((acc, rally, evo)):
        print("none of the newer titles found in the scanned libraries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
