from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from ..colors import Colors
from ..fonts import label_font
from .draggable import DraggableWidget


# Scale factors applied on top of the auto-detected resolution multiplier.
# Index 2 ("M") is 1.0 — i.e. matches the original auto-picked size.
SIZE_FACTORS: tuple[float, ...] = (0.5, 0.75, 1.0, 1.25, 1.5)
SIZE_LABELS: tuple[str, ...] = ("XS", "S", "M", "L", "XL")
DEFAULT_SIZE_INDEX = 2


class SizeButton(DraggableWidget):
    """Floating button that cycles widget sizes. Each click advances
    through XS → S → M → L → XL → XS… emitting ``size_changed`` with the
    new index. Like the reset button it's draggable but never closable.
    """

    size_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, closable=False)
        self._index = DEFAULT_SIZE_INDEX
        self.setFixedSize(36, 36)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(self._cycle)

    @property
    def index(self) -> int:
        return self._index

    def set_index(self, idx: int) -> None:
        idx = max(0, min(len(SIZE_FACTORS) - 1, int(idx)))
        if idx != self._index:
            self._index = idx
            self.update()

    def _cycle(self) -> None:
        new_idx = (self._index + 1) % len(SIZE_FACTORS)
        self._index = new_idx
        self.update()
        self.size_changed.emit(new_idx)

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
        # XS/S/M/L/XL label sized to fit comfortably inside the disc.
        glyph = SIZE_LABELS[self._index]
        scale = 0.5 if len(glyph) == 2 else 0.6
        p.setFont(label_font(int(self.height() * scale)))
        p.drawText(self.rect(), Qt.AlignCenter, glyph)
        p.end()
