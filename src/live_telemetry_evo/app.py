from __future__ import annotations

import argparse
import sys
import time
from typing import Callable, Sequence

from PySide6.QtCore import QThread, QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from .frame_bus import FrameBus
from .layout import ScreenLayout, compute_layout, pick_resolution
from .logger import CsvLogger
from .settings import (delete_entries, load_polling_hz, load_positions,
                        load_size_index, load_visibility, load_vr_distance,
                        load_vr_placement, load_vr_spread, save_polling_hz,
                        save_position, save_size_index, save_visibility,
                        save_vr_distance, save_vr_placement, save_vr_spread)
from .sources import make_source
from .telemetry import TelemetryFrame
from .tray import make_tray
from .updater import UpdateController
from .vr import detect as vr_detect
from .vr.overlay_output import VROverlayOutput
from .widgets.countdown import CountdownView
from .widgets.detection import DetectionView
from .widgets.engine_view import EngineView
from .widgets.inputs_view import InputsView
from .widgets.wheel_view import WheelView
from .window import (HOTKEY_CLICK_LABEL, HOTKEY_LOG_LABEL, HOTKEY_QUIT_LABEL,
                     HOTKEY_RESET_LABEL, HOTKEY_SIZE_LABEL,
                     HOTKEY_VR_DISTANCE_LABEL, HOTKEY_VR_PLACEMENT_LABEL,
                     HOTKEY_VR_SPREAD_LABEL, OverlayWindow)


# Polling rates exposed in the tray submenu. The source's QTimer runs at
# the chosen Hz on its dedicated worker thread; UI repaint is independent
# (display refresh rate). 60 is the default — matches AC's physics step
# and keeps the EMA-smoothed derivatives (e.g. kers_deploy_kw) tight.
POLLING_HZ_OPTIONS: tuple[int, ...] = (30, 60, 100, 120, 144, 250)
DEFAULT_POLLING_HZ = 60


_RESETTABLE_IDS = ("engine", "inputs", "FL", "FR", "RL", "RR")

# Scale factors applied on top of the auto-detected resolution multiplier.
# Index 2 ("M") is 1.0 — i.e. matches the original auto-picked size.
SIZE_FACTORS: tuple[float, ...] = (0.5, 0.75, 1.0, 1.25, 1.5)
SIZE_LABELS: tuple[str, ...] = ("XS", "S", "M", "L", "XL")
DEFAULT_SIZE_INDEX = 2


# VR panel geometry exposed in the tray. Spread is the horizontal fan-out
# factor (1.0 == exact desktop layout); distance is how far the panel
# floats from the viewer, in metres (the cylinder radius). Defaults mirror
# HORIZONTAL_SPREAD / CYLINDER_RADIUS_M in vr.overlay_output.
VR_SPREAD_OPTIONS: tuple[float, ...] = (
    0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2)
DEFAULT_VR_SPREAD = 0.8
VR_DISTANCE_OPTIONS: tuple[float, ...] = (1.0, 1.2, 1.4, 1.6, 1.8, 2.0)
DEFAULT_VR_DISTANCE = 1.4

# Placement modes the VR placement hotkey cycles through, in order. Keys
# match the persisted values driving the tray placement submenu ("dash" is
# the fixed-in-place mode, kept for settings backward-compatibility).
VR_PLACEMENT_MODES: tuple[str, ...] = ("head", "dash")


def _next_option(options: Sequence[float], current: float) -> float:
    """Return the option after ``current`` (wrapping to the first), matched
    with a float tolerance. Falls back to the first option when ``current``
    is not in the list — e.g. a persisted value that is no longer offered."""
    for i, opt in enumerate(options):
        if abs(opt - current) < 1e-6:
            return options[(i + 1) % len(options)]
    return options[0]


# Anchor corner per widget — kept stable across size cycles so a widget
# the user dragged into a particular corner stays pinned to that corner
# when growing/shrinking. Going back to the original size recovers the
# exact original position (no drift from edge-clamping during cycles).
_ANCHORS: dict[str, tuple[str, str]] = {
    "engine": ("center", "bottom"),
    "inputs": ("center", "top"),
    "FL": ("left", "top"),
    "FR": ("right", "top"),
    "RL": ("left", "bottom"),
    "RR": ("right", "bottom"),
}


