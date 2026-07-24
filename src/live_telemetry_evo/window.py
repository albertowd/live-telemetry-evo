from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import Qt, QAbstractNativeEventFilter, QPoint, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget

from .logbook import log, log_action


# --- Win32 helpers for topmost reassertion ----------------------------------
# Qt's WindowStaysOnTopHint sets WS_EX_TOPMOST on creation, but a fullscreen
# game changing the foreground window can knock our overlay out of the
# topmost band. Re-issuing SetWindowPos(HWND_TOPMOST) keeps us in front.
# WS_EX_NOACTIVATE prevents the overlay from ever stealing focus when shown
# or when click-through is toggled.
_HWND_TOPMOST = -1
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOACTIVATE = 0x0010
_SWP_SHOWWINDOW = 0x0040
_GWL_EXSTYLE = -20
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_TOPMOST = 0x00000008
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020

_WM_HOTKEY = 0x0312
_ERROR_HOTKEY_ALREADY_REGISTERED = 1409
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_NOREPEAT = 0x4000
_VK_C = 0x43
_VK_D = 0x44
_VK_L = 0x4C
_VK_P = 0x50
_VK_Q = 0x51
_VK_R = 0x52
_VK_S = 0x53
_VK_W = 0x57

# Hotkey IDs are app-scoped; any unique small ints work.
_HK_ID_QUIT = 2
_HK_ID_RESET = 3
_HK_ID_SIZE = 4
_HK_ID_LOG = 5
_HK_ID_CLICK_THROUGH = 6
_HK_ID_VR_PLACEMENT = 7
_HK_ID_VR_SPREAD = 8
_HK_ID_VR_DISTANCE = 9

# Human-readable labels shown next to the matching tray-menu entries.
# Every global combo we hold is one an unrelated app can collide with, so
# the set stays focused on the actions genuinely wanted without reaching for
# the tray mid-session: logging (needed mid-lap), reset/quit (recovery paths
# for when the overlay is in a state you cannot click your way out of),
# click-through and size (the two layout tweaks you make while the game has
# the foreground), plus VR placement/spread/distance — the panel geometry
# you adjust from inside the headset, where the tray is unreachable entirely.
#
# Ctrl+Shift rather than Ctrl+Alt: the Ctrl+Alt space is heavily contested by
# gaming peripherals and vendor overlays (NVIDIA's ShadowPlay alone holds
# Ctrl+Alt+M there), which is exactly the software this overlay runs
# alongside. Note the trade: a global hotkey is dispatched ahead of the
# focused window, so while the overlay runs these combos are unavailable to
# whatever app is in front — Ctrl+Shift+R will not reach a browser as
# hard-reload, for instance.
HOTKEY_LOG_LABEL = "Ctrl+Shift+L"
HOTKEY_QUIT_LABEL = "Ctrl+Shift+Q"
HOTKEY_RESET_LABEL = "Ctrl+Shift+R"
HOTKEY_SIZE_LABEL = "Ctrl+Shift+S"
HOTKEY_CLICK_LABEL = "Ctrl+Shift+C"
HOTKEY_VR_PLACEMENT_LABEL = "Ctrl+Shift+P"
HOTKEY_VR_SPREAD_LABEL = "Ctrl+Shift+W"
HOTKEY_VR_DISTANCE_LABEL = "Ctrl+Shift+D"

# (id, virtual-key, label) for each global hotkey. Registration is per-key:
# one combo losing to another app must not take the others down with it.
_HOTKEY_SPECS = (
    (_HK_ID_LOG, _VK_L, HOTKEY_LOG_LABEL),
    (_HK_ID_QUIT, _VK_Q, HOTKEY_QUIT_LABEL),
    (_HK_ID_RESET, _VK_R, HOTKEY_RESET_LABEL),
    (_HK_ID_SIZE, _VK_S, HOTKEY_SIZE_LABEL),
    (_HK_ID_CLICK_THROUGH, _VK_C, HOTKEY_CLICK_LABEL),
    (_HK_ID_VR_PLACEMENT, _VK_P, HOTKEY_VR_PLACEMENT_LABEL),
    (_HK_ID_VR_SPREAD, _VK_W, HOTKEY_VR_SPREAD_LABEL),
    (_HK_ID_VR_DISTANCE, _VK_D, HOTKEY_VR_DISTANCE_LABEL),
)

if sys.platform != "win32":
    raise OSError("Live Telemetry Evo is Windows-only")

_user32 = ctypes.WinDLL("user32", use_last_error=True)

_SetWindowPos = _user32.SetWindowPos
_SetWindowPos.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_uint,
]
_SetWindowPos.restype = ctypes.c_int

_GetWindowLongW = _user32.GetWindowLongW
_GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
_GetWindowLongW.restype = ctypes.c_long

_SetWindowLongW = _user32.SetWindowLongW
_SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
_SetWindowLongW.restype = ctypes.c_long

