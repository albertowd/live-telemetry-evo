from __future__ import annotations

import math

from PySide6.QtCore import QObject, QTimer, Signal


class TelemetrySource(QObject):
    """Stub data source emitting synthetic samples.

    Replace `_tick` with a reader for the AC Evo shared-memory layout
    (or UDP feed) when wiring up real telemetry. The signal contract stays
    the same so downstream widgets do not need to change.
    """

    sample = Signal(dict)

    def __init__(self, hz: int = 60, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._t = 0.0
        self._dt = 1.0 / hz
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / hz))
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        self._t += self._dt
        throttle = 0.5 + 0.5 * math.sin(self._t * 1.7)
        brake = max(0.0, -math.sin(self._t * 0.9)) * 0.8
        speed = 0.3 + 0.4 * (0.5 + 0.5 * math.sin(self._t * 0.4))
        self.sample.emit(
            {
                "throttle": throttle,
                "brake": brake,
                "speed": speed,
            }
        )