def _resolve_xy(saved: dict[str, tuple[int, int]],
                wid: str, default_x: int, default_y: int,
                w: int, h: int, screen_w: int, screen_h: int) -> tuple[int, int]:
    """Use the saved position only if the widget would land fully on-screen
    at the current geometry; otherwise fall back to the layout default."""
    if wid in saved:
        x, y = saved[wid]
        if 0 <= x and x + w <= screen_w and 0 <= y and y + h <= screen_h:
            return x, y
    return default_x, default_y


def _default_pos(wid: str, layout: ScreenLayout) -> tuple[int, int, int, int]:
    """Return (x, y, w, h) for the layout default of a given widget id."""
    if wid == "engine":
        p = layout.engine
    elif wid == "inputs":
        p = layout.inputs
    else:
        p = layout.wheels[wid]
    return p.x, p.y, p.w, p.h


def _apply_layout(
    window: OverlayWindow,
    engine: EngineView,
    inputs: InputsView,
    wheels: dict[str, WheelView],
    layout: ScreenLayout,
) -> None:
    """Stretch the overlay across the screen and place widgets at corners.

    Saved positions are honoured when they fit on the current screen;
    anything off-screen (e.g. resolution change since last run) reverts
    to the computed default for that screen. Visibility flags persist
    too — closing a widget hides it across sessions until reset.
    """
    saved = load_positions()
    visibility = load_visibility()
    window.setGeometry(0, 0, layout.screen_w, layout.screen_h)

    def _place(wid: str, view, default_x: int, default_y: int,
               w: int, h: int) -> None:
        view.setParent(window)
        x, y = _resolve_xy(saved, wid, default_x, default_y, w, h,
                           layout.screen_w, layout.screen_h)
        view.setGeometry(x, y, w, h)

    _place("engine", engine, *_default_pos("engine", layout))
    engine.moved_to.connect(lambda x, y: save_position("engine", x, y))
    engine.closed.connect(lambda: (engine.hide(), save_visibility("engine", False)))
    if visibility.get("engine", True):
        engine.show()
    else:
        engine.hide()

    _place("inputs", inputs, *_default_pos("inputs", layout))
    inputs.moved_to.connect(lambda x, y: save_position("inputs", x, y))
    inputs.closed.connect(lambda: (inputs.hide(), save_visibility("inputs", False)))
    # Phase-3 widget hidden by default for now — Ctrl+Shift+R / tray Reset
    # brings it back when the user wants to see it.
    inputs.hide()

    for wid, view in wheels.items():
        _place(wid, view, *_default_pos(wid, layout))
        # Default-arg trick binds the loop variable into each lambda;
        # otherwise all four would close over the last value of `wid`.
        view.moved_to.connect(lambda x, y, k=wid: save_position(k, x, y))
        view.closed.connect(lambda v=view, k=wid:
                            (v.hide(), save_visibility(k, False)))
        if visibility.get(wid, True):
            view.show()
        else:
            view.hide()


def _reset_layout(engine: EngineView, inputs: InputsView,
                  wheels: dict[str, WheelView],
                  layout: ScreenLayout) -> None:
    """Restore every overlay widget to its default position and shown
    state, and wipe persisted entries for them."""
    delete_entries(list(_RESETTABLE_IDS))
    engine.setGeometry(*_default_pos("engine", layout))
    engine.show()
    inputs.setGeometry(*_default_pos("inputs", layout))
    inputs.show()
    for wid, view in wheels.items():
        view.setGeometry(*_default_pos(wid, layout))
        view.show()


def _anchor_resize(view, wid: str, new_w: int, new_h: int,
                   screen_w: int, screen_h: int) -> None:
    """Resize a widget to (``new_w``, ``new_h``) while pinning the corner
    declared in ``_ANCHORS`` for ``wid``. A size cycle is round-trippable:
    going M → L → M lands the widget back where it started."""
    ax_kind, ay_kind = _ANCHORS[wid]
    old_x, old_y = view.x(), view.y()
    old_w, old_h = view.width(), view.height()

    if ax_kind == "left":
        new_x = old_x
    elif ax_kind == "right":
        new_x = old_x + old_w - new_w
    else:  # center
        new_x = int(old_x + old_w / 2 - new_w / 2)

    if ay_kind == "top":
        new_y = old_y
    elif ay_kind == "bottom":
        new_y = old_y + old_h - new_h
    else:
        new_y = int(old_y + old_h / 2 - new_h / 2)

    new_x = max(0, min(screen_w - new_w, int(new_x)))
    new_y = max(0, min(screen_h - new_h, int(new_y)))
    view.setGeometry(new_x, new_y, new_w, new_h)