_RegisterHotKey = _user32.RegisterHotKey
_RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int,
                            ctypes.c_uint, ctypes.c_uint]
_RegisterHotKey.restype = ctypes.c_int

_UnregisterHotKey = _user32.UnregisterHotKey
_UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
_UnregisterHotKey.restype = ctypes.c_int


def _force_topmost(hwnd: int) -> None:
    """Re-assert the overlay sits in the topmost Z-order band.

    Issued on show and periodically thereafter. Borderless / windowed-
    fullscreen games can shuffle Z-order when they (re-)take the foreground;
    re-issuing keeps the overlay visible without stealing focus.
    """
    if not hwnd:
        return
    flags = _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_SHOWWINDOW
    _SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(_HWND_TOPMOST),
                  0, 0, 0, 0, flags)


def _apply_overlay_styles(hwnd: int) -> None:
    """Add WS_EX_NOACTIVATE so the overlay never steals focus.

    Qt sets WS_EX_TOPMOST + WS_EX_LAYERED + WS_EX_TOOLWINDOW from the window
    flags; we layer NOACTIVATE on top so even a click on the overlay (when
    not click-through) doesn't snap focus away from the running game.
    """
    if not hwnd:
        return
    handle = ctypes.c_void_p(hwnd)
    style = _GetWindowLongW(handle, _GWL_EXSTYLE)
    new_style = style | _WS_EX_NOACTIVATE | _WS_EX_TOPMOST | _WS_EX_TOOLWINDOW
    if new_style != style:
        _SetWindowLongW(handle, _GWL_EXSTYLE, new_style)


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_uint32),
        ("pt_x", ctypes.c_long),
        ("pt_y", ctypes.c_long),
    ]


class _HotkeyFilter(QAbstractNativeEventFilter):
    """Routes Win32 WM_HOTKEY messages to per-id Python callbacks.

    QShortcut requires the hosting widget to receive keyboard focus, but the
    overlay sets WS_EX_NOACTIVATE + Qt.WindowDoesNotAcceptFocus precisely so
    it cannot steal focus from the game. Registered global hotkeys bypass
    focus entirely — the OS dispatches WM_HOTKEY to the registering thread's
    message queue regardless of which window is foreground.
    """

    def __init__(self) -> None:
        super().__init__()
        self._callbacks: dict[int, object] = {}

    def register(self, hotkey_id: int, callback) -> None:
        self._callbacks[hotkey_id] = callback

    def nativeEventFilter(self, event_type, message):
        if event_type != b"windows_generic_MSG":
            return False, 0
        msg = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
        if msg.message == _WM_HOTKEY:
            cb = self._callbacks.get(int(msg.wParam))
            if cb is not None:
                cb()
                return True, 0
        return False, 0


