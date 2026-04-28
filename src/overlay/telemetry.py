from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QTimer, Signal


WHEEL_IDS = ("FL", "FR", "RL", "RR")


@dataclass
class WheelData:
    """Per-wheel telemetry sample. Field names mirror the AC plugin so the
    porting target stays familiar; AC Evo equivalents will be wired in later."""
    abs_active: bool = False
    camber: float = 0.0       # radians, negative = top-in
    height: float = 0.0       # mm, ride height
    lock: bool = False
    susp_t: float = 0.0       # current suspension travel (m)
    susp_m_t: float = 0.1     # max suspension travel (m)
    tire_d: float = 0.0       # dirt level 0..4
    tire_l: float = 0.0       # load (5*kgf units used by the original Load circle)
    tire_p: float = 26.0      # pressure psi
    tire_t_c: float = 80.0    # core temperature C
    tire_t_i: float = 80.0    # inner C
    tire_t_m: float = 80.0    # middle C
    tire_t_o: float = 80.0    # outer C
    tire_w: float = 1.0       # wear 0..1 (1 = new)


@dataclass
class EngineData:
    max_power: float = 500.0   # HP
    max_rpm: float = 8500.0
    max_turbo_boost: float = 1.2
    rpm: float = 0.0
    turbo_boost: float = 0.0


@dataclass
class TelemetryFrame:
    engine: EngineData = field(default_factory=EngineData)
    wheels: dict[str, WheelData] = field(default_factory=lambda: {w: WheelData() for w in WHEEL_IDS})


class TelemetrySource(QObject):
    """Synthetic 60 Hz telemetry generator.

    Drives a coherent fake "lap" — throttle/brake oscillate, RPM follows
    throttle, tires heat under load, pressures drift, wear ticks down slowly.
    Replace with the AC Evo shared-memory reader; keep the `frame` signal shape.
    """

    frame = Signal(TelemetryFrame)

    def __init__(self, hz: int = 60, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._t = 0.0
        self._dt = 1.0 / hz
        self._frame = TelemetryFrame()
        self._frame.engine.max_turbo_boost = 1.2
        for w in self._frame.wheels.values():
            w.susp_m_t = 0.1
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / hz))
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        self._t += self._dt
        t = self._t

        # Drive cycle: throttle/brake oscillate roughly out of phase.
        throttle = max(0.0, math.sin(t * 0.6) * 0.8 + 0.2)
        brake = max(0.0, -math.sin(t * 0.6) * 0.6)
        cornering = math.sin(t * 0.35)  # -1 (left) .. +1 (right)

        e = self._frame.engine
        e.rpm = 1200.0 + throttle * (e.max_rpm - 1500.0) + math.sin(t * 7.0) * 80.0
        e.turbo_boost = max(0.0, throttle * 1.0 + math.sin(t * 4.2) * 0.05)
        e.max_turbo_boost = max(e.max_turbo_boost, e.turbo_boost)

        # Per-wheel dynamics. Outer wheels load up under cornering, fronts
        # under braking, drives heat under throttle. All loose, just enough
        # to make every visual change.
        for wid, w in self._frame.wheels.items():
            is_front = wid[0] == "F"
            is_left = wid[1] == "L"

            # Load: base + braking bias to fronts + cornering to outside wheels.
            load_base = 50.0
            brake_bias = 30.0 * brake * (1.0 if is_front else 0.4)
            corner_bias = 25.0 * (cornering if not is_left else -cornering)
            corner_bias = max(0.0, corner_bias)
            w.tire_l = load_base + brake_bias + corner_bias + random.uniform(-2.0, 2.0)

            # Suspension travel follows load (compression).
            w.susp_t = min(w.susp_m_t, max(0.005, (w.tire_l / 200.0) * w.susp_m_t))

            # Ride height (mm) — bobs around ~30mm, drops under load.
            w.height = 30.0 - (w.tire_l - 50.0) * 0.05 + math.sin(t * 3.1 + (0 if is_front else 1.0)) * 1.5

            # Camber (rad) — small static neg + dynamic on the loaded side.
            w.camber = -0.03 + (corner_bias * 0.0008 if not is_left else -corner_bias * 0.0008)

            # Pressure drifts up with temperature.
            target_p = 26.0 + (w.tire_t_c - 80.0) * 0.04
            w.tire_p += (target_p - w.tire_p) * 0.02

            # Temps: cores rise with load+throttle, inner/outer skew with camber.
            heat_in = throttle * (1.2 if is_front else 1.0) + brake * (0.8 if is_front else 0.5)
            cool = 0.4
            w.tire_t_c += (heat_in - cool) * self._dt * 6.0
            w.tire_t_c = max(40.0, min(140.0, w.tire_t_c))
            skew = corner_bias * 0.05
            w.tire_t_i = w.tire_t_c + (skew if is_left else -skew) + random.uniform(-1.0, 1.0)
            w.tire_t_m = w.tire_t_c + random.uniform(-1.0, 1.0)
            w.tire_t_o = w.tire_t_c + (-skew if is_left else skew) + random.uniform(-1.0, 1.0)

            # Dirt: drifts up slowly, occasionally jumps.
            w.tire_d = min(4.0, w.tire_d + self._dt * 0.02)
            if random.random() < 0.0005:
                w.tire_d = min(4.0, w.tire_d + 0.5)

            # Wear: very slow tick down from 1.0 toward 0.94 to exercise the
            # green/yellow/red bands of the wear bar.
            w.tire_w = max(0.94, w.tire_w - self._dt * 0.0003)

            # Lock + ABS: under hard braking with high slip mock.
            hard_brake = brake > 0.35
            slip_mock = brake > 0.5 and random.random() < 0.05
            w.lock = hard_brake and slip_mock and is_front
            w.abs_active = hard_brake and not w.lock and is_front

        self.frame.emit(self._frame)
