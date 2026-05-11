from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from ..colors import Colors
from ..fonts import label_font
from ..sources.detect import detect_running_game


class DetectionView(QWidget):
    """Full-screen 'Detecting AC Environment...' poller shown before the
    countdown when ``--source auto`` is in effect.

    Polls every :attr:`POLL_MS` ms; on first hit it emits
    :attr:`detected` with the source name and hides itself.
    """

    detected = Signal(str)

    POLL_MS = 500
    MESSAGE = "Detecting AC Environment..."

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        # Stay hidden until start() — child widgets are otherwise made
        # visible when the top-level window shows, which would paint
        # the message before we begin polling.
        self.hide()
        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_MS)
        # pylint: disable-next=no-member  # QTimer.timeout is a PySide6 Signal
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self.show()
        self.update()
        # Probe once immediately so an already-running game is picked up
        # without waiting a full POLL_MS for the first tick.
        QTimer.singleShot(0, self._tick)
        self._timer.start()

    def _tick(self) -> None:
        name = detect_running_game()
        if name is None:
            return
        self._timer.stop()
        self.hide()
        # pylint: disable-next=no-member  # detected is a PySide6 Signal
        self.detected.emit(name)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        # Scale text to a fraction of the smaller side so the message
        # stays readable across portrait/landscape/split-screen layouts.
        size = max(18, min(self.width(), self.height()) // 28)
        p.setFont(label_font(size))
        p.setPen(Colors.white)
        p.drawText(self.rect(), Qt.AlignCenter, self.MESSAGE)
        p.end()
