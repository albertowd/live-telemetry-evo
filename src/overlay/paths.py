"""Filesystem locations the overlay reads / writes at runtime.

Resolution policy: **always use a folder local to where the app is
running.** Config (``positions.json``) and CSV logs land in the same
place so the user can browse them in the same Explorer window they
launched the app from — no hunting through ``%APPDATA%``.

* **Frozen build (PyInstaller .exe)** — directory the ``.exe`` lives in
  (``Path(sys.executable).parent``). Stable regardless of how the user
  launched it (double-click, shortcut with custom "Start in", CLI).
* **Dev (`python -m overlay`)** — current working directory, which is
  typically the repo root. ``positions.json`` and ``logs/`` will appear
  there during development; both are git-ignored.

If you need to override (testing, portable install), set the
``LIVE_TELEMETRY_DATA_DIR`` environment variable to an absolute path —
it wins over both branches above.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


_ENV_OVERRIDE = "LIVE_TELEMETRY_DATA_DIR"


def app_data_dir() -> Path:
    """Return the directory the overlay reads/writes its config + logs
    in. Always exists on return (``mkdir(parents=True, exist_ok=True)``)."""
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        path = Path(override)
    elif getattr(sys, "frozen", False):
        # ``sys.executable`` on a PyInstaller onefile binary points to
        # the real .exe location (the bootloader sets this), not the
        # ``_MEIPASS`` temp dir the bundle is unpacked into.
        path = Path(sys.executable).resolve().parent
    else:
        path = Path.cwd().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    """Absolute path to the JSON settings file (positions, visibility,
    size index, polling Hz). Sits alongside the ``logs/`` folder so
    everything user-relevant is in one place."""
    return app_data_dir() / "positions.json"


def logs_dir() -> Path:
    """``<app_data_dir>/logs`` — created on first call."""
    path = app_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path
