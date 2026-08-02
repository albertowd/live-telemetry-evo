from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal

from ..frame_bus import FrameBus
from ..telemetry import TelemetryFrame


class TelemetrySource(QObject):
    """Base class for any telemetry feed.

    Subclasses are designed to live on a worker :class:`QThread`.
    ``start()`` is invoked via ``QThread.started`` (queued) so the
    internal :class:`QTimer` is created on the worker thread — Qt timers
    are bound to the thread they're started from.

    Two channels for downstream consumers:
      * legacy ``frame`` signal — preserved for any direct subscriber
        that doesn't want a bus, but emitted from the worker thread now
        (use a queued connection on the receiver).
      * :class:`FrameBus` (preferred) — UI reads the latest snapshot at
        display refresh rate, CSV writer drains a bounded queue at the
        polling cadence. Wire one via :meth:`set_bus` before starting.

    Hz changes from the UI thread go through :attr:`hz_change_requested`
    — emit it from the UI side and the queued connection re-fires
    :meth:`set_hz` on the worker thread, so the :class:`QTimer` mutation
    happens where the timer lives.
    """

    frame = Signal(TelemetryFrame)
    hz_change_requested = Signal(int)
    stop_requested = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bus: FrameBus | None = None
        # Identity of the car the frame's per-car state was built for.
        # "" = nothing adopted yet (also the state after a disconnect, so
        # the next connect re-seeds from scratch). See :meth:`_car_changed`.
        self._car_key: str = ""
        # Queued connection — emitted from UI, lands on the worker thread.
        # Subclasses override ``set_hz`` to mutate their own QTimer.
        # pylint: disable-next=no-member
        self.hz_change_requested.connect(self.set_hz, Qt.QueuedConnection)
        # Blocking queued — emitted from the UI thread at shutdown and
        # returns only after ``stop()`` ran on the worker, so the polling
        # QTimer is stopped on the thread it lives on (stopping it from
        # the main thread trips Qt's "Timers cannot be stopped from
        # another thread"). Must NEVER be emitted from the worker thread
        # itself — a blocking queued emit to your own thread deadlocks.
        # pylint: disable-next=no-member
        self.stop_requested.connect(self.stop, Qt.BlockingQueuedConnection)

    def set_bus(self, bus: FrameBus) -> None:
        """Wire the source to a :class:`FrameBus`. Set before the source
        is moved to its worker thread — the attribute write is plain
        Python and visible to the worker as soon as the thread starts."""
        self._bus = bus

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def set_hz(self, hz: int) -> None:
        """Live-change the polling rate. Default no-op; subclasses with
        a :class:`QTimer` override to call ``setInterval`` on it."""

    def _car_changed(self, car_key: str, frame: TelemetryFrame) -> bool:
        """Adopt ``car_key`` as the current car, resetting ``frame`` if
        it differs from the one the frame was built for.

        Returns True exactly once per change, so the caller can also
        clear its own per-car caches (parsed car data, static spec
        sheets, derivation state) and re-seed them for the new car.

        A blank key is ignored: some games leave the identity slot empty
        for the first frames of a session, and treating that as a car
        change would reset the frame on every tick until it fills in.
        """
        key = (car_key or "").strip()
        if not key or key == self._car_key:
            return False
        self._car_key = key
        frame.reset_for_car(key)
        return True
