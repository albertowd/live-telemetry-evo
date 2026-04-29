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
from .widgets.countdown import CountdownView
from .widgets.engine_view import EngineView
from .widgets.reset_button import ResetButton
from .widgets.size_button import (DEFAULT_SIZE_INDEX, SIZE_FACTORS,
                                   SizeButton)
from .widgets.wheel_view import WheelView
from .window import OverlayWindow


_RESETTABLE_IDS = ("engine", "FL", "FR", "RL", "RR")


# Anchor corner per widget — kept stable across size cycles so a widget
# the user dragged into a particular corner stays pinned to that corner
# when growing/shrinking. Going back to the original size recovers the
# exact original position (no drift from edge-clamping during cycles).
_ANCHORS: dict[str, tuple[str, str]] = {
    "engine": ("center", "bottom"),
    "FL": ("left", "top"),
    "FR": ("right", "top"),
    "RL": ("left", "bottom"),
    "RR": ("right", "bottom"),
    "reset": ("right", "top"),
    "size": ("right", "top"),
}

# Reference button size at multiplier 1.0 (matches the resolution-table
# baseline). Scaled per-cycle so reset/size buttons stay proportional to
# the widgets they sit alongside.
_BUTTON_BASE_PX = 36
_BUTTON_MIN_PX = 20


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
    else:
        p = layout.wheels[wid]
    return p.x, p.y, p.w, p.h


