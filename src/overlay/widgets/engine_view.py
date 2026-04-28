from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from ..colors import Colors
from ..fonts import label_font
from ..interpolation import DEFAULT_TORQUE_CURVE, Power
from ..telemetry import EngineData


# Original AC plugin sizes — we paint in this logical coord system and let
# the widget's actual size scale via QPainter transforms.
LOGICAL_W = 512.0
LOGICAL_H = 120.0
BOOST_BAR_RECT = QRectF(0.0, 0.0, LOGICAL_W, 24.0)
RPM_BAR_RECT = QRectF(0.0, 26.0, LOGICAL_W, 50.0)
LABEL_RECT = QRectF(0.0, 77.0, LOGICAL_W, 22.0)
AIDS_RECT = QRectF(0.0, 100.0, LOGICAL_W, 20.0)


def _format_gear(gear: int) -> str:
    """AC1/Evo convention: 0=R, 1=N, 2+ = forward gears.
    The driver expects forward gears displayed as 1, 2, 3..."""
    if gear <= 0:
        return "R"
    if gear == 1:
        return "N"
    return str(gear - 1)


class EngineView(QWidget):
    """Engine widget: RPM/power bar + boost bar, ported from BoostBar/RPMPower."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data = EngineData()
        self._power = Power.from_torque_curve(DEFAULT_TORQUE_CURVE)
        self._power_peaks: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self.setMinimumSize(384, 64)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def set_data(self, data: EngineData) -> None:
        peaks = (data.max_torque, data.max_power, data.max_rpm)
        if peaks != self._power_peaks and all(v > 0.0 for v in peaks):
            self._power = Power.from_peaks(*peaks)
            self._power_peaks = peaks
        self._data = data
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        # Scale logical coords (512x120) into the actual widget rect.
        sx = self.width() / LOGICAL_W
        sy = self.height() / LOGICAL_H
        p.scale(sx, sy)

        d = self._data

        # RPM/power bar background.
        p.fillRect(RPM_BAR_RECT, Colors.black)
        ratio = min(1.0, d.rpm / d.max_rpm) if d.max_rpm > 0.0 else 0.0
        color = self._power.interpolate_color(d.rpm)
        rpm_fill = QRectF(RPM_BAR_RECT)
        rpm_fill.setWidth(RPM_BAR_RECT.width() * ratio)
        p.fillRect(rpm_fill, color)

        # HP / gear+speed / RPM labels under the bar.
        torque_at_rpm_hp = self._power.interpolate(d.rpm)
        hp = int(torque_at_rpm_hp * (1.0 + d.turbo_boost))
        p.setFont(label_font(20))
        p.setPen(color)

        p.drawText(LABEL_RECT, Qt.AlignLeft | Qt.AlignVCenter, f"  {hp} HP")
        p.drawText(LABEL_RECT, Qt.AlignCenter | Qt.AlignVCenter,
                   f"{_format_gear(d.gear)}   {int(d.speed_kmh)} km/h")
        p.drawText(LABEL_RECT, Qt.AlignRight | Qt.AlignVCenter, f"{int(d.rpm)} RPM  ")

        self._draw_aids(p, d)

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
        """Driver-aid status row: PIT / TC / ABS chips, only when active."""
        chips: list[tuple[str, QColor]] = []
        if d.pit_limiter:
            chips.append(("PIT", Colors.yellow))
        if d.tc_level > 0.0:
            chips.append(("TC", Colors.green))
        if d.abs_level > 0.0:
            chips.append(("ABS", Colors.blue))
        if not chips:
            return

        chip_w = 80.0
        total_w = chip_w * len(chips)
        x = (LOGICAL_W - total_w) / 2.0
        p.setFont(label_font(16))
        for label, color in chips:
            p.setPen(color)
            p.drawText(QRectF(x, AIDS_RECT.y(), chip_w, AIDS_RECT.height()),
                       Qt.AlignCenter, label)
            x += chip_w
