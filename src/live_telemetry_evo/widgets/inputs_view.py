from __future__ import annotations

import math

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..colors import Colors
from ..fonts import label_font
from ..telemetry import InputsData
from .draggable import DraggableWidget


# Logical canvas — keep in sync with ``INPUTS_LOGICAL_W`` / ``_H`` in
# ``layout.py``. Layout is two columns: pedal/steering/FFB bars on the
# left, G-meter circle + status panel on the right.
LOGICAL_W = 480.0
LOGICAL_H = 160.0

LEFT_COL_W = 280.0
RIGHT_COL_X = LEFT_COL_W + 8.0  # 8 px gutter

# Each input bar row: label (28 px) + bar (rest) + value (40 px) +
# vertical padding so six rows (THR / BRK / CLU / HBR / STR / FFB)
# stack inside LEFT_COL_W.
ROW_H = 23.0
ROWS_TOP = 6.0
ROWS_LEFT_PAD = 4.0
LABEL_W = 30.0
VALUE_W = 44.0

# G-meter circle in the right column, top half. Radius is set so the
# 1-g ring sits at ~60 % of the available radius — leaves headroom
# for ~1.7 g spikes before the dot pins to the rim.
G_METER_CENTER = QPointF(RIGHT_COL_X + 90.0, 50.0)
G_METER_RADIUS = 42.0
G_METER_FULL_SCALE = 1.7  # g equivalent to the rim

# Status panel: damage chips + tyres-out + performance mode, anchored
# bottom-right. The y origin sits below the G-meter's "g" readout (which
# extends to ~y=108) so the two never collide.
STATUS_RECT = QRectF(RIGHT_COL_X, 112.0, LOGICAL_W - RIGHT_COL_X, 44.0)


