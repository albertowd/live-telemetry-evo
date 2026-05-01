from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from ..colors import Colors
from ..fonts import label_font
from ..interpolation import DEFAULT_TORQUE_CURVE, Power
from ..resources import has_resource, tinted
from ..telemetry import EngineData
from .draggable import DraggableWidget


# Original AC plugin sizes — we paint in this logical coord system and let
# the widget's actual size scale via QPainter transforms.
LOGICAL_W = 512.0
# Bumped from 120 to fit the Phase 2 readouts row at the bottom — keep
# in sync with ``ENGINE_LOGICAL_H`` in ``layout.py``.
LOGICAL_H = 148.0
BOOST_BAR_RECT = QRectF(0.0, 0.0, LOGICAL_W, 24.0)
RPM_BAR_RECT = QRectF(0.0, 26.0, LOGICAL_W, 50.0)
LABEL_RECT = QRectF(0.0, 77.0, LOGICAL_W, 22.0)
AIDS_RECT = QRectF(0.0, 100.0, LOGICAL_W, 20.0)
READOUTS_RECT = QRectF(0.0, 122.0, LOGICAL_W, 24.0)

# Chip strip cell width. Conservative so up to eight Phase 1 chips fit
# inside ``LOGICAL_W`` simultaneously (8 × 64 = 512); typical play only
# shows three or four at once.
CHIP_W = 64.0
# Readouts cell width — the strip centres only the populated cells, so
# this is just per-cell breathing room, not a hard fit constraint.
READOUT_W = 64.0


def _format_gear(gear: int) -> str:
    """AC1/Evo convention: 0=R, 1=N, 2+ = forward gears.
    The driver expects forward gears displayed as 1, 2, 3..."""
    if gear <= 0:
        return "R"
    if gear == 1:
        return "N"
    return str(gear - 1)


