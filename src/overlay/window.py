from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import Qt, QAbstractNativeEventFilter, QPoint, QTimer
from PySide6.QtGui import QCloseEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget


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
_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_NOREPEAT = 0x4000
_VK_L = 0x4C
_VK_Q = 0x51

# Hotkey IDs are app-scoped; any unique small ints work.
_HK_ID_TOGGLE = 1
_HK_ID_QUIT = 2

if sys.platform != "win32":
    raise OSError("AC Evo Telemetry Overlay is Windows-only")

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

    Click-through is toggled with Ctrl+Alt+L. When enabled, mouse events pass
    through to whatever is underneath (e.g. the game). When disabled, the
    window can be dragged with the left mouse button.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setWindowTitle("AC Evo Telemetry Overlay")
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
        self._hotkeys_registered = False

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
        self._hotkey_filter.register(_HK_ID_TOGGLE, self.toggle_click_through)
        self._hotkey_filter.register(_HK_ID_QUIT, QApplication.quit)
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
        if self._hotkeys_registered:
            return
        mods = _MOD_CONTROL | _MOD_ALT | _MOD_NOREPEAT
        ok_l = _RegisterHotKey(None, _HK_ID_TOGGLE, mods, _VK_L)
        ok_q = _RegisterHotKey(None, _HK_ID_QUIT, mods, _VK_Q)
        if not ok_l or not ok_q:
            err = ctypes.get_last_error()
            print(f"[overlay] RegisterHotKey failed (err={err}); "
                  f"Ctrl+Alt+L/Q may not work", file=sys.stderr)
        self._hotkeys_registered = True

    def _unregister_hotkeys(self) -> None:
        if not self._hotkeys_registered:
            return
        _UnregisterHotKey(None, _HK_ID_TOGGLE)
        _UnregisterHotKey(None, _HK_ID_QUIT)
        self._hotkeys_registered = False

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

    def toggle_click_through(self) -> None:
        self._click_through = not self._click_through
        self.setAttribute(Qt.WA_TransparentForMouseEvents, self._click_through)
        self.setWindowFlags(self.windowFlags())
        self.show()
        _force_topmost(int(self.winId()))

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
