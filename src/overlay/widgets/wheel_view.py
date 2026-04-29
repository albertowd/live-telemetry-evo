from __future__ import annotations

import math
import time

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
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


LOGICAL_W = 512.0
LOGICAL_H = 271.0
WARNING_TIME_S = 0.5
LOCK_BLINK_PERIOD_S = 0.1


def _draw_tinted(p: QPainter, name: str, rect: QRectF, color: QColor) -> None:
    """Tint an icon and stamp it inside the given logical rect.

    The painter has an active scale transform (logical -> widget), so we hand
    the tint helper the *logical* size; Qt's smooth pixmap transform handles
    the on-screen scaling. The source PNGs are 2048-tall masks, so the
    once-cached scaled mask is always a downscale and looks crisp.
    """
    pix = tinted(name, int(rect.width()), int(rect.height()), color)
    p.drawPixmap(rect.topLeft(), pix)


class WheelView(QWidget):
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
        self._last_paint = time.monotonic()
        self.setMinimumSize(384, 200)
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

        self._draw_tire_and_temps(p, d)
        self._draw_dirt(p, d)
        self._draw_camber(p, d)
        self._draw_suspension(p, d)
        self._draw_wear(p, d)
        self._draw_lock(p, d, delta_t)
        self._draw_pressure(p, d)
        self._draw_height(p, d)
        self._draw_load(p, d)  # last, on top
        self._draw_label(p)

        p.end()

    # --- components ---------------------------------------------------------

    def _draw_tire_and_temps(self, p: QPainter, d: WheelData) -> None:
        rect = QRectF(176.0, 0.0, 160.0, 256.0)

        # Tire silhouette tinted by composite temperature (mirrors lt_components.Tire).
        body = (d.tire_t_c * 0.75
                + ((d.tire_t_i + d.tire_t_m + d.tire_t_o) / 3.0) * 0.25)
        body_color = QColor(self._temp.interpolate_color(body))
        _draw_tinted(p, "tire", rect, body_color)

        # Temps overlay: 3 columns x 8 rows. Inner/Mid/Outer get the top+bottom
        # bumps; the core temp fills the central 75%.
        pad = 12.0
        quarter = (rect.height() - 2.0 * pad) * 0.125
        part = (rect.width() - 2.0 * pad) / 3.0
        inner_x = rect.x() + pad
        outer_x = rect.x() + pad + 2.0 * part
        top_y = rect.y() + pad

        core_color = QColor(self._temp.interpolate_color(d.tire_t_c))
        core_color.setAlphaF(0.85)
        p.setPen(Qt.NoPen)
        p.setBrush(core_color)
        p.drawRect(QRectF(inner_x, top_y + quarter, part * 3.0, quarter * 6.0))

        for value, x in (
            (d.tire_t_i, inner_x),
            (d.tire_t_m, rect.x() + pad + part),
            (d.tire_t_o, outer_x),
        ):
            c = QColor(self._temp.interpolate_color(value))
            p.setBrush(c)
            p.drawRect(QRectF(x, top_y, part, quarter))
            p.drawRect(QRectF(x, top_y + quarter * 7.0, part, quarter))

    def _draw_dirt(self, p: QPainter, d: WheelData) -> None:
        full = QRectF(188.0, 128.0, 136.0, 116.0)
        dirt = max(0.0, min(4.0, d.tire_d)) / 4.0 * full.height()
        dirt_rect = QRectF(full.x(), full.bottom() - dirt, full.width(), dirt)
        c = QColor(Colors.brown)
        c.setAlphaF(0.7)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawRect(dirt_rect)

    def _draw_camber(self, p: QPainter, d: WheelData) -> None:
        rect = QRectF(170.0, 256.0, 172.0, 15.0)
        tan = math.tan(d.camber) * rect.width()
        tan_left = -(tan if d.camber < 0.0 else 0.0)
        tan_right = tan if d.camber > 0.0 else 0.0
        poly = QPolygonF([
            QPointF(rect.x(), rect.y() + tan_left),
            QPointF(rect.x(), rect.bottom()),
            QPointF(rect.right(), rect.bottom()),
            QPointF(rect.right(), rect.y() + tan_right),
        ])
        p.setPen(Qt.NoPen)
        p.setBrush(Colors.white)
        p.drawPolygon(poly)

    def _draw_suspension(self, p: QPainter, d: WheelData) -> None:
        rect = QRectF(self._x_left(346.0, 64.0), 0.0, 64.0, 256.0)
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
        # parity with LiveTelemetry. 10x44 padding inside the icon graphic.
        inner = QRectF(rect.x() + 10.0, rect.y() + 44.0,
                       rect.width() - 20.0, rect.height() - 88.0)
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

        # Display the 0.85..1.00 range — AC Evo reports wear on a small
        # scale (1.0 = fresh, ~0.85 = significantly worn), so a narrower
        # band would leave the bar pinned at full all session.
        if d.tire_w > 0.95:
            color = Colors.green
        elif d.tire_w > 0.90:
            color = Colors.yellow
        else:
            color = Colors.red
        wear = max(0.0, min(1.0, (d.tire_w - 0.85) / 0.15))
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
        temp_color = QColor(self._brake_temp.interpolate_color(d.brake_t))

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

        _draw_tinted(p, "brake", rect, color)

        # Disc temperature label below the icon, always in the temp-tint
        # color so the value stays legible even when ABS/lock force the
        # icon blue/yellow.
        p.setFont(label_font(18))
        p.setPen(temp_color)
        label_rect = QRectF(rect.x() - 20.0, rect.bottom() + 2.0,
                            rect.width() + 40.0, 22.0)
        p.drawText(label_rect, Qt.AlignCenter, f"{int(d.brake_t)} °C")

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
        rect = QRectF(self._x_left(430.0, 64.0), 208.0, 64.0, 48.0)
        if d.height < 20.0:
            self._height_warn_until = time.monotonic() + WARNING_TIME_S
        color = Colors.red if time.monotonic() < self._height_warn_until else Colors.white

        _draw_tinted(p, "height", rect, color)

        p.setFont(label_font(16))
        p.setPen(color)
        text_rect = QRectF(rect.x() - 20.0, rect.y(),
                           rect.width() + 40.0, rect.height())
        p.drawText(text_rect, Qt.AlignCenter, f"{d.height:.1f} mm")

    def _draw_load(self, p: QPainter, d: WheelData) -> None:
        # The load circle stays centred over the tire and grows with load.
        # Original Box: (128, 0, 256, 256) → centre (256, 128).
        center = QPointF(256.0, 128.0)
        diameter = max(40.0, min(256.0, d.tire_l * 2.4))
        rect = QRectF(center.x() - diameter / 2.0,
                      center.y() - diameter / 2.0,
                      diameter, diameter)
        c = QColor(Colors.white)
        c.setAlphaF(0.85)
        _draw_tinted(p, "load", rect, c)

    def _draw_label(self, p: QPainter) -> None:
        p.setFont(label_font(20))
        p.setPen(Colors.white)
        if self._is_left:
            anchor = QRectF(LOGICAL_W - 80.0, 4.0, 76.0, 24.0)
            align = Qt.AlignRight | Qt.AlignVCenter
        else:
            anchor = QRectF(4.0, 4.0, 76.0, 24.0)
            align = Qt.AlignLeft | Qt.AlignVCenter
        p.drawText(anchor, align, self._id)

        # Compound abbreviation below the wheel ID, when known. First three
        # uppercase chars keep "SOFT" / "MEDIUM" / "HARD" / "INTER" / "WET"
        # readable while staying out of the load circle's footprint.
        if self._data.compound:
            p.setFont(label_font(14))
            compound_rect = QRectF(anchor.x(), anchor.bottom() + 1.0,
                                   anchor.width(), 18.0)
            p.drawText(compound_rect, align, self._data.compound[:3].upper())
