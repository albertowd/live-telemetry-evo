from __future__ import annotations

import argparse
import sys

from PySide6.QtWidgets import QApplication

from .layout import ScreenLayout, compute_layout
from .settings import (delete_entries, load_positions, load_visibility,
                        save_position, save_visibility)
from .sources import make_source
from .telemetry import TelemetryFrame
from .widgets.countdown import CountdownView
from .widgets.engine_view import EngineView
from .widgets.reset_button import ResetButton
from .widgets.wheel_view import WheelView
from .window import OverlayWindow


_RESETTABLE_IDS = ("engine", "FL", "FR", "RL", "RR")


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

    # Reset button: top-right corner default, draggable, never closable.
    btn_w = reset_btn.width()
    btn_h = reset_btn.height()
    margin = layout.margin
    default_x = layout.screen_w - btn_w - margin
    default_y = margin
    _place("reset", reset_btn, default_x, default_y, btn_w, btn_h)
    reset_btn.moved_to.connect(lambda x, y: save_position("reset", x, y))
    reset_btn.show()


def _reset_layout(engine: EngineView, wheels: dict[str, WheelView],
                  layout: ScreenLayout) -> None:
    """Restore every overlay widget to its default position and shown
    state, and wipe persisted entries for them. The reset button itself
    is preserved so the user keeps the spot they put it."""
    delete_entries(list(_RESETTABLE_IDS))
    engine.setGeometry(*_default_pos("engine", layout))
    engine.show()
    for wid, view in wheels.items():
        view.setGeometry(*_default_pos(wid, layout))
        view.show()


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

    layout = compute_layout(geom.width(), geom.height())
    _apply_layout(window, engine, wheels, reset_btn, layout)
    window.move(geom.x(), geom.y())

    reset_btn.clicked.connect(lambda: _reset_layout(engine, wheels, layout))

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
