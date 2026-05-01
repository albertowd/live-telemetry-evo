"""Download MDI icons from Iconify and rasterise them to PNG.

Drops 256x256 PNGs into ``resources/img/`` with the file names the
engine widget expects (``car-brake-abs.png`` etc.). Uses Iconify's SVG
endpoint and PySide6's ``QSvgRenderer`` — no new dependencies.

Usage:

    .venv/Scripts/python fetch_icons.py            # fetch any missing
    .venv/Scripts/python fetch_icons.py --force    # re-fetch all
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

from PySide6.QtCore import QByteArray
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "resources" / "img"
SIZE = 256
ICONIFY_URL = "https://api.iconify.design/mdi/{name}.svg"

# Engine-widget MDI mapping. Keep in sync with ``_draw_aids`` /
# ``_draw_readouts`` in ``src/overlay/widgets/engine_view.py``.
ICONS: tuple[str, ...] = (
    # Phase 1 — driver-aid / status chips
    "car-speed-limiter",        # PIT
    "car-traction-control",     # TC
    "car-brake-abs",            # ABS
    "car-esp",                  # ESC
    "rocket-launch",            # LC
    "car-cruise-control",       # DRS
    "battery-charging",         # ERS
    "alert",                    # WW (wrong way)
    "flag-remove",              # INV (invalid lap)
    "flag-checkered",           # LAST
    # Phase 2 — analog readouts
    "water-thermometer",        # WAT
    "oil-temperature",          # OIL
    "oil-level",                # OPR (oil pressure)
    "gas-station",              # FPR (fuel pressure)
    "smoke",                    # EXH
    "car-battery",              # BAT
    "fuel",                     # FUEL
    "car-brake-parking",        # BIAS
)


_UA = "Mozilla/5.0 (compatible; ac-evo-overlay-icon-fetcher)"


def fetch(name: str) -> bytes:
    """Download one SVG from Iconify; raises on HTTP failure.

    Iconify's edge rejects Python's default ``Python-urllib`` UA with
    HTTP 403, so we send a generic browser-style identifier.
    """
    req = urllib.request.Request(
        ICONIFY_URL.format(name=name),
        headers={"User-Agent": _UA},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()


def rasterise(svg_bytes: bytes, out_path: Path, size: int) -> None:
    """Render an SVG to a transparent-background PNG of (size x size).

    The engine widget's ``tinted()`` pipeline keys on the alpha channel,
    not the RGB, so MDI's default black fill is fine — every chip gets
    re-coloured at paint time anyway.
    """
    renderer = QSvgRenderer(QByteArray(svg_bytes))
    if not renderer.isValid():
        raise RuntimeError(f"invalid SVG (Iconify returned an empty doc?)")
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(0)  # transparent
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    image.save(str(out_path), "PNG")


def main(argv: list[str]) -> int:
    force = "--force" in argv

    # QImage + QPainter need a QGuiApplication; QApplication is a superset
    # and constructs cleanly in a headless dev shell.
    QApplication(sys.argv)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    failures: list[tuple[str, str]] = []
    for name in ICONS:
        out = OUT_DIR / f"{name}.png"
        if out.exists() and not force:
            print(f"[skip] {out.name} (already present; pass --force to refresh)")
            continue
        try:
            svg = fetch(name)
            rasterise(svg, out, SIZE)
            print(f"[ok]   {out.name}")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            failures.append((name, str(exc)))
            print(f"[fail] {name}: {exc}", file=sys.stderr)

    if failures:
        print(
            f"\n{len(failures)} icon(s) failed — likely an MDI rename. "
            "Pick an alternative on Pictogrammers and update both this list "
            "and engine_view.py:",
            file=sys.stderr,
        )
        for name, _ in failures:
            print(f"  https://pictogrammers.com/library/mdi/icon/{name}/",
                  file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
