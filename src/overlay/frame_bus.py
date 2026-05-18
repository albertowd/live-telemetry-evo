"""Cross-thread telemetry transport.

The polling worker (a :class:`TelemetrySource` running on a worker
:class:`QThread`) calls :meth:`FrameBus.publish` on every tick. The UI
thread reads :meth:`FrameBus.latest` from a repaint :class:`QTimer`
running at the display refresh rate, and the optional CSV writer thread
drains :attr:`FrameBus.csv_queue` at the polling cadence.

We use plain :mod:`threading` primitives instead of Qt signals because
queued signals serialise through the receiver's event loop, which would
turn a 250 Hz polling rate into 250 events/sec sitting in the UI loop
even when most snapshots are stale by the time the next paint runs.
Latest-only via a lock keeps the UI's per-frame cost O(1) regardless of
how fast the worker publishes.

Snapshot semantics: :meth:`publish` deep-copies the source's mutable
``TelemetryFrame`` before storing it. Readers therefore see an immutable
slice of state — no risk of catching the source mid-mutation between
``_apply_graphics`` and ``_apply_physics``.
"""
from __future__ import annotations

import copy
import queue
import threading
from typing import Optional

from .telemetry import TelemetryFrame


class FrameBus:
    """Latest-snapshot + bounded-queue transport between threads."""

    def __init__(self, csv_queue_maxsize: int = 1024) -> None:
        self._lock = threading.Lock()
        self._latest: Optional[TelemetryFrame] = None
        # CSV queue is opt-in (logger calls :meth:`enable_csv`). The
        # bounded size protects the polling thread from disk backpressure
        # — if writes stall, oldest rows are dropped instead of blocking
        # the worker. ``maxsize`` of 1024 buys ~4 s at 250 Hz before
        # eviction.
        self._csv_queue_maxsize = csv_queue_maxsize
        self.csv_queue: Optional["queue.Queue[TelemetryFrame]"] = None
        self.csv_dropped = 0

    def enable_csv(self) -> "queue.Queue[TelemetryFrame]":
        """Open the CSV queue and return it. Idempotent — re-enabling
        clears the dropped-frame counter so each logging session starts
        from zero."""
        if self.csv_queue is None:
            self.csv_queue = queue.Queue(maxsize=self._csv_queue_maxsize)
        self.csv_dropped = 0
        return self.csv_queue

    def disable_csv(self) -> None:
        """Drop the queue reference so subsequent publishes skip the
        enqueue cost. Any rows still queued are GC'd with it."""
        self.csv_queue = None

    def publish(self, frame: TelemetryFrame) -> None:
        """Called from the polling worker thread on every tick.

        Deep-copies the frame so the UI / CSV writer never observe state
        that the source is mid-way through mutating. Cheap at telemetry
        scale (a few microseconds even at 250 Hz) — the alternative
        (sharing references under a long-held lock) would stall the UI.
        """
        snapshot = copy.deepcopy(frame)
        with self._lock:
            self._latest = snapshot
        q = self.csv_queue
        if q is not None:
            try:
                q.put_nowait(snapshot)
            except queue.Full:
                # Disk can't keep up — drop the oldest row, push the new
                # one. Polling cadence stays exact; the writer sees a hole
                # in the timeline rather than the wrong samples.
                try:
                    q.get_nowait()
                    q.put_nowait(snapshot)
                except (queue.Empty, queue.Full):
                    pass
                self.csv_dropped += 1

    def latest(self) -> Optional[TelemetryFrame]:
        """Called from the UI repaint timer. Returns the most recent
        snapshot, or ``None`` until the first :meth:`publish`."""
        with self._lock:
            return self._latest
