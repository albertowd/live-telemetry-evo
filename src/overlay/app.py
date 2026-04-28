from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QVBoxLayout

from .chart import LiveChart
from .telemetry import TelemetrySource
from .window import OverlayWindow


def run() -> int:
    app = QApplication(sys.argv)

    window = OverlayWindow()
    chart = LiveChart(window_size=600)

    layout = QVBoxLayout(window)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(chart)

    source = TelemetrySource(hz=60, parent=window)
    source.sample.connect(chart.append)
    source.start()

    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
