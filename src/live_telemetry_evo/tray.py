from __future__ import annotations

from typing import Callable, Sequence

from PySide6.QtGui import QAction, QActionGroup, QCursor, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from .logbook import log_action
from .resources import app_icon_path
from .updater import UpdateController


# Labels for each UpdateController state — the tray menu action's text
# tracks these directly. Kept module-level so the constants are easy to
# tweak without diving into the menu-construction code.
_UPDATE_LABELS = {
    UpdateController.IDLE: ("Check for Updates", True),
    UpdateController.CHECKING: ("Checking updates...", False),
    UpdateController.DOWNLOADING: ("Downloading update...", False),
    UpdateController.READY: ("Restart to Update", True),
}


def _with_shortcut(text: str, shortcut: str | None) -> str:
    """Embed a shortcut hint Qt right-aligns in the menu, like 'Reset\\tCtrl+Shift+R'."""
    return f"{text}\t{shortcut}" if shortcut else text


def make_tray(
    parent: QWidget,
    on_reset: Callable[[], None],
    on_toggle_click_through: Callable[[], None],
    is_click_through: Callable[[], bool],
    on_set_size: Callable[[int], None],
    current_size_index: Callable[[], int],
    size_labels: Sequence[str],
    widget_toggles: Sequence[tuple[str, str]],
    on_toggle_widget: Callable[[str, bool], None],
    is_widget_visible: Callable[[str], bool],
    on_set_polling_hz: Callable[[int], None],
    current_polling_hz: Callable[[], int],
    polling_hz_options: Sequence[int],
    on_set_vr_placement: Callable[[str], None],
    current_vr_placement: Callable[[], str],
    on_set_vr_spread: Callable[[float], None],
    current_vr_spread: Callable[[], float],
    vr_spread_options: Sequence[float],
    on_set_vr_distance: Callable[[float], None],
    current_vr_distance: Callable[[], float],
    vr_distance_options: Sequence[float],
    is_vr_active: Callable[[], bool],
    is_windows_ready: Callable[[], bool],
    is_game_detected: Callable[[], bool],
    on_toggle_logging: Callable[[], None],
    is_logging: Callable[[], bool],
    on_open_logs_folder: Callable[[], None],
    on_quit: Callable[[], None],
    updater: UpdateController | None = None,
    reset_shortcut: str | None = None,
    quit_shortcut: str | None = None,
    logging_shortcut: str | None = None,
    size_shortcut: str | None = None,
    click_through_shortcut: str | None = None,
    vr_placement_shortcut: str | None = None,
    vr_spread_shortcut: str | None = None,
    vr_distance_shortcut: str | None = None,
# PySide6 exposes QAction.triggered / QMenu.aboutToShow /
# QSystemTrayIcon.activated as bound Signal objects via runtime metaclass
# magic that pylint can't introspect, so every .connect() in this module
# trips no-member. The signals are real — we silence the whole function.
# pylint: disable=no-member
) -> QSystemTrayIcon | None:
    """Build the notification-area icon and its context menu.

    Menu layout: three category submenus — Data (Open logs folder /
    Polling Hz / logging), VR (Distance / Placement / Size / Spread /
    Widgets), Windows (Click-through / Reset positions / Size / Widgets) —
    then Check for Updates and Quit. Entries within each menu are ordered
    alphabetically by label; value-scale submenus (Polling Hz, Distance,
    Spread, Size) keep their natural numeric / scale order.
    The ``*_shortcut`` strings are display-only hints (e.g. 'Ctrl+Shift+R')
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

    # The tray menu is organised into three top-level categories — Data,
    # VR, Windows — followed by Check for Updates and Quit. Each category
    # groups the controls that act on one part of the app so the flat list
    # doesn't sprawl as more knobs are added.

    # Per-widget show/hide toggles. A **Widgets** submenu is built under both
    # the VR and Windows menus, each with its OWN checkable actions — mirroring
    # how the Size submenu keeps separate action sets per menu. (A single
    # QAction shared across two menus doesn't render its checkmark reliably in
    # the second one.) All sets drive the same visibility flags, and every set
    # is re-synced from ``is_widget_visible`` on menu open, so a change made in
    # one menu shows correctly the next time either opens. The two menus are
    # mutually exclusive (VR vs desktop), so both are never visible at once.
    # Not radio-exclusive, so no action group. No shortcut hints by request.
    def _build_widgets_submenu(parent_menu: QMenu) -> list[tuple[str, QAction]]:
        sub = parent_menu.addMenu("Widgets")
        actions: list[tuple[str, QAction]] = []
        for key, label in widget_toggles:
            a = QAction(label, sub)
            a.setCheckable(True)
            # ``triggered`` passes the new checked state for a checkable action.
            a.triggered.connect(
                lambda checked, k=key: on_toggle_widget(k, checked))
            sub.addAction(a)
            actions.append((key, a))
        return actions

    # ---- Data: telemetry poll rate + CSV logging --------------------------
    # The whole category is greyed out until a game is detected and the
    # source starts feeding frames — polling Hz has nothing to drive and
    # logging would only produce an empty CSV before then (see
    # _refresh_state). The persisted poll rate still applies the moment the
    # source starts.
    data_menu = menu.addMenu("Data")

    # Entries are ordered alphabetically by label: Open logs folder, Polling
    # Hz, Start/Stop logging.
    open_logs_action = QAction("Open logs folder", data_menu)
    open_logs_action.triggered.connect(lambda _checked: on_open_logs_folder())
    data_menu.addAction(open_logs_action)

    # Polling-Hz submenu: drives the SHM poll cadence (and, once logging is
    # enabled, the CSV row rate). Independent from the UI repaint timer,
    # which runs at the display refresh rate. Values stay in numeric order.
    hz_menu = data_menu.addMenu("Polling Hz")
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

    # CSV logging — single toggle action whose text flips between
    # "Start logging" / "Stop logging" based on current state.
    logging_action = QAction(
        _with_shortcut("Start logging", logging_shortcut), data_menu
    )
    logging_action.triggered.connect(lambda _checked: on_toggle_logging())
    data_menu.addAction(logging_action)

    # ---- VR: headset panel placement / geometry --------------------------
    # Every control here only bites while the HUD is being rendered into a
    # headset, so the whole category is greyed out on the desktop overlay
    # (see _refresh_state). The persisted choices still apply the moment VR
    # starts. The shortcut hint on each submenu title (Placement / Spread /
    # Distance) belongs to the global hotkey that *cycles* forward through
    # that submenu's entries — the tray can't be reached from inside VR.
    vr_menu = menu.addMenu("VR")
    # Submenus are ordered alphabetically by title: Distance, Placement,
    # Size, Spread, Widgets. Their value lists (Distance / Spread / Size)
    # stay in natural numeric / scale order.

    # Distance submenu: how far the whole panel floats from the viewer
    # (the cylinder radius). Larger pushes the HUD further away.
    distance_menu = vr_menu.addMenu(
        _with_shortcut("Distance", vr_distance_shortcut)
    )
    distance_group = QActionGroup(distance_menu)
    distance_group.setExclusive(True)
    distance_actions: list[tuple[float, QAction]] = []
    for meters in vr_distance_options:
        a = QAction(f"{meters:.1f} m", distance_menu)
        a.setCheckable(True)
        distance_group.addAction(a)
        distance_menu.addAction(a)
        a.triggered.connect(lambda _checked, d=meters: on_set_vr_distance(d))
        distance_actions.append((meters, a))

    # Placement submenu: where the HUD quad floats in the headset. Head-
    # locked follows the gaze; fixed freezes the quad in the play space
    # right where it currently floats (re-selecting re-anchors at the
    # current gaze).
    placement_menu = vr_menu.addMenu(
        _with_shortcut("Placement", vr_placement_shortcut)
    )
    vr_group = QActionGroup(placement_menu)
    vr_group.setExclusive(True)
    # (internal mode, human label), alphabetical by label — mode strings
    # match VROverlayOutput; "dash" is kept as the persisted key for the
    # fixed mode for backward compatibility with existing settings files.
    _vr_modes = (("dash", "Fixed in place"), ("head", "Head-locked"))
    vr_actions: list[tuple[str, QAction]] = []
    for mode, label in _vr_modes:
        a = QAction(label, placement_menu)
        a.setCheckable(True)
        vr_group.addAction(a)
        placement_menu.addAction(a)
        a.triggered.connect(lambda _checked, m=mode: on_set_vr_placement(m))
        vr_actions.append((mode, a))

    # Size submenu (VR): the same widget-size cycle offered under Windows,
    # duplicated here because it scales the HUD in the headset too (bigger
    # widgets grab a bigger texture -> a bigger quad). Reset / click-through
    # stay desktop-only, but size is meaningful in both modes, so it lives
    # in both menus and shares one handler / hotkey. Enabled with the VR
    # category (only bites while the HUD renders into a headset).
    vr_size_menu = vr_menu.addMenu(_with_shortcut("Size", size_shortcut))
    vr_size_group = QActionGroup(vr_size_menu)
    vr_size_group.setExclusive(True)
    vr_size_actions: list[QAction] = []
    for idx, label in enumerate(size_labels):
        a = QAction(label, vr_size_menu)
        a.setCheckable(True)
        vr_size_group.addAction(a)
        vr_size_menu.addAction(a)
        a.triggered.connect(lambda _checked, i=idx: on_set_size(i))
        vr_size_actions.append(a)

    # Spread submenu: how wide the widgets fan out sideways around the
    # cylinder. 100% is the exact desktop layout; lower pulls the corner
    # widgets toward the centre of view (they can otherwise sit near the
    # edge of the FOV in a headset). Radio-button behaviour like Size.
    spread_menu = vr_menu.addMenu(_with_shortcut("Spread", vr_spread_shortcut))
    spread_group = QActionGroup(spread_menu)
    spread_group.setExclusive(True)
    spread_actions: list[tuple[float, QAction]] = []
    for factor in vr_spread_options:
        a = QAction(f"{round(factor * 100)}%", spread_menu)
        a.setCheckable(True)
        spread_group.addAction(a)
        spread_menu.addAction(a)
        a.triggered.connect(lambda _checked, f=factor: on_set_vr_spread(f))
        spread_actions.append((factor, a))

    # Per-widget show/hide, so individual panels can be dropped from the
    # headset HUD. Own action set (see _build_widgets_submenu); same
    # visibility flags as the Windows "Widgets" submenu below.
    vr_widget_actions = _build_widgets_submenu(vr_menu)

    # ---- Windows: on-screen overlay widget layout ------------------------
    # Entries are ordered alphabetically by label: Click-through, Reset
    # positions, Size, Widgets (Size values stay in scale order).
    windows_menu = menu.addMenu("Windows")

    click_through_action = QAction(
        _with_shortcut("Click-through", click_through_shortcut), windows_menu
    )
    click_through_action.setCheckable(True)
    # Discard the bool emitted by triggered — the window flips its own
    # state, and aboutToShow re-syncs the checkmark from the source of
    # truth so a toggle from anywhere stays in lockstep with the menu.
    click_through_action.triggered.connect(lambda _checked: on_toggle_click_through())
    windows_menu.addAction(click_through_action)

    reset_action = QAction(
        _with_shortcut("Reset positions", reset_shortcut), windows_menu
    )
    reset_action.triggered.connect(on_reset)
    windows_menu.addAction(reset_action)

    # The hint sits on the submenu title because the global hotkey *cycles*
    # through these entries rather than selecting one — it advances to the
    # next size and wraps around.
    size_menu = windows_menu.addMenu(_with_shortcut("Size", size_shortcut))
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

    # Per-widget show/hide on the desktop overlay. Own action set; same
    # visibility flags as the VR "Widgets" submenu.
    windows_widget_actions = _build_widgets_submenu(windows_menu)

    menu.addSeparator()

    update_action: QAction | None = None
    if updater is not None:
        text, enabled = _UPDATE_LABELS[updater.state]
        update_action = QAction(text, menu)
        update_action.setEnabled(enabled)

        def _on_update_clicked() -> None:
            # Dispatch by current state: from IDLE this kicks off a new
            # check; from READY it relaunches into the downloaded .exe.
            # CHECKING / DOWNLOADING render the action disabled so the
            # click can't reach this branch.
            if updater.state == UpdateController.READY:
                log_action("restart to update")
                updater.restart_into_update()
            elif updater.state == UpdateController.IDLE:
                log_action("check for updates")
                updater.start_check()

        update_action.triggered.connect(lambda _checked: _on_update_clicked())
        menu.addAction(update_action)

        def _on_update_state_changed(state: str, _detail: str) -> None:
            label = _UPDATE_LABELS.get(state)
            if label is None or update_action is None:
                return
            update_action.setText(label[0])
            update_action.setEnabled(label[1])

        updater.state_changed.connect(_on_update_state_changed)
        # Separator sits inside the guard so a build without an updater
        # doesn't render two adjacent separators before Quit.
        menu.addSeparator()

    quit_action = QAction(_with_shortcut("Quit", quit_shortcut), menu)
    quit_action.triggered.connect(on_quit)
    menu.addAction(quit_action)

    def _refresh_state() -> None:
        click_through_action.setChecked(is_click_through())
        cur = current_size_index()
        for i, a in enumerate(size_actions):
            a.setChecked(i == cur)
        # VR Size submenu mirrors the same current index (shared handler).
        for i, a in enumerate(vr_size_actions):
            a.setChecked(i == cur)
        # Widget show/hide checkmarks reflect live visibility. Both submenus
        # (VR and Windows) keep their own action set; sync each.
        for key, a in (*vr_widget_actions, *windows_widget_actions):
            a.setChecked(is_widget_visible(key))
        cur_hz = current_polling_hz()
        for hz, a in hz_actions:
            a.setChecked(hz == cur_hz)
        # The VR panel controls only do anything while the HUD is being
        # rendered into a headset — grey the whole VR category out on the
        # desktop overlay so it doesn't imply an effect it can't have. The
        # persisted choices are still applied the moment VR does start.
        vr_menu.menuAction().setEnabled(is_vr_active())
        # The Windows controls act on the on-screen overlay widgets, which
        # aren't placed until the source is detected and the countdown
        # reveals them — grey the category out until then so reset/size/
        # click-through can't fire against widgets that don't exist yet.
        # Windows and VR are also mutually exclusive: once VR starts the
        # desktop overlay is blanked (fully transparent) and the HUD lives
        # in the headset, tuned through the VR category — so the desktop-
        # oriented Windows controls (reset / click-through) act on a surface
        # the user can't see. Grey Windows out whenever VR is live (mirrors
        # VR being greyed out on the desktop overlay above). Size is the one
        # widget control that still matters in VR, so it's duplicated in the
        # VR category above rather than left stranded in this greyed-out one.
        windows_menu.menuAction().setEnabled(
            is_windows_ready() and not is_vr_active())
        # Polling Hz / logging only do anything once the source is live —
        # grey the Data category out until a game is detected so logging
        # can't be started against a source that isn't feeding frames yet.
        data_menu.menuAction().setEnabled(is_game_detected())
        cur_vr = current_vr_placement()
        for mode, a in vr_actions:
            a.setChecked(mode == cur_vr)
        cur_spread = current_vr_spread()
        for factor, a in spread_actions:
            a.setChecked(abs(factor - cur_spread) < 1e-6)
        cur_distance = current_vr_distance()
        for meters, a in distance_actions:
            a.setChecked(abs(meters - cur_distance) < 1e-6)
        logging_action.setText(
            _with_shortcut(
                "Stop logging" if is_logging() else "Start logging",
                logging_shortcut,
            )
        )

    # Re-read state every time the menu opens so checkmarks and the
    # start/stop logging label stay in lockstep with the floating buttons
    # and the global hotkeys.
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
