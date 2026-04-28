from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ..telemetry import TelemetryFrame


class TelemetrySource(QObject):
    """Base class for any telemetry feed.

    Subclasses emit ``frame`` at their preferred cadence; the rest of the app
    only depends on this interface, so swapping a synthetic feed for a live
    AC Evo reader does not touch the widgets or layout.
    """

    frame = Signal(TelemetryFrame)

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError
