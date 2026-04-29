from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QPainter, QPixmap


def _img_dir() -> Path:
    """Locate ``resources/img`` whether running from source or frozen.

    PyInstaller sets ``sys.frozen`` and unpacks bundled data under
    ``sys._MEIPASS`` (a temp dir for one-file mode, the dist folder for
    one-folder). The build script copies ``resources/img`` into that
    same relative path. From a source checkout we fall back to the
    project tree (``parents[2]`` of this file).
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return base / "resources" / "img"
    return Path(__file__).resolve().parents[2] / "resources" / "img"


_IMG_DIR = _img_dir()

# Per-icon source pixmaps loaded on demand (full-resolution alpha masks).
_source_cache: dict[str, QPixmap] = {}

# Per-icon scaled greyscale masks, keyed by (name, w, h).
_scaled_cache: dict[tuple[str, int, int], QPixmap] = {}


def source_pixmap(name: str) -> QPixmap:
    """Return the full-resolution white-on-transparent source pixmap.

    Lazily loaded so the QApplication can be constructed before any pixmap
    is touched (QPixmap requires a running QGuiApplication).
    """
    pm = _source_cache.get(name)
    if pm is None:
        path = _IMG_DIR / f"{name}.png"
        pm = QPixmap(str(path))
        if pm.isNull():
            raise FileNotFoundError(f"missing image resource: {path}")
        _source_cache[name] = pm
    return pm


def _scaled_mask(name: str, w: int, h: int) -> QPixmap:
    key = (name, max(1, int(w)), max(1, int(h)))
    pm = _scaled_cache.get(key)
    if pm is None:
        src = source_pixmap(name)
        pm = src.scaled(
            QSize(key[1], key[2]),
            Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation,
        )
        _scaled_cache[key] = pm
    return pm


def tinted(name: str, w: int, h: int, color: QColor) -> QPixmap:
    """Return a copy of the named icon, scaled to (w, h) and tinted with color.

    Recomputed per call (tints change every frame), but the scaled mask is
    cached so the cost is just a small alpha composite.
    """
    mask = _scaled_mask(name, w, h)
    out = QPixmap(mask.size())
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.drawPixmap(0, 0, mask)
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(out.rect(), color)
    p.end()
    return out