class InputsView(DraggableWidget):
    """Phase 3 widget — driver inputs + dynamics + car state.

    Pedal/steering/FFB bars on the left; G-meter circle + damage /
    tyres-out / performance-mode chips on the right.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data = InputsData()
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def set_data(self, data: InputsData) -> None:
        self._data = data
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        sx = self.width() / LOGICAL_W
        sy = self.height() / LOGICAL_H
        p.scale(sx, sy)

        d = self._data
        self._draw_bars(p, d)
        self._draw_g_meter(p, d)
        self._draw_status(p, d)

        p.end()

    # --- left column: pedal / steering / FFB bars ---------------------------

    def _draw_bars(self, p: QPainter, d: InputsData) -> None:
        """Six horizontal bars: throttle, brake, clutch, handbrake,
        steering (centred at 50 %), FFB.

        Steering is the only signed bar — it grows from the centre line
        in the direction of input. Everything else fills left-to-right.
        """
        p.setFont(label_font(11))
        rows: list[tuple[str, float, QColor, str, bool]] = [
            ("THR", d.throttle, Colors.green, f"{int(d.throttle * 100)}%", False),
            ("BRK", d.brake, Colors.red, f"{int(d.brake * 100)}%", False),
            ("CLU", d.clutch, Colors.yellow, f"{int(d.clutch * 100)}%", False),
            ("HBR", d.handbrake, Colors.white, f"{int(d.handbrake * 100)}%", False),
            ("STR", d.steering, Colors.blue, f"{int(d.steering_deg):+d}°", True),
            ("FFB", d.ffb, Colors.white, f"{int(d.ffb * 100)}%", False),
        ]

        bar_x = ROWS_LEFT_PAD + LABEL_W
        bar_w = LEFT_COL_W - LABEL_W - VALUE_W - ROWS_LEFT_PAD * 2
        for idx, (label, value, color, value_text, signed) in enumerate(rows):
            y = ROWS_TOP + idx * ROW_H
            row_rect = QRectF(ROWS_LEFT_PAD, y, LEFT_COL_W - ROWS_LEFT_PAD * 2, ROW_H - 4.0)

            # Label, dim — keeps the colour budget for the bar fill.
            label_color = QColor(Colors.white)
            label_color.setAlphaF(0.7)
            p.setPen(label_color)
            p.drawText(QRectF(row_rect.x(), row_rect.y(),
                              LABEL_W, row_rect.height()),
                       Qt.AlignLeft | Qt.AlignVCenter, label)

            # Bar background (subtle dark fill so the row outline reads
            # even with the input at zero).
            track = QRectF(bar_x, row_rect.y() + 3.0, bar_w, row_rect.height() - 6.0)
            bg = QColor(Colors.white)
            bg.setAlphaF(0.12)
            p.fillRect(track, bg)

            if signed:
                # Steering: centre marker + bidirectional fill.
                centre_x = track.x() + track.width() / 2.0
                tick = QColor(Colors.white)
                tick.setAlphaF(0.5)
                p.setPen(QPen(tick, 1.0))
                p.drawLine(QPointF(centre_x, track.y()),
                           QPointF(centre_x, track.bottom()))
                ratio = max(-1.0, min(1.0, value))
                fill_w = (track.width() / 2.0) * abs(ratio)
                if ratio >= 0.0:
                    fill = QRectF(centre_x, track.y(), fill_w, track.height())
                else:
                    fill = QRectF(centre_x - fill_w, track.y(), fill_w, track.height())
                p.fillRect(fill, color)
            else:
                ratio = max(0.0, min(1.0, value))
                fill_w = track.width() * ratio
                fill_color = color
                # FFB clipping: anything ≥ 0.95 turns red so the user
                # sees they're losing detail.
                if label == "FFB" and value >= 0.95:
                    fill_color = Colors.red
                p.fillRect(QRectF(track.x(), track.y(), fill_w, track.height()),
                           fill_color)

            # Value on the right.
            p.setPen(Colors.white)
            p.drawText(QRectF(row_rect.right() - VALUE_W, row_rect.y(),
                              VALUE_W, row_rect.height()),
                       Qt.AlignRight | Qt.AlignVCenter, value_text)

    # --- right column: G-meter circle ---------------------------------------

    def _draw_g_meter(self, p: QPainter, d: InputsData) -> None:
        """G-meter: a circle with concentric reference rings and a dot
        at (g_lat, g_long) scaled to the rim. Dot colour shifts from
        green → yellow → red as combined-g increases.
        """
        # Background disc — translucent so the value text behind stays legible.
        bg = QColor(0, 0, 0)
        bg.setAlphaF(0.45)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawEllipse(G_METER_CENTER, G_METER_RADIUS, G_METER_RADIUS)

        # Reference rings at 0.5 g and 1.0 g.
        ring_pen = QColor(Colors.white)
        ring_pen.setAlphaF(0.25)
        p.setPen(QPen(ring_pen, 1.0))
        p.setBrush(Qt.NoBrush)
        for g_ring in (0.5, 1.0):
            r = G_METER_RADIUS * (g_ring / G_METER_FULL_SCALE)
            p.drawEllipse(G_METER_CENTER, r, r)

        # Cross-hair through the centre.
        cross_pen = QColor(Colors.white)
        cross_pen.setAlphaF(0.3)
        p.setPen(QPen(cross_pen, 1.0))
        p.drawLine(QPointF(G_METER_CENTER.x() - G_METER_RADIUS, G_METER_CENTER.y()),
                   QPointF(G_METER_CENTER.x() + G_METER_RADIUS, G_METER_CENTER.y()))
        p.drawLine(QPointF(G_METER_CENTER.x(), G_METER_CENTER.y() - G_METER_RADIUS),
                   QPointF(G_METER_CENTER.x(), G_METER_CENTER.y() + G_METER_RADIUS))

        # Plot dot. Lateral g grows the dot rightward; longitudinal g
        # is tricky — we treat negative as braking (dot up = forward
        # weight transfer), positive as acceleration (dot down).
        scale = G_METER_RADIUS / G_METER_FULL_SCALE
        dx = max(-G_METER_RADIUS, min(G_METER_RADIUS, d.g_lat * scale))
        dy = max(-G_METER_RADIUS, min(G_METER_RADIUS, -d.g_long * scale))
        combined = math.sqrt(d.g_lat ** 2 + d.g_long ** 2)
        if combined < 1.0:
            dot_color = Colors.green
        elif combined < 1.5:
            dot_color = Colors.yellow
        else:
            dot_color = Colors.red
        p.setPen(Qt.NoPen)
        p.setBrush(dot_color)
        p.drawEllipse(QPointF(G_METER_CENTER.x() + dx,
                              G_METER_CENTER.y() + dy), 4.0, 4.0)

        # Combined-g readout under the meter.
        p.setFont(label_font(11))
        p.setPen(Colors.white)
        text_rect = QRectF(G_METER_CENTER.x() - G_METER_RADIUS,
                           G_METER_CENTER.y() + G_METER_RADIUS + 2.0,
                           G_METER_RADIUS * 2.0, 14.0)
        p.drawText(text_rect, Qt.AlignCenter, f"{combined:.2f} g")

    # --- right column: status panel -----------------------------------------

    def _draw_status(self, p: QPainter, d: InputsData) -> None:
        """Compact status row: per-zone damage chips, tyres-out count,
        performance-mode label.

        Damage chips light up only above a 5 % threshold so the panel
        stays empty on undamaged cars. Tyres-out shows only when a
        tyre is actually off-track.
        """
        p.setFont(label_font(11))
        x = STATUS_RECT.x() + 4.0
        y = STATUS_RECT.y()

        # Damage zones (front / rear / left / right / centre): chips lit
        # when the zone is non-trivially damaged. Colour scales with
        # severity.
        chip_w = 18.0
        chip_h = 18.0
        zone_labels = ("F", "R", "L", "I", "C")  # front/rear/left/right/centre
        any_damage = any(z > 0.05 for z in d.damage)
        if any_damage:
            p.setPen(QColor(Colors.white))
            label_color = QColor(Colors.white)
            label_color.setAlphaF(0.7)
            p.setPen(label_color)
            p.drawText(QRectF(x, y, 32.0, chip_h),
                       Qt.AlignLeft | Qt.AlignVCenter, "DMG")
            cx = x + 32.0
            for zone, zone_label in zip(d.damage, zone_labels):
                color = self._damage_color(zone)
                p.setPen(Qt.NoPen)
                p.setBrush(color)
                p.drawRoundedRect(QRectF(cx, y, chip_w, chip_h), 3.0, 3.0)
                p.setPen(Colors.black if zone > 0.05 else Colors.white)
                p.drawText(QRectF(cx, y, chip_w, chip_h),
                           Qt.AlignCenter, zone_label)
                cx += chip_w + 2.0

        # Tyres-out chip on the next line.
        line2_y = y + chip_h + 6.0
        if d.tyres_out > 0:
            chip = QRectF(x, line2_y, 60.0, chip_h)
            p.setPen(Qt.NoPen)
            p.setBrush(Colors.red)
            p.drawRoundedRect(chip, 3.0, 3.0)
            p.setPen(Colors.white)
            p.drawText(chip, Qt.AlignCenter, f"OUT {d.tyres_out}")
            mode_x = chip.right() + 6.0
        else:
            mode_x = x

        # Performance mode label — last so it can soak up whatever space
        # remains on the row.
        if d.performance_mode:
            mode_rect = QRectF(mode_x, line2_y,
                               STATUS_RECT.right() - mode_x - 4.0, chip_h)
            p.setPen(QColor(Colors.white))
            p.drawText(mode_rect, Qt.AlignLeft | Qt.AlignVCenter,
                       f"MODE {d.performance_mode}")

    @staticmethod
    def _damage_color(zone_value: float) -> QColor:
        """Map a 0..1 damage value to a chip colour. Below 5 % the chip
        renders as a dim outline so undamaged zones don't dominate."""
        if zone_value < 0.05:
            c = QColor(Colors.white)
            c.setAlphaF(0.15)
            return c
        if zone_value < 0.25:
            return Colors.yellow
        if zone_value < 0.6:
            c = QColor(Colors.red)
            return c
        return Colors.red
