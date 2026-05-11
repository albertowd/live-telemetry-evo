from __future__ import annotations

import math
import time

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..colors import Colors
from ..fonts import label_font
from ..interpolation import (
    DEFAULT_BRAKE_TEMP_CURVE,
    DEFAULT_TIRE_TEMP_CURVE,
    TirePsi,
    TireTemp,
)
from ..resources import tinted
from ..telemetry import WheelData
from .draggable import DraggableWidget


LOGICAL_W = 512.0
LOGICAL_H = 316.0
# Top padding above the tire silhouette. The rotation pivot is at the
# tire's centre, so the top corners swing both sideways and slightly
# downward (1 - cos θ) under camber tilt — without a margin a 5°
# visual rotation already clips ~7 px above y = 0. 16 px handles up to
# ~14° visual rotation, which covers any realistic camber setup at the
# 2× amplification below.
TOP_MARGIN = 16.0
WARNING_TIME_S = 0.5
LOCK_BLINK_PERIOD_S = 0.1
# Tire-load circle: pixels of diameter per Newton. Calibrated so a
# typical static wheel load (~3 kN) fills roughly half of the 160 px
# tire silhouette, and the circle saturates at the full tire width on
# heavy braking / cornering loads (~6 kN). At this ratio the diameter
# scales linearly with load through almost the entire useful range —
# the upper clamp in `_draw_load` only kicks in on extreme hits like
# kerb strikes, so under normal driving the circle is an accurate
# load-magnitude indicator at every moment.
LOAD_PX_PER_N = 0.027
# Contact-patch model constants. The patch shape is inferred from camber
# (lateral bias) × pressure (crown vs bow) × load (overall extent). The
# game doesn't publish tyre dimensions or stiffness, so this is a
# qualitative indicator — the *colour* of each segment comes from the
# game-simulated per-face tyre temps (which already encode real contact
# pressure × slip), the *height* is the heuristic.
_CAMBER_FULL_BIAS_RAD = math.radians(4.0)  # ±4° = full lateral bias
_PRESSURE_FULL_BIAS = 0.30                 # ±30% off ideal = full crown/bow
_LOAD_FULL_N = 6000.0                       # ~6 kN = full-height patch
_LOAD_FLOOR = 0.30                          # patch never collapses fully
# Visual amplification of the camber tilt. Real setup camber is ±2–3°,
# which on a 256 px-tall tire silhouette moves the bottom corners only
# ~5 px — hard to read at a glance. 2× makes the tilt obvious without
# changing the relationship to the underlying value (0° still renders
# as 0°, the curve stays linear).
_CAMBER_VIS_AMPLIFY = 2.0


def _draw_tinted(p: QPainter, name: str, rect: QRectF, color: QColor) -> None:
    """Tint an icon and stamp it inside the given logical rect.

    The painter has an active scale transform (logical -> widget), so we hand
    the tint helper the *logical* size; Qt's smooth pixmap transform handles
    the on-screen scaling. The source PNGs are 2048-tall masks, so the
    once-cached scaled mask is always a downscale and looks crisp.
    """
    pix = tinted(name, int(rect.width()), int(rect.height()), color)
    p.drawPixmap(rect.topLeft(), pix)


def _text_color_for(bg: QColor) -> QColor:
    """Black or white, whichever lands more readable over ``bg``.

    Uses Rec. 601 perceptual luminance — handles the full blue → green →
    red sweep the temperature colours pass through. Alpha is ignored on
    purpose: callers blend the colour over a similarly-tinted silhouette,
    so the underlying RGB is the right reference.
    """
    lum = 0.299 * bg.redF() + 0.587 * bg.greenF() + 0.114 * bg.blueF()
    return Colors.black if lum > 0.5 else Colors.white