def _apply_layout(
    window: OverlayWindow,
    engine: EngineView,
    wheels: dict[str, WheelView],
    reset_btn: ResetButton,
    size_btn: SizeButton,
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

    # Control buttons sit in the top-right corner by default. Reset goes
    # closest to the edge; size button to its left so they read as a row.
    margin = layout.margin
    reset_default_x = layout.screen_w - reset_btn.width() - margin
    reset_default_y = margin
    _place("reset", reset_btn,
           reset_default_x, reset_default_y, reset_btn.width(), reset_btn.height())
    reset_btn.moved_to.connect(lambda x, y: save_position("reset", x, y))
    reset_btn.show()

    size_default_x = reset_default_x - size_btn.width() - margin // 2
    size_default_y = reset_default_y
    _place("size", size_btn,
           size_default_x, size_default_y, size_btn.width(), size_btn.height())
    size_btn.moved_to.connect(lambda x, y: save_position("size", x, y))
    size_btn.show()


def _reset_layout(engine: EngineView, wheels: dict[str, WheelView],
                  layout: ScreenLayout) -> None:
    """Restore every overlay widget to its default position and shown
    state, and wipe persisted entries for them. The reset/size buttons
    are preserved so the user keeps the spots they put them."""
    delete_entries(list(_RESETTABLE_IDS))
    engine.setGeometry(*_default_pos("engine", layout))
    engine.show()
    for wid, view in wheels.items():
        view.setGeometry(*_default_pos(wid, layout))
        view.show()


def _anchor_resize(view, wid: str, new_w: int, new_h: int,
                   screen_w: int, screen_h: int) -> None:
    """Resize a widget to (``new_w``, ``new_h``) while pinning the corner
    declared in ``_ANCHORS`` for ``wid``. Used for both the telemetry
    widgets (engine + wheels) and the floating buttons so a size cycle
    is round-trippable from any of them."""
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


def _resize_widgets(engine: EngineView, wheels: dict[str, WheelView],
                    reset_btn: ResetButton, size_btn: SizeButton,
                    layout: ScreenLayout, multiplier: float) -> None:
    """Re-apply layout-computed dimensions for engine + wheels and scale
    the floating buttons by the same multiplier. Sizing then shrinking
    is round-trippable: a widget at size M, scaled to L and back, lands
    in the exact same place — the anchor logic in :func:`_anchor_resize`
    pins each widget's natural corner."""
    _anchor_resize(engine, "engine", layout.engine.w, layout.engine.h,
                   layout.screen_w, layout.screen_h)
    for wid, view in wheels.items():
        place = layout.wheels[wid]
        _anchor_resize(view, wid, place.w, place.h,
                       layout.screen_w, layout.screen_h)

    btn_size = max(_BUTTON_MIN_PX, int(_BUTTON_BASE_PX * multiplier))
    _anchor_resize(reset_btn, "reset", btn_size, btn_size,
                   layout.screen_w, layout.screen_h)
    reset_btn.setFixedSize(btn_size, btn_size)
    _anchor_resize(size_btn, "size", btn_size, btn_size,
                   layout.screen_w, layout.screen_h)
    size_btn.setFixedSize(btn_size, btn_size)


def _on_frame(frame: TelemetryFrame, engine: EngineView, wheels: dict[str, WheelView]) -> None:
    engine.set_data(frame.engine)
    for wid, view in wheels.items():
        view.set_data(frame.wheels[wid])


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="overlay", description="AC Evo telemetry overlay")
    parser.add_argument(
        "--source",
        choices=("synthetic", "ac-evo"),
        default="synthetic",
        help="telemetry source: 'synthetic' (default, mock data) or 'ac-evo' (live game)",
    )
    parser.add_argument("--hz", type=int, default=60, help="sample rate in Hz (default: 60)")
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    app = QApplication(sys.argv)
    # Sets the per-user config directory QStandardPaths resolves for our
    # positions.json — without this, Qt falls back to a generic "QtProject"
    # path which is harder to find when the user wants to clear it.
    app.setOrganizationName("LiveTelemetryAcEvo")
    app.setApplicationName("Overlay")

    screen = app.primaryScreen()
    geom = screen.availableGeometry()

    window = OverlayWindow()

    engine = EngineView()
    wheels = {wid: WheelView(wid) for wid in ("FL", "FR", "RL", "RR")}
    reset_btn = ResetButton()
    size_btn = SizeButton()

    # Apply persisted size choice so the first compute_layout uses the
    # multiplier the user last picked. ``layout`` is rebound by the
    # size-cycle handler below; lambdas referencing it read the current
    # value via the closure rather than capturing it by value.
    size_idx = load_size_index(DEFAULT_SIZE_INDEX, len(SIZE_FACTORS))
    size_btn.set_index(size_idx)
    base_mult = pick_resolution(geom.height())[1]
    actual_mult = base_mult * SIZE_FACTORS[size_idx]
    # Pre-size the buttons so _apply_layout's _place uses their final
    # dimensions when computing the default top-right corner anchor.
    initial_btn = max(_BUTTON_MIN_PX, int(_BUTTON_BASE_PX * actual_mult))
    reset_btn.setFixedSize(initial_btn, initial_btn)
    size_btn.setFixedSize(initial_btn, initial_btn)
    layout = compute_layout(geom.width(), geom.height(), multiplier=actual_mult)
    _apply_layout(window, engine, wheels, reset_btn, size_btn, layout)
    window.move(geom.x(), geom.y())

    reset_btn.clicked.connect(lambda: _reset_layout(engine, wheels, layout))

    def _on_size_cycled(idx: int) -> None:
        nonlocal layout
        save_size_index(idx)
        new_mult = base_mult * SIZE_FACTORS[idx]
        layout = compute_layout(geom.width(), geom.height(), multiplier=new_mult)
        _resize_widgets(engine, wheels, reset_btn, size_btn, layout, new_mult)

    size_btn.size_changed.connect(_on_size_cycled)

    # Hide the telemetry widgets during the countdown — they reveal when
    # the countdown finishes (subject to the persisted visibility flag).
    # The source still feeds frames the whole time so widgets show live
    # data the instant they appear.
    visibility = load_visibility()
    engine.hide()
    for view in wheels.values():
        view.hide()

    countdown = CountdownView(window)
    countdown.setGeometry(0, 0, layout.screen_w, layout.screen_h)
    countdown.raise_()  # ensure it sits above any pre-shown chrome

    def _reveal_widgets() -> None:
        if visibility.get("engine", True):
            engine.show()
        for wid, view in wheels.items():
            if visibility.get(wid, True):
                view.show()

    countdown.finished.connect(_reveal_widgets)

    source = make_source(args.source, hz=args.hz, parent=window)
    source.frame.connect(lambda f: _on_frame(f, engine, wheels))
    source.start()

    print(
        f"[overlay] source={args.source} hz={args.hz} "
        f"screen={geom.width()}x{geom.height()} "
        f"resolution={layout.resolution_name} multiplier={layout.multiplier:.2f} "
        f"engine={layout.engine.w}x{layout.engine.h} "
        f"wheel={layout.wheels['FL'].w}x{layout.wheels['FL'].h}"
    )

    window.show()
    # Default to click-through ON: a full-screen overlay must not steal mouse
    # input from the game underneath. User can toggle with Ctrl+Alt+L.
    window.toggle_click_through()
    countdown.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