class EngineView(DraggableWidget):
    """Engine widget: RPM/power bar + boost bar, ported from BoostBar/RPMPower."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data = EngineData()
        self._power = Power.from_torque_curve(DEFAULT_TORQUE_CURVE)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def set_data(self, data: EngineData) -> None:
        self._data = data
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        # Scale logical coords (512x148) into the actual widget rect.
        sx = self.width() / LOGICAL_W
        sy = self.height() / LOGICAL_H
        p.scale(sx, sy)

        d = self._data

        # RPM/power bar background. Prefer the game-supplied rpm_percent
        # (exact fraction of redline) over rpm/max_rpm — works even when the
        # absolute redline is unknown.
        p.fillRect(RPM_BAR_RECT, Colors.black)
        if d.rpm_percent >= 0.0:
            ratio = min(1.0, d.rpm_percent)
        else:
            ratio = min(1.0, d.rpm / d.max_rpm) if d.max_rpm > 0.0 else 0.0
        # Game-driven shift hints override the power-curve colour: red on
        # the upshift cue (full bar acts as a shift light), blue on the
        # downshift cue. Falls back to the power-curve colour otherwise.
        if d.shift_up_hint:
            color = Colors.red
        elif d.shift_down_hint:
            color = Colors.blue
        else:
            color = self._power.interpolate_color(d.rpm)
        rpm_fill = QRectF(RPM_BAR_RECT)
        rpm_fill.setWidth(RPM_BAR_RECT.width() * ratio)
        p.fillRect(rpm_fill, color)

        # HP / gear+speed / RPM labels under the bar. Prefer live current_bhp
        # from AC Evo's graphics block over the synthesized curve when
        # available — exact engine output, no (1+boost) hack needed.
        if d.current_bhp >= 0.0:
            hp = int(d.current_bhp)
        else:
            torque_at_rpm_hp = self._power.interpolate(d.rpm)
            hp = int(torque_at_rpm_hp * (1.0 + d.turbo_boost))
        p.setFont(label_font(20))
        p.setPen(color)

        p.drawText(LABEL_RECT, Qt.AlignLeft | Qt.AlignVCenter, f"  {hp} HP")
        p.drawText(LABEL_RECT, Qt.AlignCenter | Qt.AlignVCenter,
                   f"{_format_gear(d.gear)}   {int(d.speed_kmh)} km/h")
        p.drawText(LABEL_RECT, Qt.AlignRight | Qt.AlignVCenter, f"{int(d.rpm)} RPM  ")

        self._draw_aids(p, d)
        self._draw_readouts(p, d)

        # Boost bar (only meaningful when the car has a turbo).
        p.fillRect(BOOST_BAR_RECT, Colors.black)
        if d.max_turbo_boost > 0.05:
            b_ratio = max(0.0, d.turbo_boost / max(0.1, d.max_turbo_boost))
            b_color: QColor = Colors.white if b_ratio < 0.9 else Colors.green
            fill_w = BOOST_BAR_RECT.width() * b_ratio
            b_fill = QRectF(BOOST_BAR_RECT.x(), BOOST_BAR_RECT.y(),
                            fill_w, BOOST_BAR_RECT.height())
            p.fillRect(b_fill, b_color)

            # Two-pass text: black where the fill is behind it, fill-color on
            # the empty (black) part. Keeps the value readable at any boost.
            p.setFont(label_font(14))
            text = f"{max(0.0, d.turbo_boost):.2f} bar"

            p.save()
            p.setClipRect(b_fill)
            p.setPen(Colors.black)
            p.drawText(BOOST_BAR_RECT, Qt.AlignCenter, text)
            p.restore()

            p.save()
            p.setClipRect(QRectF(BOOST_BAR_RECT.x() + fill_w, BOOST_BAR_RECT.y(),
                                 BOOST_BAR_RECT.width() - fill_w, BOOST_BAR_RECT.height()))
            p.setPen(b_color)
            p.drawText(BOOST_BAR_RECT, Qt.AlignCenter, text)
            p.restore()

        p.end()

    def _draw_aids(self, p: QPainter, d: EngineData) -> None:
        """Driver-aid status row.

        Each chip is ``(label, color, engaging, icon_name)``: the icon is
        rendered tinted by ``color`` when ``resources/img/<icon_name>.png``
        exists; otherwise the chip falls back to the text label so a
        missing icon is visible-but-bounded rather than blank. Chip colour
        brightens when the aid is *currently engaging* and dims to alpha
        0.4 when the aid is enabled-but-idle (PIT limiter is binary and
        always drawn fully bright when on).
        """
        chips: list[tuple[str, QColor, bool, str]] = []
        if d.pit_limiter:
            chips.append(("PIT", Colors.yellow, True, "car-speed-limiter"))
        if d.tc_level > 0.0:
            chips.append(("TC", Colors.green, d.tc_in_action, "car-traction-control"))
        if d.abs_level > 0.0:
            chips.append(("ABS", Colors.blue, d.abs_in_action, "car-brake-abs"))
        if d.esc_active:
            chips.append(("ESC", Colors.red, True, "car-esp"))
        if d.launch_active:
            chips.append(("LC", Colors.green, True, "rocket-launch"))
        if d.drs_available:
            # Bright when the driver's actually deployed it; dim while
            # the zone allows it but the driver hasn't pressed the button.
            chips.append(("DRS", Colors.blue, d.drs_enabled, "car-cruise-control"))
        if d.ers_charging:
            chips.append(("ERS", Colors.yellow, True, "battery-charging"))
        if d.wrong_way:
            chips.append(("WW", Colors.red, True, "alert"))
        if not d.valid_lap:
            chips.append(("INV", Colors.red, True, "flag-remove"))
        if d.last_lap:
            chips.append(("LAST", Colors.white, True, "flag-checkered"))
        if not chips:
            return

        # Compress per-cell width when we'd otherwise overflow the widget;
        # 10 chips × 64 = 640 > LOGICAL_W. Keep a floor so the icons /
        # text don't disappear entirely on a worst-case "everything on at
        # once" frame.
        chip_w = min(CHIP_W, max(40.0, LOGICAL_W / len(chips)))
        total_w = chip_w * len(chips)
        x = (LOGICAL_W - total_w) / 2.0
        p.setFont(label_font(14))
        for label, color, engaging, icon_name in chips:
            chip_color = QColor(color)
            if not engaging:
                chip_color.setAlphaF(0.4)
            self._draw_chip(p, label, chip_color, icon_name,
                            QRectF(x, AIDS_RECT.y(), chip_w, AIDS_RECT.height()))
            x += chip_w

    def _draw_chip(self, p: QPainter, label: str, color: QColor,
                   icon_name: str, rect: QRectF) -> None:
        """Render a chip — tinted icon when the PNG is on disk, otherwise
        the text label centred in the cell. This way the rendering path
        is the same whether or not the user has dropped the icon PNG into
        ``resources/img/`` yet."""
        if has_resource(icon_name):
            # Square the icon vertically so the PNG draws as a centred
            # sprite inside the chip cell, padded slightly so adjacent
            # icons don't touch.
            side = max(8.0, rect.height() - 2.0)
            ix = rect.x() + (rect.width() - side) / 2.0
            iy = rect.y() + (rect.height() - side) / 2.0
            pix = tinted(icon_name, int(side), int(side), color)
            p.drawPixmap(QRectF(ix, iy, side, side).topLeft(), pix)
            return
        p.setPen(color)
        p.drawText(rect, Qt.AlignCenter, label)

    def _draw_readouts(self, p: QPainter, d: EngineData) -> None:
        """Phase 2 analog readouts row.

        Each populated cell is ``label + value + unit`` (text-only until
        the matching MDI PNG lands in ``resources/img/``; once present
        the icon replaces the label inline). Cells with non-positive
        values are hidden so a car/source that doesn't publish a given
        field doesn't leave dead space in the strip.
        """
        cells: list[tuple[str, str, str]] = []  # (icon_name, label, value+unit)
        if d.water_temp_c > 0.0:
            cells.append(("water-thermometer", "WAT", f"{int(d.water_temp_c)}°C"))
        if d.oil_temp_c > 0.0:
            cells.append(("oil-temperature", "OIL", f"{int(d.oil_temp_c)}°C"))
        if d.oil_pressure_bar > 0.0:
            cells.append(("oil-level", "OILP", f"{d.oil_pressure_bar:.1f}bar"))
        if d.fuel_pressure_bar > 0.0:
            cells.append(("gas-station", "FUELP", f"{d.fuel_pressure_bar:.1f}bar"))
        if d.exhaust_temp_c > 0.0:
            cells.append(("smoke", "EXH", f"{int(d.exhaust_temp_c)}°C"))
        if d.battery_voltage > 0.0:
            cells.append(("car-battery", "BAT", f"{d.battery_voltage:.1f}V"))
        if d.fuel_liters > 0.0:
            cells.append(("fuel", "FUEL", f"{d.fuel_liters:.0f}L"))
        if d.brake_bias > 0.0:
            cells.append(("car-brake-parking", "BBIAS", f"{int(d.brake_bias * 100)}%F"))
        if not cells:
            return

        # Same compression rule as the chip strip — never let the row
        # exceed LOGICAL_W, never collapse below a readable floor.
        cell_w = min(READOUT_W, max(48.0, LOGICAL_W / len(cells)))
        total_w = cell_w * len(cells)
        x = (LOGICAL_W - total_w) / 2.0
        for icon_name, label, value in cells:
            self._draw_readout(p, icon_name, label, value,
                               QRectF(x, READOUTS_RECT.y(),
                                      cell_w, READOUTS_RECT.height()))
            x += cell_w

    def _draw_readout(self, p: QPainter, icon_name: str, label: str,
                      value: str, rect: QRectF) -> None:
        """Render one readout cell: icon (or label) on the left, value on
        the right. Icon is tinted white; the value is white too, sized to
        fit the 24-px row."""
        icon_w = 0.0
        if has_resource(icon_name):
            side = max(10.0, rect.height() - 4.0)
            ix = rect.x() + 2.0
            iy = rect.y() + (rect.height() - side) / 2.0
            pix = tinted(icon_name, int(side), int(side), Colors.white)
            p.drawPixmap(QRectF(ix, iy, side, side).topLeft(), pix)
            icon_w = side + 4.0
        else:
            # Text fallback: small dim label in the icon's slot. Same width
            # budget so the value still lines up across cells.
            label_w = rect.width() * 0.45
            label_color = QColor(Colors.white)
            label_color.setAlphaF(0.55)
            p.setPen(label_color)
            p.setFont(label_font(10))
            p.drawText(QRectF(rect.x() + 2.0, rect.y(), label_w, rect.height()),
                       Qt.AlignLeft | Qt.AlignVCenter, label)
            icon_w = label_w

        p.setPen(Colors.white)
        p.setFont(label_font(11))
        p.drawText(QRectF(rect.x() + icon_w, rect.y(),
                          rect.width() - icon_w - 2.0, rect.height()),
                   Qt.AlignRight | Qt.AlignVCenter, value)
