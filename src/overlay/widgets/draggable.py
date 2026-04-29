from __future__ import annotations

from PySide6.QtCore import Qt, QPoint, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter
from PySide6.QtWidgets import QWidget

from ..colors import Colors
from ..fonts import label_font


_CLICK_DRAG_THRESHOLD_PX = 4


class _IconButton(QWidget):
    """Tiny click target painted with a single glyph. Emits ``clicked`` on
    a press-then-release inside its own rect; consumes the events so the
    parent's drag logic never starts."""

    clicked = Signal()

    def __init__(self, glyph: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._glyph = glyph
        self._pressed = False
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        # Subtle dark disc behind the glyph so it's findable on bright
        # backgrounds without being visually heavy on dark ones.
        bg = QColor(0, 0, 0)
        bg.setAlphaF(0.35)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawEllipse(self.rect())
        # Glyph itself, sized to 70 % of the widget height.
        p.setPen(Colors.white)
        p.setFont(label_font(max(10, int(self.height() * 0.7))))
        p.drawText(self.rect(), Qt.AlignCenter, self._glyph)
        p.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._pressed = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._pressed:
            self._pressed = False
            if self.rect().contains(event.position().toPoint()):
                self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class DraggableWidget(QWidget):
    """Base for the overlay's child widgets. Left-button drag inside the
    parent rect, with the final position emitted on release. A small ``×``
    button in the top-right corner emits ``closed`` (skipped when
    ``closable=False``). A no-drag click anywhere else emits ``clicked``,
    used by the reset button.
    """

    moved_to = Signal(int, int)
    clicked = Signal()
    closed = Signal()

    def __init__(self, parent: QWidget | None = None, *, closable: bool = True) -> None:
        super().__init__(parent)
        self._drag_offset: QPoint | None = None
        self._press_pos: QPoint | None = None
        self._dragged = False
        self._close_btn: _IconButton | None = None
        if closable:
            self._close_btn = _IconButton("×", self)
            self._close_btn.clicked.connect(self.closed)
            self._close_btn.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._close_btn is not None:
            # Scale the button with the widget but cap it so it never
            # disappears on small widgets or balloons on huge ones.
            size = max(16, min(28, self.height() // 8))
            self._close_btn.resize(size, size)
            self._close_btn.move(self.width() - size - 4, 4)
            self._close_btn.raise_()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.position().toPoint()
            self._press_pos = event.position().toPoint()
            self._dragged = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            here = event.position().toPoint()
            if not self._dragged and self._press_pos is not None:
                delta = here - self._press_pos
                if abs(delta.x()) + abs(delta.y()) >= _CLICK_DRAG_THRESHOLD_PX:
                    self._dragged = True
            if self._dragged:
                target = self.mapToParent(here) - self._drag_offset
                parent = self.parentWidget()
                if parent is not None:
                    max_x = max(0, parent.width() - self.width())
                    max_y = max(0, parent.height() - self.height())
                    target.setX(max(0, min(max_x, target.x())))
                    target.setY(max(0, min(max_y, target.y())))
                self.move(target)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._drag_offset is not None:
            was_drag = self._dragged
            self._drag_offset = None
            self._press_pos = None
            self._dragged = False
            if was_drag:
                self.moved_to.emit(self.x(), self.y())
            else:
                self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)
