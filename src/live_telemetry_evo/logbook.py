"""Timestamped console + file logging for the overlay.

Every line the app emits goes through :func:`log` (replacing bare
``print``): it is printed and appended to ``logs/session.log``, both
prefixed with a bracketed local timestamp. :func:`log_action` is a thin
wrapper that tags user actions with ``[action]`` so they stand out in the
same stream — menu clicks, hotkeys, widget drags, show/hide toggles, VR
tweaks — next to the ``[overlay]`` / ``[updater]`` / source diagnostics.

Separate from the telemetry :class:`~live_telemetry_evo.logger.CsvLogger`,
which captures the car's data. One file for the whole run, appended across
runs (each run writes a ``session started`` marker), opened lazily on the
first message and line-buffered so nothing is lost on a hard kill.

Best-effort: logging must never raise into the caller, so all file I/O is
guarded. The lock serialises writes from the UI thread and the source /
VR worker threads.
"""
from __future__ import annotations

import threading
from datetime import datetime

from .paths import logs_dir

_LOG_NAME = "session.log"

_lock = threading.Lock()
_fp = None          # open file handle, created lazily
_failed = False     # latched if the file can't be opened, to stop retrying


def _handle():
    """Return the (lazily opened) log file handle, or ``None`` if it can't
    be opened. Line-buffered text append so each line reaches disk
    immediately."""
    global _fp, _failed  # pylint: disable=global-statement
    if _fp is None and not _failed:
        try:
            _fp = open(logs_dir() / _LOG_NAME, "a",
                       encoding="utf-8", buffering=1)
        except Exception:  # pylint: disable=broad-except
            _failed = True
    return _fp


def log(message: str) -> None:
    """Print ``message`` and append it to ``session.log``, both prefixed
    with a bracketed local timestamp. Best-effort: never raises."""
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    with _lock:
        print(line)
        fp = _handle()
        if fp is not None:
            try:
                fp.write(line + "\n")
            except Exception:  # pylint: disable=broad-except
                pass


def log_action(message: str) -> None:
    """Record one user action — :func:`log` with an ``[action]`` tag so it
    stands out in the shared session log."""
    log(f"[action] {message}")


__all__ = ["log", "log_action"]
