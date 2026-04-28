from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QCloseEvent, QKeySequence, QMouseEvent, QShortcut
from PySide6.QtWidgets import QApplication, QWidget


class OverlayWindow(QWidget):
    """Frameless, translucent, always-on-top window that hosts the chart.

    Click-through is toggled with Ctrl+Alt+L. When enabled, mouse events pass
    through to whatever is underneath (e.g. the game). When disabled, the
    window can be dragged with the left mouse button.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setWindowTitle("AC Evo Telemetry Overlay")
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)

        self._click_through = False
        self._drag_origin: QPoint | None = None

        # Ctrl+Alt+L: toggle click-through
        QShortcut(QKeySequence("Ctrl+Alt+L"), self, self.toggle_click_through)
        # Ctrl+Alt+Q: quit. Qt.Tool windows do not trigger quitOnLastWindowClosed,
        # so we exit the app explicitly instead of just closing the window.
        QShortcut(QKeySequence("Ctrl+Alt+Q"), self, QApplication.quit)

        self.resize(640, 280)

    def toggle_click_through(self) -> None:
        self._click_through = not self._click_through
        self.setAttribute(Qt.WA_TransparentForMouseEvents, self._click_through)
        # On Windows, re-applying flags ensures the layered-window style
        # picks up the new transparency hint reliably.
        if sys.platform == "win32":
            self.setWindowFlags(self.windowFlags())
            self.show()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and not self._click_through:
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_origin = None
        super().mouseReleaseEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        QApplication.quit()
        super().closeEvent(event)
