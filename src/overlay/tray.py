from __future__ import annotations

from typing import Callable, Sequence

from PySide6.QtGui import QAction, QActionGroup, QCursor, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from .resources import app_icon_path


def _with_shortcut(text: str, shortcut: str | None) -> str:
    """Embed a shortcut hint Qt right-aligns in the menu, like 'Reset\\tCtrl+Alt+R'."""
    return f"{text}\t{shortcut}" if shortcut else text


def make_tray(
    parent: QWidget,
    on_reset: Callable[[], None],
    on_toggle_click_through: Callable[[], None],
    is_click_through: Callable[[], bool],
    on_set_size: Callable[[int], None],
    current_size_index: Callable[[], int],
    size_labels: Sequence[str],
    on_set_polling_hz: Callable[[int], None],
    current_polling_hz: Callable[[], int],
    polling_hz_options: Sequence[int],
    on_toggle_logging: Callable[[], None],
    is_logging: Callable[[], bool],
    on_open_logs_folder: Callable[[], None],
    on_quit: Callable[[], None],
    reset_shortcut: str | None = None,
    click_through_shortcut: str | None = None,
    size_shortcut: str | None = None,
    quit_shortcut: str | None = None,
    logging_shortcut: str | None = None,
# PySide6 exposes QAction.triggered / QMenu.aboutToShow /
# QSystemTrayIcon.activated as bound Signal objects via runtime metaclass
# magic that pylint can't introspect, so every .connect() in this module
# trips no-member. The signals are real — we silence the whole function.
# pylint: disable=no-member
) -> QSystemTrayIcon | None:
    """Build the notification-area icon and its context menu.

    Menu layout: Reset positions / Click-through / Size submenu / Quit.
    The ``*_shortcut`` strings are display-only hints (e.g. 'Ctrl+Alt+R')
    appended to the action text — actual key handling is done via
    Win32 ``RegisterHotKey`` in :class:`OverlayWindow` because the
    overlay never receives keyboard focus and cannot use ``QShortcut``.

    Left-click and right-click both surface the same menu — the overlay
    has no main window to "show", so a primary action that opens the
    menu matches what the user expects from clicking the icon.
    Returns ``None`` if the OS reports no system-tray support.
    """
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None
    tray = QSystemTrayIcon(QIcon(str(app_icon_path())), parent)
    tray.setToolTip("Live Telemetry Evo")

    menu = QMenu(parent)

    reset_action = QAction(_with_shortcut("Reset positions", reset_shortcut), menu)
    reset_action.triggered.connect(on_reset)
    menu.addAction(reset_action)

    menu.addSeparator()

    click_through_action = QAction(
        _with_shortcut("Click-through", click_through_shortcut), menu
    )
    click_through_action.setCheckable(True)
    # Discard the bool emitted by triggered — the window flips its own
    # state, and aboutToShow re-syncs the checkmark from the source of
    # truth so a hotkey-driven toggle stays in lockstep with the menu.
    click_through_action.triggered.connect(lambda _checked: on_toggle_click_through())
    menu.addAction(click_through_action)

    size_menu = menu.addMenu(_with_shortcut("Size", size_shortcut))
    # Exclusive group gives radio-button behaviour — exactly one entry
    # is checked at a time, mirroring the floating size button's state.
    size_group = QActionGroup(size_menu)
    size_group.setExclusive(True)
    size_actions: list[QAction] = []
    for idx, label in enumerate(size_labels):
        a = QAction(label, size_menu)
        a.setCheckable(True)
        size_group.addAction(a)
        size_menu.addAction(a)
        # Default-arg captures the loop variable per iteration so each
        # action sets its own index instead of all firing the last one.
        a.triggered.connect(lambda _checked, i=idx: on_set_size(i))
        size_actions.append(a)

    # Polling-Hz submenu: drives the SHM poll cadence (and, once
    # logging is enabled, the CSV row rate). Independent from the UI
    # repaint timer, which runs at the display refresh rate.
    hz_menu = menu.addMenu("Polling Hz")
    hz_group = QActionGroup(hz_menu)
    hz_group.setExclusive(True)
    hz_actions: list[tuple[int, QAction]] = []
    for hz in polling_hz_options:
        a = QAction(f"{hz} Hz", hz_menu)
        a.setCheckable(True)
        hz_group.addAction(a)
        hz_menu.addAction(a)
        a.triggered.connect(lambda _checked, h=hz: on_set_polling_hz(h))
        hz_actions.append((hz, a))

    menu.addSeparator()

    # CSV logging — single toggle action whose text flips between
    # "Start logging" / "Stop logging" based on current state.
    logging_action = QAction(_with_shortcut("Start logging", logging_shortcut), menu)
    logging_action.triggered.connect(lambda _checked: on_toggle_logging())
    menu.addAction(logging_action)

    open_logs_action = QAction("Open logs folder", menu)
    open_logs_action.triggered.connect(lambda _checked: on_open_logs_folder())
    menu.addAction(open_logs_action)

    menu.addSeparator()

    quit_action = QAction(_with_shortcut("Quit", quit_shortcut), menu)
    quit_action.triggered.connect(on_quit)
    menu.addAction(quit_action)

    def _refresh_state() -> None:
        click_through_action.setChecked(is_click_through())
        cur = current_size_index()
        for i, a in enumerate(size_actions):
            a.setChecked(i == cur)
        cur_hz = current_polling_hz()
        for hz, a in hz_actions:
            a.setChecked(hz == cur_hz)
        logging_action.setText(
            _with_shortcut(
                "Stop logging" if is_logging() else "Start logging",
                logging_shortcut,
            )
        )

    # Re-read state every time the menu opens so checkmarks stay in
    # lockstep with the floating buttons and the Ctrl+Alt+L hotkey.
    menu.aboutToShow.connect(_refresh_state)

    tray.setContextMenu(menu)

    def _on_activated(reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            _refresh_state()
            menu.popup(QCursor.pos())

    tray.activated.connect(_on_activated)
    tray.show()
    return tray
