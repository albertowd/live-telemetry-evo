from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from ..colors import Colors
from ..fonts import label_font


class CountdownView(QWidget):
    """Full-screen countdown shown before the telemetry widgets appear.

    Renders a single large digit centred on the overlay, ticking down from
    DURATION_S to 1. When the timer expires, the widget hides itself and
    emits :attr:`finished` so the caller can show the telemetry widgets.
    """

    finished = Signal()

    DURATION_S = 5

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._remaining = self.DURATION_S
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        # Stay hidden until start() — otherwise child widgets become
        # visible as soon as the top-level window shows, which would
        # paint the initial "5" on top of the detection screen.
        self.hide()
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        # pylint: disable-next=no-member  # QTimer.timeout is a PySide6 Signal
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._remaining = self.DURATION_S
        self.show()
        self._timer.start()
        self.update()

    def _tick(self) -> None:
        self._remaining -= 1
        if self._remaining <= 0:
            self._timer.stop()
            self.hide()
            # pylint: disable-next=no-member  # finished is a PySide6 Signal
            self.finished.emit()
            return
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        # Scale the digit to a third of the smaller side so it stays
        # readable on portrait or split-screen layouts without overflowing.
        size = max(64, min(self.width(), self.height()) // 3)
        p.setFont(label_font(size))
        p.setPen(Colors.white)
        p.drawText(self.rect(), Qt.AlignCenter, str(self._remaining))
        p.end()
