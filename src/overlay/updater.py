"""Async update check against GitHub releases.

On startup the app queries
``https://api.github.com/repos/albertowd/live-telemetry-evo/releases/latest``
and, if the tag is newer than the running version, downloads the
matching ``LiveTelemetryEvo-<version>.exe`` asset into the same folder
as the running executable. The user sees a system-tray balloon when
the download finishes; failures are silent.

Re-downloads are skipped — if a file with the asset's filename already
exists next to the .exe (from a previous run), we leave it alone.

The tray menu has a "Check for Updates" action whose label tracks the
controller's state machine — Checking… / Downloading… / Restart to
Update — and offers a manual re-check from the IDLE state. The
restart path launches the downloaded .exe detached, then quits the
current process so the new build can attach to the same SHM tags.
"""
from __future__ import annotations

import json
import re
import ssl  # imported eagerly so PyInstaller bundles _ssl + OpenSSL DLLs
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QApplication


GITHUB_API_LATEST = (
    "https://api.github.com/repos/albertowd/live-telemetry-evo/releases/latest"
)
HTTP_TIMEOUT_S = 10
USER_AGENT = "live-telemetry-evo-updater"


def _parse_version(s: str) -> Optional[tuple[int, int, int]]:
    """Strip a leading ``v`` and parse a ``X.Y.Z`` triple. Returns
    ``None`` if the string doesn't carry a recognisable version
    (pre-release suffixes like ``-rc1`` are tolerated but ignored)."""
    s = s.strip().lstrip("vV")
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", s)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def get_current_version() -> Optional[str]:
    """Resolve the running app's version. Frozen build: from the .exe
    filename (``LiveTelemetryEvo-X.Y.Z.exe``). Dev: from
    ``pyproject.toml`` walking up from this file. ``None`` if neither
    branch produces a match — the update check is then skipped."""
    if getattr(sys, "frozen", False):
        m = re.search(r"-(\d+\.\d+\.\d+)$", Path(sys.executable).stem)
        return m.group(1) if m else None
    for parent in Path(__file__).resolve().parents:
        pyproject = parent / "pyproject.toml"
        if pyproject.exists():
            text = pyproject.read_text(encoding="utf-8")
            m = re.search(r'^\s*version\s*=\s*"([^"]+)"',
                          text, flags=re.MULTILINE)
            return m.group(1) if m else None
    return None


def install_dir() -> Path:
    """Folder the new .exe will be written into — alongside the running
    executable when frozen, current working directory in dev."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd().resolve()


class UpdateChecker(QObject):
    """Single-shot worker: query GitHub, compare versions, download the
    asset if newer. Lives on its own QThread; reports outcomes via
    Qt signals that are queued back to the UI thread automatically."""

    # Emitted when a newer release was found and download is starting.
    download_started = Signal(str)            # tag

    # Emitted once the file is fully written to disk.
    download_finished = Signal(str, str)      # tag, absolute path

    # Emitted when the asset for the latest tag is already on disk
    # (left over from a prior run) — nothing to do, but useful in logs.
    already_present = Signal(str, str)        # tag, absolute path

    # Emitted when we're already on the latest release.
    up_to_date = Signal(str)                  # current tag

    # Emitted on any error (offline, GitHub 5xx, asset missing). The
    # UI keeps quiet — this is for diagnostics in stdout only.
    failed = Signal(str)                      # human-readable reason

    # Emitted exactly once when the worker finishes (success or fail),
    # so the parent can quit the QThread cleanly.
    done = Signal()

    def __init__(self, current_version: str,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._current = current_version

    def run(self) -> None:
        """Entry point — wire to ``QThread.started``."""
        try:
            self._run()
        finally:
            self.done.emit()

    def _run(self) -> None:
        try:
            payload = self._fetch_latest()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            self.failed.emit(f"could not reach GitHub: {e}")
            return
        except ValueError as e:
            self.failed.emit(f"could not parse GitHub response: {e}")
            return

        tag = (payload.get("tag_name") or "").strip()
        latest = _parse_version(tag)
        current = _parse_version(self._current)
        if latest is None or current is None:
            self.failed.emit(
                f"could not parse versions (latest={tag!r}, "
                f"current={self._current!r})"
            )
            return
        if latest <= current:
            self.up_to_date.emit(tag)
            return

        asset_url = self._pick_asset(payload, tag)
        if asset_url is None:
            self.failed.emit(f"no .exe asset found in release {tag}")
            return

        target = install_dir() / Path(asset_url).name
        if target.exists():
            self.already_present.emit(tag, str(target))
            return

        self.download_started.emit(tag)
        try:
            self._download(asset_url, target)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            self.failed.emit(f"download failed: {e}")
            return
        self.download_finished.emit(tag, str(target))

    @staticmethod
    def _fetch_latest() -> dict:
        req = urllib.request.Request(
            GITHUB_API_LATEST,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": USER_AGENT,
            },
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S,
                                     context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))

    @staticmethod
    def _pick_asset(payload: dict, tag: str) -> Optional[str]:
        """Find the ``LiveTelemetryEvo-<version>.exe`` asset. Prefer one
        whose name contains the tag's bare version; otherwise return
        the first ``.exe`` in the release's asset list."""
        version = tag.lstrip("vV")
        assets = payload.get("assets") or []
        for a in assets:
            name = (a.get("name") or "")
            if name.lower().endswith(".exe") and version in name:
                return a.get("browser_download_url")
        for a in assets:
            name = (a.get("name") or "")
            if name.lower().endswith(".exe"):
                return a.get("browser_download_url")
        return None

    @staticmethod
    def _download(url: str, target: Path) -> None:
        """Stream the asset to ``<target>.partial`` then atomic-rename
        to ``target`` so a crash mid-download never leaves an
        incomplete file under the final name (which would defeat the
        skip-if-exists check on the next launch)."""
        tmp = target.with_suffix(target.suffix + ".partial")
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S,
                                     context=ctx) as resp:
            with tmp.open("wb") as f:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
        tmp.replace(target)


