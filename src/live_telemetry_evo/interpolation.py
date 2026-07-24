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

    def __init__(self, hp_curve: list[tuple[float, float]]) -> None:
        super().__init__(hp_curve)

    @classmethod
    def from_torque_curve(cls, torque_curve: list[tuple[float, float]]) -> "Power":
        """Build from a torque-vs-RPM table via the canonical 5252 constant.
        Used for the hardcoded mock curve when no live data is available."""
        hp_curve = [(rpm, (torque * rpm) / 5252.0) for rpm, torque in torque_curve]
        return cls(hp_curve)

    @classmethod
    def from_peaks(cls, max_torque: float, max_power_hp: float, max_rpm: float) -> "Power":
        """Synthesize a Power curve from AC Evo's static peaks.

        The game exposes maxTorque (Nm), maxPower (HP), and maxRpm but no
        actual curve. We fit a torque-shaped curve with peak torque around
        65% redline and peak power around 90%, then scale uniformly so the
        peak HP matches max_power_hp regardless of unit conventions.
        """
        if max_rpm <= 0.0 or max_power_hp <= 0.0 or max_torque <= 0.0:
            return cls.from_torque_curve(DEFAULT_TORQUE_CURVE)

        shape = [
            (max_rpm * 0.10, 0.50),
            (max_rpm * 0.35, 0.85),
            (max_rpm * 0.65, 1.00),  # peak torque
            (max_rpm * 0.90, 0.93),  # peak power (HP highest here)
            (max_rpm,        0.75),
        ]
        # HP ∝ torque × rpm; scale uniformly so peak HP == max_power_hp.
        raw = [(rpm, max_torque * t * rpm) for rpm, t in shape]
        peak = max(p[1] for p in raw)
        scale = max_power_hp / peak if peak > 0.0 else 0.0
        return cls([(rpm, hp * scale) for rpm, hp in raw])

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
    """Pressure colour bands around the game-reported normalized pressure
    (1.0 = ideal cold pressure for the current compound).

    AC Evo holds norm at 1.0 across a wide window and only moves it once
    pressure runs well over the compound's ideal — so by the time norm
    leaves 1.0 the tyre is already out of spec. Bands sit tight (±0.02
    green, 0.01 lerp) so any move off 1.0 turns the icon decisively
    rather than fading in slowly."""

    GREEN_HALF_WIDTH = 0.02
    LERP_HALF_WIDTH = 0.01

    def interpolate_color(self, norm: float) -> QColor:
        delta = norm - 1.0
        if abs(delta) <= self.GREEN_HALF_WIDTH:
            return Colors.green
        edge = self.GREEN_HALF_WIDTH + self.LERP_HALF_WIDTH
        if delta < -edge:
            return Colors.blue
        if delta > edge:
            return Colors.red
        t = (abs(delta) - self.GREEN_HALF_WIDTH) / self.LERP_HALF_WIDTH
        return lerp_color(Colors.green, Colors.blue if delta < 0 else Colors.red, t)


class TireTemp(Curve):
    """Tire/brake temperature curve. Returns a 0..1 grip-ish band with
    cold/hot colours. When ``norm`` is supplied (the game's per-compound
    normalized temp, 1.0 = ideal) it is used directly for the band
    saturation; otherwise the band is read off the embedded curve. The
    cold-vs-hot side is still picked from the raw temp against the curve's
    peak — slightly off for compounds whose ideal sits far from the
    curve's reference, but correct for the common case."""

    def interpolate_color(self, temp: float, norm: float | None = None) -> QColor:
        if not self._curve:
            return Colors.white
        interp = norm if norm is not None else self.interpolate(temp)
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


# Brake disc temperature operating window. Cold below ~150 C and hot above
# ~600 C reduce stopping power; race-pad sweet spot sits roughly 250-500 C.
DEFAULT_BRAKE_TEMP_CURVE: list[tuple[float, float]] = [
    (50.0, 0.85),
    (200.0, 0.97),
    (400.0, 1.00),  # peak
    (600.0, 0.97),
    (900.0, 0.85),
]
