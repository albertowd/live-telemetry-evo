"""Build a portable single-file ``.exe`` of the overlay.

Reads the version from ``pyproject.toml``, converts ``resources/icon.png``
to ``.ico`` if Pillow is available, then invokes PyInstaller in one-file
windowed mode. Output: ``dist/ACEvoOverlay-<version>.exe``.

Usage:

    .venv/Scripts/python build.py

Requires ``pyinstaller`` and (for the icon) ``pillow`` in the active
environment::

    pip install pyinstaller pillow
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT_NAME = "ACEvoOverlay"
ICON_PNG = ROOT / "resources" / "icon.png"
ICON_ICO = ROOT / "resources" / "icon.ico"
RES_IMG_DIR = ROOT / "resources" / "img"
ENTRYPOINT = ROOT / "src" / "overlay" / "__main__.py"
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"


def _read_version() -> str:
    """Pull ``project.version`` from pyproject.toml without depending on
    ``tomllib`` (3.11+) — a regex over the line is enough for our flat
    metadata block and keeps the script working on Python 3.10."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^\s*version\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
    if not m:
        raise RuntimeError("could not parse 'version' from pyproject.toml")
    return m.group(1)


def _ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.stderr.write(
            "[build] PyInstaller is not installed. Run:\n"
            f"    {sys.executable} -m pip install pyinstaller\n"
        )
        sys.exit(1)


def _convert_icon() -> Path | None:
    """Build ``icon.ico`` from ``icon.png`` (or use a cached one).

    Returns the path to a usable ``.ico`` or ``None`` if no icon source
    exists. PyInstaller's ``--icon`` insists on ``.ico`` on Windows, so
    PNG can't be passed directly.
    """
    if ICON_ICO.exists() and ICON_PNG.exists():
        if ICON_ICO.stat().st_mtime >= ICON_PNG.stat().st_mtime:
            return ICON_ICO
    if ICON_ICO.exists() and not ICON_PNG.exists():
        return ICON_ICO
    if not ICON_PNG.exists():
        print(f"[build] no icon at {ICON_PNG} — building without one")
        return None
    try:
        from PIL import Image
    except ImportError:
        sys.stderr.write(
            "[build] Pillow is not installed (needed to convert icon.png -> .ico). Run:\n"
            f"    {sys.executable} -m pip install pillow\n"
            "  ...or pre-create resources/icon.ico yourself.\n"
        )
        sys.exit(1)
    img = Image.open(ICON_PNG)
    # Multi-resolution .ico so Windows picks an appropriate size for
    # taskbar / explorer / alt-tab.
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(ICON_ICO, format="ICO", sizes=sizes)
    print(f"[build] wrote {ICON_ICO}")
    return ICON_ICO


def main() -> int:
    _ensure_pyinstaller()
    version = _read_version()
    final_name = f"{PROJECT_NAME}-{version}"
    icon = _convert_icon()

    if not RES_IMG_DIR.is_dir():
        sys.stderr.write(f"[build] missing resources at {RES_IMG_DIR}\n")
        return 1
    if not ENTRYPOINT.is_file():
        sys.stderr.write(f"[build] missing entrypoint {ENTRYPOINT}\n")
        return 1

    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm",
        "--onefile",
        "--windowed",
        "--name", final_name,
        "--add-data", f"{RES_IMG_DIR};resources/img",
        "--paths", str(ROOT / "src"),
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR),
        "--specpath", str(BUILD_DIR),
    ]
    # Bundle the source PNG so the system-tray icon can load it at
    # runtime — the embedded .ico (--icon) only sets the EXE icon.
    if ICON_PNG.exists():
        cmd.extend(["--add-data", f"{ICON_PNG};resources"])
    if icon is not None:
        cmd.extend(["--icon", str(icon)])
    cmd.append(str(ENTRYPOINT))

    print(f"[build] {' '.join(cmd)}")
    rc = subprocess.call(cmd)
    if rc != 0:
        return rc

    out = DIST_DIR / f"{final_name}.exe"
    if not out.exists():
        sys.stderr.write(f"[build] expected {out}, but it wasn't produced\n")
        return 1
    print(f"[build] success → {out}  ({out.stat().st_size / (1024 * 1024):.1f} MB)")

    # PyInstaller drops a one-folder fallback alongside the onefile exe;
    # delete it so the dist directory holds only the redistributable.
    fallback_dir = DIST_DIR / final_name
    if fallback_dir.is_dir():
        shutil.rmtree(fallback_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
