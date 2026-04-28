from __future__ import annotations

from dataclasses import dataclass


# Logical pixel sizes inherited from the original AC plugin.
ENGINE_LOGICAL_W = 512
ENGINE_LOGICAL_H = 85
WHEEL_LOGICAL_W = 512
WHEEL_LOGICAL_H = 271

# Resolution -> multiplier table from lt_components.BoxComponent.resolution_map.
# We pick the row whose nominal vertical resolution best matches the actual
# screen height so widgets keep the original aspect at every common display.
_RESOLUTION_TABLE = (
    ("240p", 240, 0.16),
    ("360p", 360, 0.25),
    ("480p", 480, 0.33),
    ("576p", 576, 0.40),
    ("HD", 720, 0.50),
    ("FHD", 1080, 0.75),
    ("1440p", 1440, 1.00),
    ("UHD", 2160, 1.50),
    ("4K", 2160, 1.60),  # legacy alias kept for parity with the AC plugin
    ("8K", 4320, 3.00),
)


def pick_resolution(screen_height: int) -> tuple[str, float]:
    """Return (name, multiplier) for the closest standard vertical resolution."""
    name, _, mult = min(
        _RESOLUTION_TABLE,
        key=lambda row: abs(row[1] - screen_height),
    )
    return name, mult


@dataclass(frozen=True)
class WidgetPlacement:
    """Absolute on-screen rectangle for a single overlay widget."""

    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class ScreenLayout:
    """Computed positions for engine + 4 wheel widgets on a full-screen overlay."""

    multiplier: float
    resolution_name: str
    screen_w: int
    screen_h: int
    margin: int
    engine: WidgetPlacement
    wheels: dict[str, WidgetPlacement]


def compute_layout(screen_w: int, screen_h: int) -> ScreenLayout:
    """Lay out widgets on a screen-sized overlay.

    Engine bar sits top-centre. Wheel widgets occupy the four corners
    (FL top-left, FR top-right, RL bottom-left, RR bottom-right) at the
    multiplier picked from the screen's vertical resolution.
    """
    name, mult = pick_resolution(screen_h)

    eng_w = int(ENGINE_LOGICAL_W * mult)
    eng_h = int(ENGINE_LOGICAL_H * mult)
    wheel_w = int(WHEEL_LOGICAL_W * mult)
    wheel_h = int(WHEEL_LOGICAL_H * mult)
    margin = max(8, int(20 * mult))

    # Down-scale uniformly if the chosen multiplier exceeds the screen
    # (e.g. someone running an FHD display in portrait): each side needs
    # one wheel widget plus margin, the centre needs the engine widget.
    side_demand = wheel_w + margin
    top_demand = eng_h + margin * 2 + wheel_h
    bottom_demand = wheel_h + margin
    width_needed = side_demand * 2 + eng_w + margin * 2
    height_needed = top_demand + bottom_demand

    if width_needed > screen_w or height_needed > screen_h:
        scale = min(screen_w / width_needed, screen_h / height_needed) * 0.95
        eng_w = int(eng_w * scale)
        eng_h = int(eng_h * scale)
        wheel_w = int(wheel_w * scale)
        wheel_h = int(wheel_h * scale)
        margin = max(4, int(margin * scale))

    engine = WidgetPlacement(
        x=(screen_w - eng_w) // 2,
        y=margin,
        w=eng_w,
        h=eng_h,
    )

    top_y = margin + eng_h + margin
    bot_y = screen_h - wheel_h - margin
    left_x = margin
    right_x = screen_w - wheel_w - margin

    wheels = {
        "FL": WidgetPlacement(left_x, top_y, wheel_w, wheel_h),
        "FR": WidgetPlacement(right_x, top_y, wheel_w, wheel_h),
        "RL": WidgetPlacement(left_x, bot_y, wheel_w, wheel_h),
        "RR": WidgetPlacement(right_x, bot_y, wheel_w, wheel_h),
    }

    return ScreenLayout(
        multiplier=mult,
        resolution_name=name,
        screen_w=screen_w,
        screen_h=screen_h,
        margin=margin,
        engine=engine,
        wheels=wheels,
    )
