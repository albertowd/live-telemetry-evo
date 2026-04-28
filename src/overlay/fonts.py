from __future__ import annotations

from PySide6.QtGui import QFont


def label_font(pixel_size: int, bold: bool = True) -> QFont:
    """Build a QFont with an explicit family chain.

    Default QFont() resolves to a stub font in some rendering paths
    (offscreen, headless) which renders ASCII as tofu boxes. Naming the
    family explicitly avoids that on Windows/macOS/Linux.
    """
    font = QFont()
    font.setFamilies(["Segoe UI", "Arial", "DejaVu Sans", "Helvetica", "sans-serif"])
    font.setPixelSize(pixel_size)
    font.setBold(bold)
    return font
