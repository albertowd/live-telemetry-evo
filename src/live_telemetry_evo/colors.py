from __future__ import annotations

from PySide6.QtGui import QColor


def _c(r: float, g: float, b: float, a: float = 1.0) -> QColor:
    return QColor.fromRgbF(r, g, b, a)


class Colors:
    """Palette ported from the original AC plugin (lt_colors.py).

    Values are in 0..1 floats in the source; converted to QColor here.
    """

    black = _c(0.0, 0.0, 0.0)
    blue = _c(0.4, 0.596, 0.948)
    brown = _c(0.513, 0.360, 0.231)
    green = _c(0.235, 0.702, 0.443)
    red = _c(1.0, 0.270, 0.0)
    transparent = _c(0.0, 0.0, 0.0, 0.0)
    yellow = _c(0.941, 0.902, 0.549)
    white = _c(1.0, 1.0, 1.0)


def lerp_color(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor.fromRgbF(
        a.redF() + (b.redF() - a.redF()) * t,
        a.greenF() + (b.greenF() - a.greenF()) * t,
        a.blueF() + (b.blueF() - a.blueF()) * t,
        a.alphaF() + (b.alphaF() - a.alphaF()) * t,
    )
