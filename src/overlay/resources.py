from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QPainter, QPixmap


# resources/img sits next to src/, so go up two from this file.
_IMG_DIR = Path(__file__).resolve().parents[2] / "resources" / "img"

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
