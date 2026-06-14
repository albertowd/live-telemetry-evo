from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from ..colors import Colors
from ..fonts import label_font


# Pretty-printed names shown above the countdown digit. Keys mirror the
# source identifiers used by ``--source`` and by ``make_source()``.
_SOURCE_DISPLAY_NAMES = {
    "ac1": "Assetto Corsa",
    "ac-evo": "Assetto Corsa Evo",
    "acc": "Assetto Corsa Competizione",
    "acrally": "Assetto Corsa Rally",
    "synthetic": "Synthetic (mock data)",
}


class CountdownView(QWidget):
    """Full-screen countdown shown after detection finishes and before
    the telemetry widgets appear.

    Renders the detected game name centred above a countdown digit. Both
    lines use the same font size as :class:`DetectionView` ("- Detecting
    AC Environment…") so the visual transition between the two screens
    is smooth — the digit no longer dominates the screen.
    """

    finished = Signal()

    DURATION_S = 5

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._remaining = self.DURATION_S
        self._source_name = ""
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

    def start(self, source_name: str = "") -> None:
        """Begin the countdown. ``source_name`` is the identifier the
        detector emitted (``"ac1"``, ``"ac-evo"`` …); the widget looks
        it up in :data:`_SOURCE_DISPLAY_NAMES` for the on-screen label,
        or falls back to the raw identifier if no mapping exists."""
        self._source_name = source_name or ""
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

        # Same font size as DetectionView so the post-detection
        # transition reads as one continuous screen. The big-digit
        # countdown the previous implementation drew (~short_side/3)
        # was visually jarring next to the much smaller detection
        # text.
        short_side = min(self.width(), self.height())
        text_h = max(18, short_side // 28)
        line_h = int(text_h * 1.5)
        gap = max(8, text_h // 3)

        display_name = _SOURCE_DISPLAY_NAMES.get(
            self._source_name, self._source_name)
        has_name = bool(display_name)

        group_h = line_h + (gap + line_h if has_name else 0)
        top_y = (self.height() - group_h) / 2.0

        p.setFont(label_font(text_h))
        p.setPen(Colors.white)

        if has_name:
            name_rect = QRectF(0.0, top_y, float(self.width()), float(line_h))
            p.drawText(name_rect, Qt.AlignCenter, display_name)
            count_y = top_y + line_h + gap
        else:
            count_y = top_y

        count_rect = QRectF(0.0, count_y, float(self.width()), float(line_h))
        p.drawText(count_rect, Qt.AlignCenter, str(self._remaining))
        p.end()
