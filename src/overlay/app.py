from __future__ import annotations

import argparse
import sys

from PySide6.QtWidgets import QApplication

from .layout import ScreenLayout, compute_layout
from .sources import make_source
from .telemetry import TelemetryFrame
from .widgets.engine_view import EngineView
from .widgets.wheel_view import WheelView
from .window import OverlayWindow


def _apply_layout(
    window: OverlayWindow,
    engine: EngineView,
    wheels: dict[str, WheelView],
    layout: ScreenLayout,
) -> None:
    """Stretch the overlay across the screen and place widgets at corners."""
    window.setGeometry(0, 0, layout.screen_w, layout.screen_h)
    engine.setParent(window)
    engine.setGeometry(layout.engine.x, layout.engine.y,
                       layout.engine.w, layout.engine.h)
    engine.show()
    for wid, view in wheels.items():
        place = layout.wheels[wid]
        view.setParent(window)
        view.setGeometry(place.x, place.y, place.w, place.h)
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

    screen = app.primaryScreen()
    geom = screen.availableGeometry()

    window = OverlayWindow()

    engine = EngineView()
    wheels = {wid: WheelView(wid) for wid in ("FL", "FR", "RL", "RR")}

    layout = compute_layout(geom.width(), geom.height())
    _apply_layout(window, engine, wheels, layout)
    window.move(geom.x(), geom.y())

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
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
