from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from ..colors import Colors
from ..fonts import label_font
from .draggable import DraggableWidget


class ResetButton(DraggableWidget):
    """Floating reset button. Draggable but not closable so the user
    can always restore the layout if every other widget got moved off
    a different monitor or hidden. ``clicked`` fires on a press-release
    that didn't drag."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, closable=False)
        self.setFixedSize(36, 36)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        bg = QColor(0, 0, 0)
        bg.setAlphaF(0.55)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawEllipse(self.rect())
        p.setPen(Colors.white)
        p.setFont(label_font(int(self.height() * 0.6)))
        p.drawText(self.rect(), Qt.AlignCenter, "↺")  # ↺
        p.end()