def _resize_widgets(engine: EngineView, inputs: InputsView,
                    wheels: dict[str, WheelView],
                    layout: ScreenLayout) -> None:
    """Re-apply layout-computed dimensions for engine + inputs + wheels."""
    _anchor_resize(engine, "engine", layout.engine.w, layout.engine.h,
                   layout.screen_w, layout.screen_h)
    _anchor_resize(inputs, "inputs", layout.inputs.w, layout.inputs.h,
                   layout.screen_w, layout.screen_h)
    for wid, view in wheels.items():
        place = layout.wheels[wid]
        _anchor_resize(view, wid, place.w, place.h,
                       layout.screen_w, layout.screen_h)


def _dispatch_frame(frame: TelemetryFrame, engine: EngineView,
                    inputs: InputsView, wheels: dict[str, WheelView]) -> None:
    """Push the latest frame to every widget. Called from the UI-side
    repaint timer (display refresh rate), not from the polling thread —
    so widget paint events stay decoupled from SHM read latency."""
    engine.set_data(frame.engine)
    inputs.set_data(frame.inputs)
    for wid, view in wheels.items():
        view.set_data(frame.wheels[wid])


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="live-telemetry-evo",
        description="Assetto Corsa telemetry overlay (AC1 / ACC / AC Evo / AC Rally)",
    )
    parser.add_argument(
        "--source",
        choices=("auto", "synthetic", "ac-evo", "ac1", "acc", "acrally"),
        default="auto",
        help=("telemetry source: 'auto' (default, detect the running game), "
              "'ac-evo' (Assetto Corsa Evo), 'ac1' (original Assetto Corsa), "
              "'acc' (Assetto Corsa Competizione), 'acrally' (Assetto Corsa "
              "Rally), or 'synthetic' (mock data)"),
    )
    parser.add_argument("--hz", type=int, default=0,
                        help=("polling rate in Hz; 0 = use the value persisted in "
                              "settings (default 60). Allowed live values: "
                              "30/60/100/120/144/250."))
    vr_group = parser.add_mutually_exclusive_group()
    vr_group.add_argument(
        "--vr", action="store_true",
        help=("force the VR overlay on: submit the HUD to SteamVR as an "
              "overlay quad. Falls back to the desktop overlay if SteamVR / "
              "the OpenVR runtime isn't available."))
    vr_group.add_argument(
        "--novr", action="store_true",
        help=("force the VR overlay off, disabling SteamVR auto-detection "
              "(pure desktop overlay). Default is to auto-detect SteamVR and "
              "enable VR when a headset is present."))
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    app = QApplication(sys.argv)
    # Config + logs live next to the executable (or in CWD during dev)
    # — see ``overlay.paths``. These identifiers no longer affect file
    # locations; they're kept so any QStandardPaths-aware Qt component
    # (taskbar grouping, native dialogs) has a sensible app identity.
    app.setOrganizationName("LiveTelemetryEvo")
    app.setApplicationName("Overlay")

    screen = app.primaryScreen()
    geom = screen.availableGeometry()

    window = OverlayWindow()

    engine = EngineView()
    inputs = InputsView()
    wheels = {wid: WheelView(wid) for wid in ("FL", "FR", "RL", "RR")}

    # ``size_idx`` and ``layout`` are mutated by the size-cycle handler
    # below; closures here read the current value via ``nonlocal``.
    size_idx = load_size_index(DEFAULT_SIZE_INDEX, len(SIZE_FACTORS))
    base_mult = pick_resolution(geom.height())[1]
    actual_mult = base_mult * SIZE_FACTORS[size_idx]
    layout = compute_layout(geom.width(), geom.height(), multiplier=actual_mult)
    _apply_layout(window, engine, inputs, wheels, layout)
    window.move(geom.x(), geom.y())

    def _set_size(idx: int) -> None:
        nonlocal size_idx, layout
        idx = max(0, min(len(SIZE_FACTORS) - 1, int(idx)))
        size_idx = idx
        save_size_index(idx)
        new_mult = base_mult * SIZE_FACTORS[idx]
        layout = compute_layout(geom.width(), geom.height(), multiplier=new_mult)
        _resize_widgets(engine, inputs, wheels, layout)

    def _cycle_size() -> None:
        _set_size((size_idx + 1) % len(SIZE_FACTORS))

    def _do_reset() -> None:
        _reset_layout(engine, inputs, wheels, layout)

    # --- Telemetry transport: bus + repaint timer + worker thread ----
    bus = FrameBus()
    logger = CsvLogger(bus)
    # Tracks the source name passed in via auto-detect / CLI so the
    # logger can stamp the CSV filename with it. Filled in by
    # ``_start_source``.
    current_source_name = ["unknown"]

    # UI-side repaint at display refresh rate. QScreen.refreshRate()
    # returns Hz as a float (60.0, 144.0, etc.); fall back to 60 when
    # the platform doesn't report a real rate.
    refresh_hz = max(30.0, float(screen.refreshRate() or 60.0))
    repaint_timer = QTimer(window)
    repaint_timer.setInterval(int(1000 / refresh_hz))

    def _on_repaint() -> None:
        f = bus.latest()
        if f is None:
            return
        _dispatch_frame(f, engine, inputs, wheels)

    # pylint: disable-next=no-member  # QTimer.timeout is a PySide6 Signal
    repaint_timer.timeout.connect(_on_repaint)
    # Started after the source goes live so we don't repaint before the
    # bus has anything; ``_start_source`` flips it on.

    # Polling Hz — persisted choice trumps the CLI when --hz is 0 (the
    # default). An explicit ``--hz N`` from the CLI overrides for this
    # session but is also persisted so the tray submenu reflects it.
    polling_hz = load_polling_hz(DEFAULT_POLLING_HZ, POLLING_HZ_OPTIONS)
    if args.hz and args.hz in POLLING_HZ_OPTIONS:
        polling_hz = args.hz
        save_polling_hz(polling_hz)

    def _set_polling_hz(hz: int) -> None:
        nonlocal polling_hz
        if hz not in POLLING_HZ_OPTIONS:
            return
        polling_hz = hz
        save_polling_hz(hz)
        src = getattr(window, "_source", None)
        if src is not None:
            # Queued signal → worker thread mutates its own QTimer.
            src.hz_change_requested.emit(hz)

    # Flips True once a game is detected and the source starts feeding the
    # bus (``_start_source``). Guards logging and greys out the tray "Data"
    # category until then — there is nothing to poll or log before a source
    # is live, and a CSV started early would just capture empty frames.
    game_detected = [False]

    def _toggle_logging() -> None:
        # No source yet → nothing to log. The tray Data menu is greyed out
        # in this state, but the global hotkey can still fire, so guard here.
        if not game_detected[0]:
            return
        if logger.is_active():
            logger.stop()
            print(f"[overlay] logging stopped (dropped rows: {bus.csv_dropped})")
        else:
            path = logger.start(current_source_name[0])
            print(f"[overlay] logging started: {path}")

    def _open_logs_folder() -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(CsvLogger.logs_dir())))

    # Flips True once the countdown reveals the overlay widgets — the tray
    # "Windows" category (reset / size / click-through) stays greyed out
    # until then, since there are no placed widgets to act on during
    # detection / countdown.
    widgets_ready = [False]

    # Global hotkeys (registered in window.py via Win32 RegisterHotKey).
    # Each signal shares its handler with the matching tray-menu entry, so
    # firing the hotkey and clicking the menu take exactly one path — and
    # each key is gated by the same readiness state that enables its menu
    # entry, so a hotkey never acts while its menu item is greyed out.
    # Reset / size / click-through act on the overlay widgets (Windows menu,
    # ready once the countdown reveals them); logging guards on
    # ``game_detected`` inside ``_toggle_logging`` (Data menu). Quit has no
    # gate, matching its always-enabled menu entry.
    def _when_widgets_ready(fn: Callable[[], None]) -> Callable[[], None]:
        return lambda: fn() if widgets_ready[0] else None

    # Reset / click-through are desktop-only: reset re-lays the on-screen
    # windows and click-through toggles a desktop-window flag, neither of
    # which the user can act on from inside the headset — so they're also
    # gated on VR being off, mirroring the greyed-out tray Windows category.
    # Size is the exception: it scales the HUD in VR too (bigger widgets ->
    # bigger grabbed texture -> bigger quad), so it stays available in both
    # modes and only needs the widgets-ready gate. (``vr`` is bound further
    # down but only read when a key fires, so the forward reference is fine.)
    def _when_desktop_widgets_ready(fn: Callable[[], None]) -> Callable[[], None]:
        return lambda: fn() if widgets_ready[0] and not vr.is_running() else None

    window.reset_hotkey.connect(_when_desktop_widgets_ready(_do_reset))
    window.log_hotkey.connect(_toggle_logging)
    window.size_hotkey.connect(_when_widgets_ready(_cycle_size))
    window.click_through_hotkey.connect(
        _when_desktop_widgets_ready(window.toggle_click_through)
    )
    # Release the Win32 hotkeys on every quit path. ``QApplication.quit()``
    # (tray Quit, Ctrl+Shift+Q) exits the event loop without delivering a
    # ``closeEvent``, so the window's own cleanup never runs — leaving the
    # hotkeys registered to a thread that may linger as an orphaned process
    # and blocking the next launch with ERROR_HOTKEY_ALREADY_REGISTERED (1409).
    # pylint: disable-next=no-member
    app.aboutToQuit.connect(window._unregister_hotkeys)

    # --- VR output: SteamVR overlay quad mirroring the HUD ------------
    # The overlay window's widgets are submitted to the OpenVR compositor
    # so they appear inside the headset (a desktop window is invisible in
    # VR). Started later per --vr / --novr / auto-detect; placement is
    # persisted and switchable from the tray submenu below.
    vr = VROverlayOutput(
        placement=load_vr_placement(),
        spread=load_vr_spread(DEFAULT_VR_SPREAD, VR_SPREAD_OPTIONS),
        distance=load_vr_distance(DEFAULT_VR_DISTANCE, VR_DISTANCE_OPTIONS),
    )

    def _set_vr_placement(mode: str) -> None:
        save_vr_placement(mode)
        vr.set_placement(mode)

    def _set_vr_spread(factor: float) -> None:
        save_vr_spread(factor)
        vr.set_spread(factor)

    def _set_vr_distance(meters: float) -> None:
        save_vr_distance(meters)
        vr.set_distance(meters)

    # --- VR hotkey cycle handlers -------------------------------------
    # The tray VR submenus expose discrete choices; the global hotkeys step
    # forward through them (wrapping) so the whole control is reachable from
    # inside the headset, where the tray can't be clicked. Each routes back
    # through the matching _set_vr_* above, so persistence and live-apply
    # stay identical to picking the entry from the menu.
    def _cycle_vr_placement() -> None:
        cur = vr.placement
        idx = VR_PLACEMENT_MODES.index(cur) if cur in VR_PLACEMENT_MODES else 0
        _set_vr_placement(VR_PLACEMENT_MODES[(idx + 1) % len(VR_PLACEMENT_MODES)])

    def _cycle_vr_spread() -> None:
        _set_vr_spread(_next_option(VR_SPREAD_OPTIONS, vr.spread))

    def _cycle_vr_distance() -> None:
        _set_vr_distance(_next_option(VR_DISTANCE_OPTIONS, vr.distance))

    # Gate the VR hotkeys on the same state that enables the tray VR
    # category (is_vr_active == vr.is_running), so a key never acts while
    # its menu entry is greyed out on the desktop overlay.
    def _when_vr_active(fn: Callable[[], None]) -> Callable[[], None]:
        return lambda: fn() if vr.is_running() else None

    window.vr_placement_hotkey.connect(_when_vr_active(_cycle_vr_placement))
    window.vr_spread_hotkey.connect(_when_vr_active(_cycle_vr_spread))
    window.vr_distance_hotkey.connect(_when_vr_active(_cycle_vr_distance))

    # System-tray icon: reset / click-through / size submenu /
    # polling Hz submenu / quit. Held by ``window`` so it lives as long
    # as the overlay does.
    # Updater is built before the tray so the tray's "Check for Updates"
    # action can subscribe to controller state transitions and dispatch
    # IDLE-click → start_check / READY-click → restart_into_update.
    window._updater = UpdateController(window)

    window._tray = make_tray(
        window,
        on_reset=_do_reset,
        on_toggle_click_through=window.toggle_click_through,
        is_click_through=lambda: window.click_through,
        on_set_size=_set_size,
        current_size_index=lambda: size_idx,
        size_labels=SIZE_LABELS,
        on_set_polling_hz=_set_polling_hz,
        current_polling_hz=lambda: polling_hz,
        polling_hz_options=POLLING_HZ_OPTIONS,
        on_set_vr_placement=_set_vr_placement,
        current_vr_placement=lambda: vr.placement,
        on_set_vr_spread=_set_vr_spread,
        current_vr_spread=lambda: vr.spread,
        vr_spread_options=VR_SPREAD_OPTIONS,
        on_set_vr_distance=_set_vr_distance,
        current_vr_distance=lambda: vr.distance,
        vr_distance_options=VR_DISTANCE_OPTIONS,
        is_vr_active=vr.is_running,
        is_windows_ready=lambda: widgets_ready[0],
        is_game_detected=lambda: game_detected[0],
        on_toggle_logging=_toggle_logging,
        is_logging=logger.is_active,
        on_open_logs_folder=_open_logs_folder,
        on_quit=app.quit,
        updater=window._updater,
        reset_shortcut=HOTKEY_RESET_LABEL,
        quit_shortcut=HOTKEY_QUIT_LABEL,
        logging_shortcut=HOTKEY_LOG_LABEL,
        size_shortcut=HOTKEY_SIZE_LABEL,
        click_through_shortcut=HOTKEY_CLICK_LABEL,
        vr_placement_shortcut=HOTKEY_VR_PLACEMENT_LABEL,
        vr_spread_shortcut=HOTKEY_VR_SPREAD_LABEL,
        vr_distance_shortcut=HOTKEY_VR_DISTANCE_LABEL,
    )

    def _on_update_downloaded(tag: str, path: str) -> None:
        # Tray balloon fires only on a fresh download — already_present
        # (file from a prior session) would otherwise spam this on every
        # startup once the .exe is on disk. The user still sees
        # "Restart to Update" in the tray menu either way.
        tray = getattr(window, "_tray", None)
        if tray is None or not tray.supportsMessages():
            return
        from pathlib import Path as _Path  # local import: rarely used
        tray.showMessage(
            f"Live Telemetry Evo {tag} downloaded",
            f"{_Path(path).name} was saved next to the current app. "
            "Open the tray menu and click \"Restart to Update\" "
            "when you're ready.",
            QSystemTrayIcon.MessageIcon.Information,
            10_000,
        )

    # pylint: disable-next=no-member
    window._updater.download_finished.connect(_on_update_downloaded)

    # Kick off the auto-check at startup. The controller is non-blocking
    # (worker thread) so telemetry detection / countdown / overlay
    # rendering proceed while the request is in flight.
    window._updater.start_check()

    # Hide the telemetry widgets during the countdown — they reveal when
    # the countdown finishes (subject to the persisted visibility flag).
    # The source still feeds frames the whole time so widgets show live
    # data the instant they appear.
    visibility = load_visibility()
    engine.hide()
    inputs.hide()
    for view in wheels.values():
        view.hide()

    countdown = CountdownView(window)
    countdown.setGeometry(0, 0, layout.screen_w, layout.screen_h)
    countdown.raise_()  # ensure it sits above any pre-shown chrome

    def _reveal_widgets() -> None:
        widgets_ready[0] = True
        if visibility.get("engine", True):
            engine.show()
        # Inputs widget stays hidden after the countdown — see _apply_layout.
        for wid, view in wheels.items():
            if visibility.get(wid, True):
                view.show()

    countdown.finished.connect(_reveal_widgets)

    def _start_source(name: str) -> None:
        current_source_name[0] = name
        # A game is now detected: unlock the tray Data category and let the
        # logging hotkey through (see ``_toggle_logging`` / the tray guard).
        game_detected[0] = True
        # Build the source on the UI thread but with no parent — Qt
        # forbids moveToThread on a parented object. Wire the bus before
        # the thread starts; ``set_bus`` is plain Python so the worker
        # sees the attribute as soon as it begins ticking.
        source = make_source(name, hz=polling_hz, parent=None)
        source.set_bus(bus)

        thread = QThread()
        source.moveToThread(thread)
        # ``thread.started`` fires on the worker thread, so ``start()``
        # builds the source's QTimer there — required because Qt timers
        # only tick on the thread they were started from.
        # pylint: disable-next=no-member
        thread.started.connect(source.start)
        # Clean shutdown when the app quits. The CSV writer is joined
        # first so its final flush doesn't race the bus teardown. Then
        # ``stop_requested`` (blocking queued) runs ``source.stop()`` on
        # the worker thread before the loop is quit and joined — quitting
        # first could drop the queued stop, leaving the polling QTimer
        # alive to be destroyed cross-thread at interpreter teardown
        # ("QObject::killTimer: Timers cannot be stopped from another
        # thread").
        def _shutdown() -> None:
            logger.stop()
            if thread.isRunning():
                source.stop_requested.emit()
                thread.quit()
                thread.wait(2000)

        # pylint: disable-next=no-member
        app.aboutToQuit.connect(_shutdown)
        thread.start()
        # Keep references on the window so the QObjects aren't GC'd when
        # this closure returns.
        window._source = source
        window._source_thread = thread
        # Now that frames will start flowing, kick the UI-side paint
        # loop — unless VR is already live, where the VR tick owns frame
        # dispatch and the desktop overlay is invisible.
        if not vr.is_running():
            repaint_timer.start()

        print(
            f"[overlay] source={name} polling_hz={polling_hz} "
            f"repaint_hz={refresh_hz:.0f} "
            f"screen={geom.width()}x{geom.height()} "
            f"resolution={layout.resolution_name} multiplier={layout.multiplier:.2f} "
            f"engine={layout.engine.w}x{layout.engine.h} "
            f"wheel={layout.wheels['FL'].w}x{layout.wheels['FL'].h}"
        )
        countdown.start(name)

    window.show()
    # Default to click-through ON: a full-screen overlay must not steal mouse
    # input from the game underneath. Toggle from the tray (Windows menu).
    window.toggle_click_through()

    if args.source == "auto":
        detection = DetectionView(window)
        detection.setGeometry(0, 0, layout.screen_w, layout.screen_h)
        detection.raise_()
        detection.detected.connect(_start_source)
        window._detection = detection
        detection.start()
    else:
        _start_source(args.source)

    # --- VR enable/teardown wiring ------------------------------------
    # One timer owns the whole VR frame pipeline: dispatch the latest
    # telemetry into the widgets, grab them, composite, upload. Folding
    # the dispatch in (instead of leaving the repaint timer running)
    # matters at high rates — two timers on the UI thread starve each
    # other once a tick costs more than the interval, and the headset
    # then streams stale widget pixels. Re-armed to the HMD's display
    # refresh rate when VR enables (45 Hz placeholder until then), and
    # stepped down automatically when the measured tick cost can't
    # sustain the interval. Precise timer type — a coarse timer's 5 %
    # batching on Windows would cap a ~11 ms interval near 60 Hz.
    vr_submit_timer = QTimer(window)
    vr_submit_timer.setTimerType(Qt.TimerType.PreciseTimer)
    vr_submit_timer.setInterval(int(1000 / 45))
    vr_tick_ema_ms = [0.0]  # smoothed tick cost, for the step-down

    def _vr_submit() -> None:
        if not vr.is_running():
            return
        t0 = time.perf_counter()
        f = bus.latest()
        if f is not None:
            _dispatch_frame(f, engine, inputs, wheels)
        t1 = time.perf_counter()
        items = []
        for key, view in (("engine", engine), ("inputs", inputs), *wheels.items()):
            if not view.isVisible():
                continue
            g = view.geometry()
            items.append((key, view.grab().toImage(),
                          g.x(), g.y(), g.width(), g.height()))
        t2 = time.perf_counter()
        vr.submit_widgets(items, layout.screen_w, layout.screen_h)
        # Adaptive pacing: when a whole tick costs more than ~80 % of the
        # interval the UI thread has no headroom left for anything else
        # (tray, hotkeys, detection), so halve the rate instead of
        # letting the event loop saturate. Steps 90 → 45 → 22 and stays
        # there; never throttles below ~20 Hz.
        cost_ms = (time.perf_counter() - t0) * 1000.0
        ema = vr_tick_ema_ms[0]
        ema = cost_ms if ema == 0.0 else ema * 0.9 + cost_ms * 0.1
        vr_tick_ema_ms[0] = ema
        interval = vr_submit_timer.interval()
        if ema > interval * 0.8 and interval < 50:
            vr_submit_timer.setInterval(interval * 2)
            vr_tick_ema_ms[0] = 0.0
            pump_ms, composite_ms, upload_ms = vr.phase_ms
            print(f"[overlay] VR tick costs {ema:.1f} ms "
                  f"(dispatch {(t1 - t0) * 1000:.1f} + "
                  f"grab {(t2 - t1) * 1000:.1f} + pump {pump_ms:.1f} + "
                  f"composite {composite_ms:.1f} + upload {upload_ms:.1f}); "
                  f"pacing down to {1000 / (interval * 2):.0f} Hz")

    # pylint: disable-next=no-member  # QTimer.timeout is a PySide6 Signal
    vr_submit_timer.timeout.connect(_vr_submit)
    # pylint: disable-next=no-member
    app.aboutToQuit.connect(vr.stop)

    def _enable_vr() -> bool:
        if not vr.start():
            return False
        # Pace the VR tick at the headset's display rate (fall back to
        # the desktop refresh rate when the runtime doesn't report one).
        # The VR tick dispatches telemetry itself, so the desktop repaint
        # timer is stopped — the desktop overlay is invisible anyway and
        # a second timer would just compete for the UI thread.
        hmd_hz = vr.display_hz() or refresh_hz
        vr_submit_timer.setInterval(max(1, round(1000 / hmd_hz)))
        vr_tick_ema_ms[0] = 0.0
        repaint_timer.stop()
        print(f"[overlay] VR refresh: {hmd_hz:.0f} Hz")
        # Hide the × buttons — the grab would bake them into the headset
        # quad, and there is no pointer in VR to click them.
        for view in (engine, inputs, *wheels.values()):
            view.set_close_visible(False)
        vr_submit_timer.start()
        # VR is live: the HUD is rendered inside the headset, so blank the
        # desktop overlay entirely — a duplicate copy floating on the
        # monitor is just clutter. Full window transparency (rather than
        # hiding the widgets) keeps each widget rendering, so ``_vr_submit``
        # can still ``grab()`` real pixels and ``isVisible()`` stays True.
        window.setWindowOpacity(0.0)
        return True

    # Resolve mode: --novr off, --vr force, neither = auto-detect.
    if args.novr:
        vr_mode = "off"
    elif args.vr:
        vr_mode = "force"
    else:
        vr_mode = "auto"

    if vr_mode == "force":
        if not _enable_vr():
            print("[overlay] VR requested but unavailable; "
                  "staying on desktop overlay")
    elif vr_mode == "auto" and VROverlayOutput.available():
        # Poll for a live SteamVR session; flip into VR on first detection,
        # then stop polling. Mirrors DetectionView's poll-and-start idiom.
        vr_detect_timer = QTimer(window)
        vr_detect_timer.setInterval(2000)

        def _vr_detect_tick() -> None:
            if vr_detect.vr_active():
                vr_detect_timer.stop()
                if _enable_vr():
                    print("[overlay] SteamVR detected; VR overlay enabled")

        # pylint: disable-next=no-member
        vr_detect_timer.timeout.connect(_vr_detect_tick)
        vr_detect_timer.start()
        window._vr_detect_timer = vr_detect_timer

    # Keep references on the window so the QObjects survive past run()'s scope.
    window._vr = vr
    window._vr_submit_timer = vr_submit_timer

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
