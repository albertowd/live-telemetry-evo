from __future__ import annotations

from PySide6.QtGui import QColor

from .colors import Colors, lerp_color


class Curve:
    """Piecewise linear curve. Mirrors lt_interpolation.Curve."""

    def __init__(self, points: list[tuple[float, float]]) -> None:
        self._curve = sorted(points, key=lambda p: p[0])
        self._max = max(self._curve, key=lambda p: p[1]) if self._curve else (0.0, 0.0)

    def interpolate(self, x: float) -> float:
        for i, (px, py) in enumerate(self._curve):
            if x < px:
                if i == 0:
                    return py
                qx, qy = self._curve[i - 1]
                t = (x - qx) / (px - qx)
                return qy + (py - qy) * t
        return self._curve[-1][1] if self._curve else 0.0

    @property
    def peak(self) -> tuple[float, float]:
        return self._max


class Power(Curve):
    """RPM -> HP curve with peak-aware color coding."""

    def __init__(self, torque_curve: list[tuple[float, float]]) -> None:
        # Convert torque (Nm) to HP using the canonical 5252 constant.
        hp_curve = [(rpm, (torque * rpm) / 5252.0) for rpm, torque in torque_curve]
        super().__init__(hp_curve)

    def interpolate_color(self, rpm: float) -> QColor:
        if self._max[1] <= 0.0:
            return Colors.white
        hp = self.interpolate(rpm)
        perc = hp / self._max[1]
        if perc < 0.995:
            if rpm < self._max[0]:
                return Colors.white if perc < 0.985 else Colors.blue
            return Colors.red
        return Colors.green


class TirePsi:
    """Pressure colour interp around an ideal target."""

    def __init__(self, ideal: float = 26.0) -> None:
        self._ideal = ideal

    def normalised(self, psi: float) -> float:
        return psi / self._ideal if self._ideal > 0.0 else 0.0

    def interpolate_color(self, psi: float) -> QColor:
        perc = self.normalised(psi)
        if perc < 0.95:
            return Colors.blue
        if perc < 1.00:
            return lerp_color(Colors.blue, Colors.green, (perc - 0.95) / 0.05)
        if perc < 1.05:
            return lerp_color(Colors.green, Colors.red, (perc - 1.00) / 0.05)
        return Colors.red


class TireTemp(Curve):
    """Tire temperature curve, returns a 0..1 grip-ish band, with cold/hot colors."""

    def interpolate_color(self, temp: float) -> QColor:
        if not self._curve:
            return Colors.white
        interp = self.interpolate(temp)
        # interp is on the original lut scale; bands match the AC plugin.
        if temp < self._max[0]:
            return lerp_color(Colors.blue, Colors.green, max(0.0, interp - 0.98) / 0.02)
        return lerp_color(Colors.red, Colors.green, max(0.0, interp - 0.98) / 0.02)


# Default curves used when no ACD data is available — tuned to give a sensible,
# colour-shifting display for mocked telemetry. Replace once real AC Evo data
# exposes per-car/per-compound curves.

DEFAULT_TORQUE_CURVE: list[tuple[float, float]] = [
    (1000.0, 200.0),
    (3000.0, 380.0),
    (5500.0, 500.0),
    (7000.0, 470.0),
    (8500.0, 380.0),
]


DEFAULT_TIRE_TEMP_CURVE: list[tuple[float, float]] = [
    (40.0, 0.85),
    (70.0, 0.95),
    (90.0, 1.00),  # peak grip
    (110.0, 0.95),
    (140.0, 0.85),
]