class OverlayWindow(QWidget):
    """Frameless, translucent, always-on-top window that hosts the chart.

    Click-through is toggled from the tray (Windows -> Click-through). When
    enabled, mouse events pass through to whatever is underneath (e.g. the
    game). When disabled, the window can be dragged with the left mouse
    button.

    Emits one signal per Win32 global hotkey — ``reset_hotkey`` /
    ``log_hotkey`` / ``size_hotkey`` / ``click_through_hotkey`` /
    ``vr_placement_hotkey`` / ``vr_spread_hotkey`` / ``vr_distance_hotkey``
    — when the matching combo fires; ``app.py`` connects each to the same
    handler its tray-menu entry uses, so the menu and the hotkey share one
    path.
    """

    reset_hotkey = Signal()
    log_hotkey = Signal()
    size_hotkey = Signal()
    click_through_hotkey = Signal()
    vr_placement_hotkey = Signal()
    vr_spread_hotkey = Signal()
    vr_distance_hotkey = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setWindowTitle("Live Telemetry Evo")
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self._click_through = False
        self._drag_origin: QPoint | None = None
        # Ids that RegisterHotKey actually accepted — the ones to release on
        # exit, and the ones to skip when retrying the rest on a later show.
        self._registered_ids: set[int] = set()
        self._last_hotkey_failures: tuple[tuple[str, int], ...] | None = None

        # Re-assert topmost periodically — once a second is enough to recover
        # within a frame or two when a game restores its foreground state.
        self._topmost_timer = QTimer(self)
        self._topmost_timer.setInterval(1000)
        # pylint: disable-next=no-member  # QTimer.timeout is a PySide6 Signal
        self._topmost_timer.timeout.connect(self._reassert_topmost)

        # Global hotkeys via Win32 RegisterHotKey. QShortcut won't work here —
        # WS_EX_NOACTIVATE + Qt.WindowDoesNotAcceptFocus mean the overlay
        # never receives keyboard focus, so widget-scoped shortcuts never fire.
        self._hotkey_filter = _HotkeyFilter()
        self._hotkey_filter.register(_HK_ID_QUIT, QApplication.quit)
        self._hotkey_filter.register(_HK_ID_RESET, self.reset_hotkey.emit)
        self._hotkey_filter.register(_HK_ID_LOG, self.log_hotkey.emit)
        self._hotkey_filter.register(_HK_ID_SIZE, self.size_hotkey.emit)
        self._hotkey_filter.register(
            _HK_ID_CLICK_THROUGH, self.click_through_hotkey.emit
        )
        self._hotkey_filter.register(
            _HK_ID_VR_PLACEMENT, self.vr_placement_hotkey.emit
        )
        self._hotkey_filter.register(_HK_ID_VR_SPREAD, self.vr_spread_hotkey.emit)
        self._hotkey_filter.register(
            _HK_ID_VR_DISTANCE, self.vr_distance_hotkey.emit
        )
        QApplication.instance().installNativeEventFilter(self._hotkey_filter)

        self.resize(640, 280)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        hwnd = int(self.winId())
        _apply_overlay_styles(hwnd)
        _force_topmost(hwnd)
        self._topmost_timer.start()
        self._register_hotkeys()

    def hideEvent(self, event) -> None:
        self._topmost_timer.stop()
        super().hideEvent(event)

    def _register_hotkeys(self) -> None:
        # Pass NULL hwnd so WM_HOTKEY is posted to the GUI thread's queue
        # (not a specific window). Qt's app-level native event filter picks
        # those up regardless of which window holds focus, and the hotkey
        # survives toggle_click_through recreating the native HWND.
        # MOD_NOREPEAT prevents auto-repeat when the user holds keys.
        # Each combo is registered independently and its error read straight
        # after its own call — GetLastError is not cleared on success, so a
        # single read after a batch reports a stale code from whichever call
        # last failed, with no way to tell which combo actually lost.
        mods = _MOD_CONTROL | _MOD_SHIFT | _MOD_NOREPEAT
        failures: list[tuple[str, int]] = []
        for hk_id, vk, label in _HOTKEY_SPECS:
            if hk_id in self._registered_ids:
                continue
            ctypes.set_last_error(0)
            if _RegisterHotKey(None, hk_id, mods, vk):
                self._registered_ids.add(hk_id)
            else:
                failures.append((label, ctypes.get_last_error()))

        # Only report when the outcome changes: this retries on every show
        # (including each click-through toggle), so an unconditional print
        # would spam the log with the same pre-existing conflict.
        outcome = tuple(failures)
        if outcome != self._last_hotkey_failures:
            self._last_hotkey_failures = outcome
            for label, err in failures:
                hint = ""
                # ASCII only: this goes to a Windows console that is often
                # cp1252, where a non-encodable character raises
                # UnicodeEncodeError and would take startup down with it.
                if err == _ERROR_HOTKEY_ALREADY_REGISTERED:
                    hint = (" - already held by another app, or by a leftover "
                            "instance of this one (check Task Manager for a "
                            "stray python.exe / LiveTelemetryEvo.exe)")
                log(f"[overlay] hotkey {label} unavailable (err={err})"
                    f"{hint}")
            if failures:
                working = [lbl for hk_id, _vk, lbl in _HOTKEY_SPECS
                           if hk_id in self._registered_ids]
                log(f"[overlay] working hotkeys: "
                    f"{', '.join(working) or 'none'}")

    def _unregister_hotkeys(self) -> None:
        # Release only what we actually own; unregistering an id we never
        # registered would fail harmlessly but muddies any error we do care
        # about. Safe to call twice (aboutToQuit and closeEvent both fire it).
        for hk_id in sorted(self._registered_ids):
            _UnregisterHotKey(None, hk_id)
        self._registered_ids.clear()

    def _reassert_topmost(self) -> None:
        # Skip while any popup (e.g. our tray context menu) is open. Popups
        # sit in the topmost band themselves, so re-asserting HWND_TOPMOST
        # on the overlay every second would cover the popup and steal the
        # user's click. The next tick after the popup closes restores us.
        if not self.isVisible():
            return
        if QApplication.activePopupWidget() is not None:
            return
        _force_topmost(int(self.winId()))

    @property
    def click_through(self) -> bool:
        return self._click_through

    def toggle_click_through(self, log: bool = True) -> None:
        """Flip click-through. ``log`` records it as a user action; the
        startup call that establishes the default-on state passes
        ``log=False`` so it isn't logged as something the user did."""
        self._click_through = not self._click_through
        self.setAttribute(Qt.WA_TransparentForMouseEvents, self._click_through)
        self.setWindowFlags(self.windowFlags())
        self.show()
        _force_topmost(int(self.winId()))
        if log:
            log_action(f"click-through {'on' if self._click_through else 'off'}")

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
        self._unregister_hotkeys()
        QApplication.quit()
        super().closeEvent(event)
