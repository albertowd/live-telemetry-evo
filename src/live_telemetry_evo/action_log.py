"""Append a timestamped record of every user action to ``logs/actions.log``.

Separate from the telemetry :class:`~live_telemetry_evo.logger.CsvLogger`:
that captures the car's data, this captures *what the user did* — menu
clicks, hotkeys, widget drags, show/hide toggles, VR tweaks — so a session
can be reconstructed or a bug report correlated with the actions that led
to it. One rolling file, appended across sessions (each run writes a
``session started`` marker), opened lazily on the first action and
line-buffered so nothing is lost on a hard kill.

Every entry point is best-effort: logging a user action must never raise
into the handler that performed it, so all file I/O is guarded.
"""
from __future__ import annotations

import threading
from datetime import datetime

from .paths import logs_dir

_LOG_NAME = "actions.log"

_lock = threading.Lock()
_fp = None          # open file handle, created lazily
_failed = False     # latched if the file can't be opened, to stop retrying


def _handle():
    """Return the (lazily opened) log file handle, or ``None`` if it can't
    be opened. Line-buffered text append so each action reaches disk
    immediately."""
    global _fp, _failed  # pylint: disable=global-statement
    if _fp is None and not _failed:
        try:
            _fp = open(logs_dir() / _LOG_NAME, "a",
                       encoding="utf-8", buffering=1)
        except Exception:  # pylint: disable=broad-except
            _failed = True
    return _fp


def log_action(message: str) -> None:
    """Record one user action to the action log (and echo to the console).
    Every line is prefixed with a bracketed local timestamp. Best-effort:
    never raises."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] [action] {message}")
    with _lock:
        fp = _handle()
        if fp is not None:
            try:
                fp.write(f"[{stamp}] {message}\n")
            except Exception:  # pylint: disable=broad-except
                pass


__all__ = ["log_action"]
