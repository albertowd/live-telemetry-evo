from __future__ import annotations

from typing import Callable, Sequence

from PySide6.QtGui import QAction, QActionGroup, QCursor, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from .resources import app_icon_path


def make_tray(
    parent: QWidget,
    on_reset: Callable[[], None],
    on_toggle_click_through: Callable[[], None],
    is_click_through: Callable[[], bool],
    on_set_size: Callable[[int], None],
    current_size_index: Callable[[], int],
    size_labels: Sequence[str],
    on_quit: Callable[[], None],
) -> QSystemTrayIcon | None:
    """Build the notification-area icon and its context menu.

    Menu layout: Reset positions / Click-through / Size submenu / Quit.
    Left-click and right-click both surface the same menu — the overlay
    has no main window to "show", so a primary action that opens the
    menu matches what the user expects from clicking the icon.
    Returns ``None`` if the OS reports no system-tray support.
    """
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None
    tray = QSystemTrayIcon(QIcon(str(app_icon_path())), parent)
    tray.setToolTip("AC Evo Telemetry Overlay")

    menu = QMenu(parent)

    reset_action = QAction("Reset positions", menu)
    reset_action.triggered.connect(on_reset)
    menu.addAction(reset_action)

    menu.addSeparator()

    click_through_action = QAction("Click-through", menu)
    click_through_action.setCheckable(True)
    # Discard the bool emitted by triggered — the window flips its own
    # state, and aboutToShow re-syncs the checkmark from the source of
    # truth so a hotkey-driven toggle stays in lockstep with the menu.
    click_through_action.triggered.connect(lambda _checked: on_toggle_click_through())
    menu.addAction(click_through_action)

    size_menu = menu.addMenu("Size")
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

    menu.addSeparator()

    quit_action = QAction("Quit", menu)
    quit_action.triggered.connect(on_quit)
    menu.addAction(quit_action)

    def _refresh_state() -> None:
        click_through_action.setChecked(is_click_through())
        cur = current_size_index()
        for i, a in enumerate(size_actions):
            a.setChecked(i == cur)

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