class UpdateController(QObject):
    """Stateful façade around :class:`UpdateChecker` for the UI.

    Drives a four-state machine the tray menu reflects directly:

    * ``IDLE`` — "Check for Updates" (clickable).
    * ``CHECKING`` — "Checking updates…" (disabled).
    * ``DOWNLOADING`` — "Downloading update…" (disabled).
    * ``READY`` — "Restart to Update" (clickable; launches the new .exe).

    Clicking the menu in ``IDLE`` triggers :meth:`start_check`. Clicking
    in ``READY`` triggers :meth:`restart_into_update`. Failures and
    "already up to date" both fall back to ``IDLE`` so the user can
    re-run the check manually.

    :class:`UpdateChecker.already_present` (the asset for a newer tag
    is already on disk from a prior session) is treated as ``READY``
    too — the user should still be able to restart into the file that
    was downloaded last time but never used.
    """

    IDLE = "idle"
    CHECKING = "checking"
    DOWNLOADING = "downloading"
    READY = "ready"

    # Emitted on every state transition so the tray menu can refresh
    # its label / enabled state without polling.
    state_changed = Signal(str, str)              # state, detail

    # Emitted when a download just completed (NOT on already_present);
    # the app uses this to fire the tray balloon notification — we
    # don't notify on already_present because that fires on every
    # startup once the file is on disk, which would be spammy.
    download_finished = Signal(str, str)          # tag, absolute path

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._state = self.IDLE
        self._thread: QThread | None = None
        self._checker: UpdateChecker | None = None
        self._restart_path: str | None = None
        self._current = get_current_version()

    @property
    def state(self) -> str:
        return self._state

    @property
    def restart_path(self) -> str | None:
        return self._restart_path

    def start_check(self) -> None:
        """Kick off a check on a worker thread. No-op if a check or
        download is already in flight, or if we're already pending a
        restart."""
        if self._state != self.IDLE:
            return
        if not self._current:
            print("[updater] skipped: could not determine current version")
            return

        self._set_state(self.CHECKING)

        thread = QThread(self)
        checker = UpdateChecker(self._current)
        checker.moveToThread(thread)
        # pylint: disable=no-member  # Qt signals are runtime metaclass magic
        thread.started.connect(checker.run)
        checker.download_started.connect(self._on_download_started)
        checker.download_finished.connect(self._on_download_finished)
        checker.already_present.connect(self._on_already_present)
        checker.up_to_date.connect(self._on_up_to_date)
        checker.failed.connect(self._on_failed)
        checker.done.connect(thread.quit)
        thread.finished.connect(checker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_thread_finished)

        self._thread = thread
        self._checker = checker
        thread.start()

    def restart_into_update(self) -> bool:
        """Launch the downloaded .exe in a detached process and quit
        the current app. Returns ``False`` if no update is ready or the
        launch failed (in which case the state stays at ``READY`` so
        the user can try again)."""
        if self._state != self.READY or not self._restart_path:
            return False
        path = Path(self._restart_path)
        if not path.exists():
            # File was deleted between download and click — degrade
            # gracefully by reverting to IDLE so a fresh check can
            # re-download it.
            print(f"[updater] restart target missing: {path}")
            self._restart_path = None
            self._set_state(self.IDLE, "restart target missing")
            return False

        flags = 0
        if sys.platform == "win32":
            # DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP: the child
            # survives our exit and has no console attachment (matches
            # how the .exe is launched from Explorer).
            flags = (subprocess.DETACHED_PROCESS
                     | subprocess.CREATE_NEW_PROCESS_GROUP)
        try:
            # pylint: disable-next=consider-using-with
            subprocess.Popen(
                [str(path)],
                cwd=str(path.parent),
                close_fds=True,
                creationflags=flags,
            )
        except OSError as e:
            print(f"[updater] failed to launch {path}: {e}")
            return False

        print(f"[updater] launched {path} — quitting current app")
        app = QApplication.instance()
        if app is not None:
            app.quit()
        return True

    def _set_state(self, new_state: str, detail: str = "") -> None:
        if new_state == self._state:
            return
        self._state = new_state
        self.state_changed.emit(new_state, detail)

    def _on_download_started(self, tag: str) -> None:
        print(f"[updater] new release {tag} — downloading")
        self._set_state(self.DOWNLOADING, tag)

    def _on_download_finished(self, tag: str, path: str) -> None:
        print(f"[updater] downloaded {tag} -> {path}")
        self._restart_path = path
        self._set_state(self.READY, tag)
        self.download_finished.emit(tag, path)

    def _on_already_present(self, tag: str, path: str) -> None:
        print(f"[updater] {tag} already on disk at {path}")
        self._restart_path = path
        self._set_state(self.READY, tag)

    def _on_up_to_date(self, tag: str) -> None:
        print(f"[updater] up to date (latest={tag}, current={self._current})")
        self._set_state(self.IDLE, tag)

    def _on_failed(self, reason: str) -> None:
        print(f"[updater] check failed: {reason}")
        # Failure always lands back in IDLE so the user can re-trigger
        # — including failures during the DOWNLOADING phase.
        self._set_state(self.IDLE, reason)

    def _on_thread_finished(self) -> None:
        # Drop strong refs so the next start_check builds a fresh
        # worker + thread; the old ones are scheduled for deletion via
        # the deleteLater chain set up in start_check.
        self._thread = None
        self._checker = None
