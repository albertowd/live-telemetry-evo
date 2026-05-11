from __future__ import annotations

import argparse
import sys

from PySide6.QtWidgets import QApplication

from .layout import ScreenLayout, compute_layout, pick_resolution
from .settings import (delete_entries, load_positions, load_size_index,
                        load_visibility, save_position, save_size_index,
                        save_visibility)
from .sources import make_source
from .telemetry import TelemetryFrame
from .tray import make_tray
from .widgets.countdown import CountdownView
from .widgets.detection import DetectionView
from .widgets.engine_view import EngineView
from .widgets.inputs_view import InputsView
from .widgets.wheel_view import WheelView
from .window import (HOTKEY_QUIT_LABEL, HOTKEY_RESET_LABEL, HOTKEY_SIZE_LABEL,
                     HOTKEY_TOGGLE_LABEL, OverlayWindow)


_RESETTABLE_IDS = ("engine", "inputs", "FL", "FR", "RL", "RR")

# Scale factors applied on top of the auto-detected resolution multiplier.
# Index 2 ("M") is 1.0 — i.e. matches the original auto-picked size.
SIZE_FACTORS: tuple[float, ...] = (0.5, 0.75, 1.0, 1.25, 1.5)
SIZE_LABELS: tuple[str, ...] = ("XS", "S", "M", "L", "XL")
DEFAULT_SIZE_INDEX = 2


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
    # Phase-3 widget hidden by default for now — Ctrl+Alt+R / tray Reset
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


def _on_frame(frame: TelemetryFrame, engine: EngineView,
              inputs: InputsView, wheels: dict[str, WheelView]) -> None:
    engine.set_data(frame.engine)
    inputs.set_data(frame.inputs)
    for wid, view in wheels.items():
        view.set_data(frame.wheels[wid])


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="overlay", description="AC Evo telemetry overlay")
    parser.add_argument(
        "--source",
        choices=("auto", "synthetic", "ac-evo", "ac1", "acc", "acrally"),
        default="auto",
        help=("telemetry source: 'auto' (default, detect the running game), "
              "'ac-evo' (Assetto Corsa Evo), 'ac1' (original Assetto Corsa), "
              "'acc' (Assetto Corsa Competizione), 'acrally' (Assetto Corsa "
              "Rally), or 'synthetic' (mock data)"),
    )
    parser.add_argument("--hz", type=int, default=60, help="sample rate in Hz (default: 60)")
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    app = QApplication(sys.argv)
    # Sets the per-user config directory QStandardPaths resolves for our
    # positions.json — without this, Qt falls back to a generic "QtProject"
    # path which is harder to find when the user wants to clear it.
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

    # Global hotkeys (registered in window.py via Win32 RegisterHotKey).
    window.reset_hotkey.connect(_do_reset)
    window.size_hotkey.connect(_cycle_size)

    # System-tray icon: reset / click-through / size submenu / quit.
    # Held by ``window`` so it lives as long as the overlay does.
    window._tray = make_tray(
        window,
        on_reset=_do_reset,
        on_toggle_click_through=window.toggle_click_through,
        is_click_through=lambda: window.click_through,
        on_set_size=_set_size,
        current_size_index=lambda: size_idx,
        size_labels=SIZE_LABELS,
        on_quit=app.quit,
        reset_shortcut=HOTKEY_RESET_LABEL,
        click_through_shortcut=HOTKEY_TOGGLE_LABEL,
        size_shortcut=HOTKEY_SIZE_LABEL,
        quit_shortcut=HOTKEY_QUIT_LABEL,
    )

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
        if visibility.get("engine", True):
            engine.show()
        # Inputs widget stays hidden after the countdown — see _apply_layout.
        for wid, view in wheels.items():
            if visibility.get(wid, True):
                view.show()

    countdown.finished.connect(_reveal_widgets)

    def _start_source(name: str) -> None:
        source = make_source(name, hz=args.hz, parent=window)
        source.frame.connect(lambda f: _on_frame(f, engine, inputs, wheels))
        source.start()
        # Keep a reference on the window so the QObject isn't GC'd when
        # this closure returns.
        window._source = source
        print(
            f"[overlay] source={name} hz={args.hz} "
            f"screen={geom.width()}x{geom.height()} "
            f"resolution={layout.resolution_name} multiplier={layout.multiplier:.2f} "
            f"engine={layout.engine.w}x{layout.engine.h} "
            f"wheel={layout.wheels['FL'].w}x{layout.wheels['FL'].h}"
        )
        countdown.start()

    window.show()
    # Default to click-through ON: a full-screen overlay must not steal mouse
    # input from the game underneath. User can toggle with Ctrl+Alt+L.
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
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
