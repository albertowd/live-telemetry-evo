from __future__ import annotations

import time

from PySide6.QtCore import Qt, QRectF, QTimer, Signal
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from ..colors import Colors
from ..fonts import label_font
from ..resources import app_icon_path
from ..sources.detect import acpmf_tag_present, detect_running_game


class DetectionView(QWidget):
    """Full-screen 'Detecting AC Environment...' poller shown before the
    countdown when ``--source auto`` is in effect.

    Polls every :attr:`POLL_MS` ms. On first confident detection it
    waits until at least :attr:`MIN_VISIBLE_MS` has elapsed since
    :meth:`start`, then emits :attr:`detected` with the source name
    and hides itself — so the logo + status text stay readable even
    when a game is already running and detection succeeds on the first
    tick.

    Detection in the ``acpmf_*`` family (AC1 / ACC / AC Rally) can be
    ambiguous when the game is paused or sitting on its main menu (the
    physics block is zero-filled, so the Kelvin-vs-Celsius fingerprint
    won't fire). In that case we keep polling for :attr:`ACPMF_TIMEOUT_MS`
    before committing to the ACC default, giving the user time to
    unpause / enter a stage so AC Rally's Kelvin temps reveal themselves.
    """

    detected = Signal(str)

    POLL_MS = 500
    MIN_VISIBLE_MS = 1000
    ACPMF_TIMEOUT_MS = 10000
    MESSAGE = "Detecting AC Environment..."

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        # Stay hidden until start() — child widgets are otherwise made
        # visible when the top-level window shows, which would paint
        # the message before we begin polling.
        self.hide()
        self._logo = QPixmap(str(app_icon_path()))
        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_MS)
        # pylint: disable-next=no-member  # QTimer.timeout is a PySide6 Signal
        self._timer.timeout.connect(self._tick)
        self._started_at = 0.0
        self._acpmf_first_seen: float | None = None
        self._detected_name: str | None = None

    def start(self) -> None:
        self._detected_name = None
        self._acpmf_first_seen = None
        self._started_at = time.monotonic()
        self.show()
        self.update()
        # Probe once immediately so an already-running game is picked up
        # without waiting a full POLL_MS for the first tick.
        QTimer.singleShot(0, self._tick)
        self._timer.start()

    def _tick(self) -> None:
        if self._detected_name is None:
            name = detect_running_game()
            if name is None:
                # Detector couldn't pick confidently. If the acpmf_* tags
                # are up (paused / menu state of ACC or AC Rally) and the
                # ambiguity has lasted past ACPMF_TIMEOUT_MS, commit to
                # ACC so genuine ACC users at the main menu don't wait
                # forever. Until then, keep polling — the user might
                # just need to unpause for the Kelvin fingerprint to
                # appear and resolve to AC Rally.
                if acpmf_tag_present():
                    now = time.monotonic()
                    if self._acpmf_first_seen is None:
                        self._acpmf_first_seen = now
                    elif (now - self._acpmf_first_seen) * 1000.0 >= self.ACPMF_TIMEOUT_MS:
                        name = "acc"
                    else:
                        return
                else:
                    self._acpmf_first_seen = None
                    return
            if name is None:
                return
            self._detected_name = name
            self._timer.stop()
        # Hold the logo + status text on screen for at least MIN_VISIBLE_MS
        # after start() — instant detections (game already running) would
        # otherwise vanish in a single frame and skip straight to the
        # countdown. Re-arm the same timer for the remaining window.
        remaining_ms = int(self.MIN_VISIBLE_MS - (time.monotonic() - self._started_at) * 1000.0)
        if remaining_ms > 0:
            QTimer.singleShot(remaining_ms, self._finish)
            return
        self._finish()

    def _finish(self) -> None:
        name = self._detected_name
        if name is None:
            return
        self.hide()
        # pylint: disable-next=no-member  # detected is a PySide6 Signal
        self.detected.emit(name)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        # Scale text to a fraction of the smaller side so the message
        # stays readable across portrait/landscape/split-screen layouts.
        short_side = min(self.width(), self.height())
        text_h = max(18, short_side // 28)
        # Logo at ~1/6 of the short side so it dominates the centre
        # without crowding the message that sits just below it.
        logo_target = max(64, short_side // 6)

        # Centre the logo+text group as one vertical column.
        gap = max(12, text_h)
        group_h = logo_target + gap + int(text_h * 1.5)
        cx = self.width() / 2.0
        top_y = (self.height() - group_h) / 2.0

        if not self._logo.isNull():
            # Preserve the icon's aspect ratio: scale by the smaller of
            # width/height so non-square sources still fit the target.
            scaled = self._logo.scaled(
                logo_target, logo_target,
                Qt.KeepAspectRatio, Qt.SmoothTransformation,
            )
            logo_x = cx - scaled.width() / 2.0
            logo_y = top_y + (logo_target - scaled.height()) / 2.0
            p.drawPixmap(int(logo_x), int(logo_y), scaled)

        p.setFont(label_font(text_h))
        p.setPen(Colors.white)
        text_rect = QRectF(0.0, top_y + logo_target + gap,
                           float(self.width()), float(text_h * 1.5))
        p.drawText(text_rect, Qt.AlignHCenter | Qt.AlignTop, self.MESSAGE)
        p.end()
