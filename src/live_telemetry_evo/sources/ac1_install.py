"""Locate the Assetto Corsa 1 install on disk so we can read per-car
``data.acd`` files.

Resolution order (first hit wins):

1. ``LT_AC_PATH`` environment variable — escape hatch for non-standard
   installs (custom drives, portable copies, etc.). Points at the AC
   root directory (the one containing ``content/``).
2. Steam library scan. We pull ``HKCU\\Software\\Valve\\Steam\\SteamPath``
   from the registry, parse ``steamapps/libraryfolders.vdf`` for every
   registered library, and pick the first one whose ``apps`` block
   declares Steam app ``244210`` (the AC1 store ID).
3. ``C:\\Program Files (x86)\\Steam\\steamapps\\common\\assettocorsa``
   final fallback for the common case where the user hasn't moved
   their default Steam library.

Returns the path to the car directory (``<install>/content/cars/<car>``)
when ``car_name`` is supplied, or the install root otherwise.
"""
from __future__ import annotations

import os
import re
import sys


_AC1_STEAM_APPID = "244210"
_DEFAULT_AC_PATH = r"C:\Program Files (x86)\Steam\steamapps\common\assettocorsa"


def find_ac_install() -> str | None:
    """Return the absolute path to the AC1 install root, or ``None``."""
    override = os.environ.get("LT_AC_PATH")
    if override and _looks_like_ac_install(override):
        return os.path.abspath(override)

    for candidate in _steam_library_candidates():
        path = os.path.join(candidate, "steamapps", "common", "assettocorsa")
        if _looks_like_ac_install(path):
            return os.path.abspath(path)

    if _looks_like_ac_install(_DEFAULT_AC_PATH):
        return os.path.abspath(_DEFAULT_AC_PATH)
    return None


def find_car_dir(car_name: str) -> str | None:
    """Return the absolute path to ``<install>/content/cars/<car_name>``,
    or ``None`` if either the install or the car directory is missing."""
    root = find_ac_install()
    if root is None:
        return None
    car_dir = os.path.join(root, "content", "cars", car_name)
    return car_dir if os.path.isdir(car_dir) else None


def _looks_like_ac_install(path: str) -> bool:
    return bool(path) and os.path.isdir(os.path.join(path, "content", "cars"))


def _steam_library_candidates() -> list[str]:
    """Return Steam library roots that have AC1 (app 244210) installed.

    Walks ``libraryfolders.vdf`` looking for a library whose ``apps``
    block declares the AC1 store ID. Falls back to "every library we
    find" if no library explicitly claims AC1 — older Steam versions
    didn't write the apps section.
    """
    steam_root = _read_steam_root()
    if steam_root is None:
        return []
    vdf = os.path.join(steam_root, "steamapps", "libraryfolders.vdf")
    if not os.path.isfile(vdf):
        return [steam_root]
    try:
        with open(vdf, "r", encoding="utf-8", errors="replace") as raw:
            content = raw.read()
    except OSError:
        return [steam_root]

    libs = _parse_library_folders(content)
    claimed = [path for path, apps in libs if _AC1_STEAM_APPID in apps]
    if claimed:
        return claimed
    # No apps section listed AC1 explicitly — return every library so
    # the caller can probe each for content/cars.
    return [path for path, _ in libs]


def _read_steam_root() -> str | None:
    if sys.platform != "win32":
        return None
    # winreg is a stdlib module that only exists on Windows; importing
    # inside the function keeps this file importable on dev machines.
    import winreg  # pylint: disable=import-outside-toplevel
    for hive, subkey in (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
    ):
        try:
            with winreg.OpenKey(hive, subkey) as key:
                value = _read_steam_path_value(key)
                if value:
                    return value
        except OSError:
            continue
    return None


def _read_steam_path_value(key: object) -> str | None:
    import winreg  # pylint: disable=import-outside-toplevel
    for value_name in ("SteamPath", "InstallPath"):
        try:
            value, _ = winreg.QueryValueEx(key, value_name)  # type: ignore[arg-type]
            if value:
                return str(value).replace("/", "\\")
        except OSError:
            continue
    return None


def _parse_library_folders(content: str) -> list[tuple[str, set[str]]]:
    """Return a list of ``(library_path, {app_ids})`` entries from a
    Steam ``libraryfolders.vdf`` blob.

    Steam's KeyValues format is nested, but we only need two scalar
    fields per library (``path`` and the keys inside ``apps``). A pair
    of regexes is more reliable here than a half-baked VDF parser —
    the format is line-oriented and the keys we want are unique.
    """
    libs: list[tuple[str, set[str]]] = []
    # Each top-level library is keyed by an integer index. We split on
    # those headers so we can collect path + apps within one block.
    blocks = re.split(r'^\s*"\d+"\s*$', content, flags=re.MULTILINE)
    for block in blocks[1:]:
        path_match = re.search(r'"path"\s*"([^"]+)"', block)
        if not path_match:
            continue
        path = path_match.group(1).replace("\\\\", "\\")
        apps_match = re.search(r'"apps"\s*\{([^}]*)\}', block, flags=re.DOTALL)
        apps: set[str] = set()
        if apps_match:
            for key_match in re.finditer(r'"(\d+)"\s*"\d+"', apps_match.group(1)):
                apps.add(key_match.group(1))
        libs.append((path, apps))
    return libs


__all__ = ["find_ac_install", "find_car_dir"]
