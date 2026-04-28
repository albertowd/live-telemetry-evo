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
LOGICAL_H = 85.0
RPM_BAR_RECT = QRectF(0.0, 0.0, LOGICAL_W, 50.0)
BOOST_BAR_RECT = QRectF(0.0, 56.0, LOGICAL_W, 24.0)


class EngineView(QWidget):
    """Engine widget: RPM/power bar + boost bar, ported from BoostBar/RPMPower."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data = EngineData()
        self._power = Power(DEFAULT_TORQUE_CURVE)
        self.setMinimumSize(384, 64)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def set_data(self, data: EngineData) -> None:
        self._data = data
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        # Scale logical coords (512x85) into the actual widget rect.
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

        # HP/RPM labels under the bar.
        torque_at_rpm_hp = self._power.interpolate(d.rpm)
        hp = int(torque_at_rpm_hp * (1.0 + d.turbo_boost))
        p.setFont(label_font(20))
        p.setPen(color)

        label_rect = QRectF(0.0, RPM_BAR_RECT.bottom() + 1, LOGICAL_W, 22.0)
        p.drawText(label_rect, Qt.AlignLeft | Qt.AlignVCenter, f"  {hp} HP")
        p.drawText(label_rect, Qt.AlignRight | Qt.AlignVCenter, f"{int(d.rpm)} RPM  ")

        # Boost bar (only meaningful when the car has a turbo).
        p.fillRect(BOOST_BAR_RECT, Colors.black)
        if d.max_turbo_boost > 0.05:
            b_ratio = max(0.0, d.turbo_boost / max(0.1, d.max_turbo_boost))
            b_color: QColor = Colors.white if b_ratio < 0.9 else Colors.green
            b_fill = QRectF(BOOST_BAR_RECT)
            b_fill.setWidth(BOOST_BAR_RECT.width() * b_ratio)
            p.fillRect(b_fill, b_color)

            p.setFont(label_font(14))
            p.setPen(b_color)
            p.drawText(BOOST_BAR_RECT, Qt.AlignCenter, f"{max(0.0, d.turbo_boost):.2f} bar")

        p.end()