class WheelView(DraggableWidget):
    """One wheel's full visualisation. ``wheel_id`` is FL/FR/RL/RR — used
    to mirror the layout for right-side wheels and tag the title."""

    def __init__(self, wheel_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if wheel_id not in ("FL", "FR", "RL", "RR"):
            raise ValueError(f"unknown wheel id: {wheel_id}")
        self._id = wheel_id
        self._is_left = wheel_id[1] == "L"
        self._data = WheelData()
        self._psi = TirePsi()
        self._temp = TireTemp(DEFAULT_TIRE_TEMP_CURVE)
        self._brake_temp = TireTemp(DEFAULT_BRAKE_TEMP_CURVE)
        self._height_warn_until = 0.0
        self._lock_warn_until = 0.0
        self._lock_blink_t = 0.0
        # Per-wheel high-water marks for brake-pad / disc life. AC EVO's
        # padLife / discLife use AC1's "1.0 = fresh, 0.0 = dead" semantic
        # but the absolute scale isn't pinned down (see ac_evo.py near the
        # field declarations), so the wear bars normalise against the max
        # value seen this session — guarantees a full bar at session start
        # and monotonic shrinkage from there.
        self._pad_w_max = 0.0
        self._disc_w_max = 0.0
        self._last_paint = time.monotonic()
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    @property
    def wheel_id(self) -> str:
        return self._id

    def set_data(self, data: WheelData) -> None:
        self._data = data
        self.update()

    def _x_left(self, x: float, w: float) -> float:
        if self._is_left:
            return x
        return LOGICAL_W - x - w

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        sx = self.width() / LOGICAL_W
        sy = self.height() / LOGICAL_H
        p.scale(sx, sy)

        now = time.monotonic()
        delta_t = now - self._last_paint
        self._last_paint = now

        d = self._data

        # Camber rotates the tire silhouette, the IMO temp band, and the
        # dirt overlay around the tire centre. A single negation
        # produces the right visual for both wheel sides: raw camberRAD
        # has a per-wheel local sign (right-side wheels flip vs the
        # setup tool, see ac_evo.py §9.7a), and the widget's left/right
        # screen placement is itself mirrored — the two flips cancel,
        # so negating the raw value lands the top of the tire tilting
        # toward screen-centre (i.e. toward the car centre) for negative
        # setup camber on both sides, which is what a real wheel does.
        camber_deg = -math.degrees(d.camber) * _CAMBER_VIS_AMPLIFY

        p.save()
        p.translate(256.0, TOP_MARGIN + 128.0)
        p.rotate(camber_deg)
        p.translate(-256.0, -(TOP_MARGIN + 128.0))
        self._draw_tire_and_temps(p, d)
        self._draw_dirt(p, d)
        p.restore()

        # Contact-patch bars *are* the ground indicator — they start at
        # the ground line and extend downward. Painted after the rotated
        # tire so any bottom corner that dipped below the ground line
        # gets visually clipped against the bars.
        self._draw_contact_patch(p, d)
        self._draw_suspension(p, d)
        # Wear bar hidden: AC EVO doesn't publish tyre wear via shared
        # memory (three independent investigations, see SHARED_MEMORY.md
        # §9.1). Re-enable this call if/when Kunos starts publishing it.
        # self._draw_wear(p, d)
        self._draw_lock(p, d, delta_t)
        self._draw_brake_wear(p, d)
        self._draw_pressure(p, d)
        self._draw_height(p, d)
        self._draw_load(p, d)  # last, on top
        self._draw_label(p)

        p.end()

    # --- components ---------------------------------------------------------

    def _draw_tire_and_temps(self, p: QPainter, d: WheelData) -> None:
        rect = QRectF(176.0, TOP_MARGIN, 160.0, 256.0)

        # Tire silhouette tinted by composite temperature (mirrors lt_components.Tire).
        body = (d.tire_t_c * 0.75
                + ((d.tire_t_i + d.tire_t_m + d.tire_t_o) / 3.0) * 0.25)
        body_norm = (d.tire_t_norm_c * 0.75
                     + ((d.tire_t_norm_i + d.tire_t_norm_m + d.tire_t_norm_o) / 3.0) * 0.25)
        body_color = QColor(self._temp.interpolate_color(body, body_norm))
        _draw_tinted(p, "tire", rect, body_color)

        # Temps overlay: 3 columns x 8 rows. Inner/Mid/Outer get the top+bottom
        # bumps; the core temp fills the central 75%.
        pad = 12.0
        quarter = (rect.height() - 2.0 * pad) * 0.125
        part = (rect.width() - 2.0 * pad) / 3.0
        # Mirror the IMO band on left-side wheels so INNER always sits
        # on the screen-centre-facing side of the widget — the same
        # side the rotation now visibly loads under negative camber.
        # Right-side wheels keep INNER on the left of the widget because
        # the overall widget layout is already mirrored (_x_left) for
        # them, so the screen-centre-facing side is already widget-LEFT.
        if self._is_left:
            inner_x = rect.x() + pad + 2.0 * part
            outer_x = rect.x() + pad
        else:
            inner_x = rect.x() + pad
            outer_x = rect.x() + pad + 2.0 * part
        top_y = rect.y() + pad

        core_color = QColor(self._temp.interpolate_color(d.tire_t_c, d.tire_t_norm_c))
        core_color.setAlphaF(0.85)
        p.setPen(Qt.NoPen)
        p.setBrush(core_color)
        # Disable AA for the temp grid: columns are 136/3 ≈ 45.33 logical px
        # wide and the painter has a non-integer scale(), so anti-aliased
        # edges on adjacent rects leave semi-transparent seams between
        # colors. Aliased rects snap consistently and meet flush.
        p.setRenderHint(QPainter.Antialiasing, False)
        # Core block: always start at the leftmost edge of the IMO band
        # (rect.x() + pad), not at inner_x. inner_x flips between
        # left/right sides depending on wheel side so the INNER label
        # lands on the screen-centre-facing column — the core block,
        # however, is always the centred fill of the band and must stay
        # anchored to the band's left edge regardless.
        p.drawRect(QRectF(rect.x() + pad, top_y + quarter,
                          part * 3.0, quarter * 6.0))

        edge_zones: list[tuple[float, float, QColor]] = []
        for value, norm, x in (
            (d.tire_t_i, d.tire_t_norm_i, inner_x),
            (d.tire_t_m, d.tire_t_norm_m, rect.x() + pad + part),
            (d.tire_t_o, d.tire_t_norm_o, outer_x),
        ):
            c = QColor(self._temp.interpolate_color(value, norm))
            p.setBrush(c)
            p.drawRect(QRectF(x, top_y, part, quarter))
            p.drawRect(QRectF(x, top_y + quarter * 7.0, part, quarter))
            edge_zones.append((value, x, c))
        p.setRenderHint(QPainter.Antialiasing, True)

        # Per-zone temperature readouts: inner / middle / outer in the top
        # bumps, core temp in the centre. Text colour flips against the
        # patch luminance so the value stays legible from cold blue
        # through ideal green to hot red.
        p.setFont(label_font(14))
        for value, x, patch_color in edge_zones:
            p.setPen(_text_color_for(patch_color))
            p.drawText(QRectF(x, top_y, part, quarter),
                       Qt.AlignCenter, f"{int(value)}°C")

        p.setFont(label_font(20))
        p.setPen(_text_color_for(core_color))
        p.drawText(rect, Qt.AlignCenter, f"{int(d.tire_t_c)} °C")

    def _draw_dirt(self, p: QPainter, d: WheelData) -> None:
        full = QRectF(188.0, TOP_MARGIN + 128.0, 136.0, 116.0)
        dirt = max(0.0, min(4.0, d.tire_d)) / 4.0 * full.height()
        dirt_rect = QRectF(full.x(), full.bottom() - dirt, full.width(), dirt)
        c = QColor(Colors.brown)
        c.setAlphaF(0.7)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawRect(dirt_rect)

    def _draw_contact_patch(self, p: QPainter, d: WheelData) -> None:
        """Three white bars dropping from where the ground line sits
        (just below the tire). Together they *are* the ground indicator
        — there is no separate ground line — and their heights encode
        which lateral part of the contact patch is actually loaded.

        Lateral convention matches the IMO band above: INNER segment
        sits on the screen-centre-facing side of the widget, OUTER on
        the screen-edge-facing side, MIDDLE between them.

        Heuristic ("cleanest path" from the SHM survey, since the game
        doesn't publish tyre dimensions or stiffness):
            * camber → lateral bias (inner-loaded when sign-normalised
              camber < 0, outer-loaded when > 0)
            * pressure → crown vs. bow (norm > 1 → centre carries,
              norm < 1 → edges carry, centre lifts)
            * load → overall patch extent (scales segment height)

        Bars render in solid white so the visual reads cleanly as the
        ground reference. Temperature information lives in the IMO band
        above; mixing it into the contact patch made the two harder to
        compare at a glance.
        """
        band_x = 188.0
        band_w = 136.0      # mirror the IMO band width above
        band_top = TOP_MARGIN + 256.0  # flush with the tire bottom
        band_max_h = 32.0
        seg_w = band_w / 3.0

        # Sign-correct camber so <0 = inner-edge-loaded for both sides.
        camber_n = d.camber * (1.0 if self._is_left else -1.0)
        # -1 = full inner bias, +1 = full outer bias, 0 = centred.
        camber_axis = max(-1.0, min(1.0, camber_n / _CAMBER_FULL_BIAS_RAD))

        # +1 = bowed (under-inflated, edges carry), -1 = crowned
        # (over-inflated, centre carries), 0 = ideal.
        p_bias = max(-1.0, min(1.0, (1.0 - d.tire_p_norm) / _PRESSURE_FULL_BIAS))

        # Load magnitude scales the overall patch, with a floor so a
        # stationary tire still shows *some* contact strip.
        load_n = max(0.0, min(1.0, d.tire_l / _LOAD_FULL_N))
        load_factor = _LOAD_FLOOR + (1.0 - _LOAD_FLOOR) * load_n

        # Camber factor: inner fades out as axis → +1, outer as axis → -1,
        # middle fades as |axis| → 1.
        inner_camber = max(0.0, 1.0 - max(0.0, camber_axis))
        outer_camber = max(0.0, 1.0 + min(0.0, camber_axis))
        middle_camber = max(0.0, 1.0 - abs(camber_axis))

        # Pressure factor: bowing (p_bias > 0) lifts the middle and adds
        # load to the edges, crowning (p_bias < 0) reverses. Additive
        # multiplier in [0, 2]; clamped by the eventual height clamp.
        edge_press = 1.0 + p_bias
        middle_press = 1.0 - p_bias

        # Segment positions mirror the IMO band convention above:
        # INNER on the screen-centre-facing side, OUTER on the
        # screen-edge-facing side.
        if self._is_left:
            inner_x_seg = band_x + 2.0 * seg_w
            outer_x_seg = band_x
        else:
            inner_x_seg = band_x
            outer_x_seg = band_x + 2.0 * seg_w
        middle_x_seg = band_x + seg_w
        zones = (
            (inner_camber * edge_press, inner_x_seg),
            (middle_camber * middle_press, middle_x_seg),
            (outer_camber * edge_press, outer_x_seg),
        )
        p.setPen(Qt.NoPen)
        p.setBrush(Colors.white)
        # Aliased rects so adjacent segments meet flush at the non-integer
        # widget scale, same trick the IMO temp grid uses.
        p.setRenderHint(QPainter.Antialiasing, False)
        for weight, x in zones:
            h = max(0.0, min(1.0, weight * load_factor)) * band_max_h
            if h < 0.5:
                continue
            p.drawRect(QRectF(x, band_top, seg_w, h))
        p.setRenderHint(QPainter.Antialiasing, True)

    def _draw_suspension(self, p: QPainter, d: WheelData) -> None:
        rect = QRectF(self._x_left(346.0, 64.0), TOP_MARGIN, 64.0, 256.0)
        travel = (d.susp_t / d.susp_m_t) if d.susp_m_t > 0.0 else 0.5

        if travel > 0.95 or travel < 0.05:
            band = Colors.red
        elif travel > 0.90 or travel < 0.10:
            band = Colors.yellow
        else:
            band = Colors.white

        # Tinted suspension graphic.
        _draw_tinted(p, "suspension", rect, band)

        # Inner travel fill — original AC plugin convention: bar fills the
        # inner area at full extension and SHRINKS as the suspension
        # compresses (height proportional to remaining travel, 1 - ratio).
        # Counter-intuitive vs a typical "load grows" gauge but kept for
        # parity with LiveTelemetry. The icon's two vertical rails sit at
        # x=0..10 and x=54..64; the fill *overlaps* each rail by 2 px so
        # the rails' anti-aliased edges can't leave a transparent seam at
        # fractional widget scales.
        inner = QRectF(rect.x() + 8.0, rect.y() + 44.0,
                       rect.width() - 16.0, rect.height() - 88.0)
        fill_h = inner.height() * max(0.0, min(1.0, 1.0 - travel))
        p.setPen(Qt.NoPen)
        p.setBrush(band)
        p.drawRect(QRectF(inner.x(), inner.y(), inner.width(), max(0.0, fill_h)))

    def _draw_wear(self, p: QPainter, d: WheelData) -> None:
        rect = QRectF(self._x_left(154.0, 10.0), 2.0, 10.0, 252.0)
        # Black bar with a 2 px white border.
        p.setPen(QPen(Colors.white, 2.0))
        p.setBrush(Colors.black)
        p.drawRect(rect)

        # Map only the *usable* portion of the wear cycle to the bar.
        # AC Evo reports wear on a small scale (1.0 = fresh; the tyre is
        # past its performance cliff well before 0.85), so an empty bar
        # means "pit now", not "still has some life left somewhere". The
        # 0.93..1.00 window is tight enough that wear ticks fill the bar
        # across its full height instead of crawling through the top
        # pixels, and the colour bands move 3 % earlier so red lands
        # while there's still time to react.
        if d.tire_w > 0.98:
            color = Colors.green
        elif d.tire_w > 0.95:
            color = Colors.yellow
        else:
            color = Colors.red
        wear = max(0.0, min(1.0, (d.tire_w - 0.93) / 0.07))
        fill_h = wear * rect.height()
        fill = QRectF(rect.x(), rect.bottom() - fill_h, rect.width(), fill_h)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawRect(fill)

    def _draw_lock(self, p: QPainter, d: WheelData, delta_t: float) -> None:
        rect = QRectF(self._x_left(70.0, 60.0), 0.0, 60.0, 60.0)

        if d.lock:
            self._lock_warn_until = time.monotonic() + WARNING_TIME_S

        # Default tint reflects brake disc temperature; ABS/lock override it.
        temp_color = QColor(self._brake_temp.interpolate_color(d.brake_t, d.brake_t_norm))

        if d.abs_active:
            # ABS modulating on this wheel: blink blue/temp so the moment
            # the system intervenes is visible at a glance, mirroring the
            # yellow lock-warning blink below.
            self._lock_blink_t += delta_t
            blink_on = int(self._lock_blink_t / LOCK_BLINK_PERIOD_S) % 2 == 0
            color = Colors.blue if blink_on else temp_color
        elif time.monotonic() < self._lock_warn_until:
            self._lock_blink_t += delta_t
            blink_on = int(self._lock_blink_t / LOCK_BLINK_PERIOD_S) % 2 == 0
            color = Colors.yellow if blink_on else temp_color
        else:
            color = temp_color
            self._lock_blink_t = 0.0

        # Disc temperature drives the *tint* (set above) only. The icon
        # itself is drawn at full size — pad/disc wear bars beside the
        # icon convey life remaining, which previously tried to live in
        # this same icon via a temperature-based clip and conflated the
        # two signals.
        _draw_tinted(p, "brake", rect, color)

        # Disc temperature label below the icon, always in the temp-tint
        # color so the value stays legible even when ABS/lock force the
        # icon blue/yellow.
        p.setFont(label_font(18))
        p.setPen(temp_color)
        label_rect = QRectF(rect.x() - 20.0, rect.bottom() + 2.0,
                            rect.width() + 40.0, 22.0)
        p.drawText(label_rect, Qt.AlignCenter, f"{int(d.brake_t)} °C")

    def _draw_brake_wear(self, p: QPainter, d: WheelData) -> None:
        """Horizontal disc/pad wear bars in the gap between the brake-
        temperature label and the pressure icon. Each row has a centred
        title and a left→right fill (full bar = fresh). Self-calibrated
        against the per-wheel max observed since session start, since
        AC EVO's padLife / discLife raw scale isn't pinned down."""
        self._pad_w_max = max(self._pad_w_max, d.pad_w)
        self._disc_w_max = max(self._disc_w_max, d.disc_w)

        bar_x = self._x_left(70.0, 60.0)
        bar_w = 60.0
        bar_h = 12.0
        title_h = 12.0
        title_rect_x = self._x_left(50.0, 100.0)
        title_rect_w = 100.0

        rows = (
            ("Disk Wear", d.disc_w, self._disc_w_max, 90.0),
            ("Pads Wear", d.pad_w, self._pad_w_max, 122.0),
        )
        for title, value, max_obs, row_y in rows:
            p.setFont(label_font(10))
            p.setPen(Colors.white)
            p.drawText(QRectF(title_rect_x, row_y, title_rect_w, title_h),
                       Qt.AlignCenter, title)

            rect = QRectF(bar_x, row_y + title_h + 2.0, bar_w, bar_h)
            p.setPen(QPen(Colors.white, 1.5))
            p.setBrush(Colors.black)
            p.drawRect(rect)

            ratio = (value / max_obs) if max_obs > 0.0 else 1.0
            ratio = max(0.0, min(1.0, ratio))
            if ratio > 0.5:
                color = Colors.green
            elif ratio > 0.2:
                color = Colors.yellow
            else:
                color = Colors.red

            inner = rect.adjusted(1.5, 1.5, -1.5, -1.5)
            fill_w = ratio * inner.width()
            fill_rect = QRectF(inner.x(), inner.y(), fill_w, inner.height())
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRect(fill_rect)

    def _draw_pressure(self, p: QPainter, d: WheelData) -> None:
        rect = QRectF(self._x_left(70.0, 60.0), 171.0, 60.0, 60.0)
        color = QColor(self._psi.interpolate_color(d.tire_p_norm))
        _draw_tinted(p, "pressure", rect, color)

        # Label sits directly under the icon, in the same colour (mirrors AC plugin).
        p.setFont(label_font(18))
        p.setPen(color)
        label_rect = QRectF(rect.x() - 20.0, rect.bottom() + 2.0,
                            rect.width() + 40.0, 22.0)
        p.drawText(label_rect, Qt.AlignCenter, f"{d.tire_p:.1f} psi")

    def _draw_height(self, p: QPainter, d: WheelData) -> None:
        rect = QRectF(self._x_left(430.0, 64.0), TOP_MARGIN + 208.0, 64.0, 48.0)
        if d.height < 20.0:
            self._height_warn_until = time.monotonic() + WARNING_TIME_S
        color = Colors.red if time.monotonic() < self._height_warn_until else Colors.white

        _draw_tinted(p, "height", rect, color)

        p.setFont(label_font(16))
        p.setPen(color)
        # Clamp the ±20 px expansion to widget bounds so the centred text
        # never overflows the logical 0..LOGICAL_W rect.
        text_x = max(0.0, rect.x() - 20.0)
        text_right = min(LOGICAL_W, rect.x() + rect.width() + 20.0)
        text_rect = QRectF(text_x, rect.y(), text_right - text_x, rect.height())
        p.drawText(text_rect, Qt.AlignCenter, f"{d.height:.1f} mm")

    def _draw_load(self, p: QPainter, d: WheelData) -> None:
        # The load circle stays centred over the tire. Diameter scales
        # linearly with load up to the tire silhouette's width (160 px);
        # no minimum floor, so a wheel that goes light or off the ground
        # accurately shrinks toward zero rather than holding a fake
        # "minimum size" floor.
        center = QPointF(256.0, TOP_MARGIN + 128.0)
        diameter = max(0.0, min(160.0, d.tire_l * LOAD_PX_PER_N))
        if diameter < 1.0:
            return
        rect = QRectF(center.x() - diameter / 2.0,
                      center.y() - diameter / 2.0,
                      diameter, diameter)
        c = QColor(Colors.white)
        c.setAlphaF(0.85)
        _draw_tinted(p, "load", rect, c)

    def _draw_label(self, p: QPainter) -> None:
        # Match the ride-height label's horizontal extent so the wheel ID
        # / compound sit in the same vertical column as the height value.
        # Same widget-bounds clamp so the column never overflows logical
        # 0..LOGICAL_W (the height-icon rect can sit flush with the edge
        # on the inboard wheels).
        rect_x = self._x_left(430.0, 64.0)
        col_x = max(0.0, rect_x - 20.0)
        col_right = min(LOGICAL_W, rect_x + 64.0 + 20.0)
        anchor = QRectF(col_x, 4.0, col_right - col_x, 24.0)
        align = Qt.AlignCenter

        p.setFont(label_font(20))
        p.setPen(Colors.white)
        p.drawText(anchor, align, self._id)

        # Compound abbreviation below the wheel ID, when known. First three
        # uppercase chars keep "SOFT" / "MEDIUM" / "HARD" / "INTER" / "WET"
        # readable while staying out of the load circle's footprint.
        if self._data.compound:
            p.setFont(label_font(14))
            compound_rect = QRectF(anchor.x(), anchor.bottom() + 1.0,
                                   anchor.width(), 18.0)
            p.drawText(compound_rect, align, self._data.compound[:3].upper())
