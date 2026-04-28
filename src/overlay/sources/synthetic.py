from __future__ import annotations

import math
import random

from PySide6.QtCore import QObject, QTimer

from ..telemetry import TelemetryFrame
from .base import TelemetrySource


class SyntheticTelemetrySource(TelemetrySource):
    """Synthetic telemetry generator.

    Drives a coherent fake "lap" — throttle/brake oscillate, RPM follows
    throttle, tires heat under load, pressures drift, wear ticks down slowly.
    Used for development, design iteration and screenshots without a running
    sim.
    """

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
        # pylint: disable-next=no-member  # QTimer.timeout is a PySide6 Signal
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        self._t += self._dt
        t = self._t

        throttle = max(0.0, math.sin(t * 0.6) * 0.8 + 0.2)
        brake = max(0.0, -math.sin(t * 0.6) * 0.6)
        cornering = math.sin(t * 0.35)

        e = self._frame.engine
        e.rpm = 1200.0 + throttle * (e.max_rpm - 1500.0) + math.sin(t * 7.0) * 80.0
        e.turbo_boost = max(0.0, throttle * 1.0 + math.sin(t * 4.2) * 0.05)
        e.max_turbo_boost = max(e.max_turbo_boost, e.turbo_boost)

        for wid, w in self._frame.wheels.items():
            is_front = wid[0] == "F"
            is_left = wid[1] == "L"

            load_base = 50.0
            brake_bias = 30.0 * brake * (1.0 if is_front else 0.4)
            corner_bias = 25.0 * (cornering if not is_left else -cornering)
            corner_bias = max(0.0, corner_bias)
            w.tire_l = load_base + brake_bias + corner_bias + random.uniform(-2.0, 2.0)

            w.susp_t = min(w.susp_m_t, max(0.005, (w.tire_l / 200.0) * w.susp_m_t))

            w.height = 30.0 - (w.tire_l - 50.0) * 0.05 + math.sin(t * 3.1 + (0 if is_front else 1.0)) * 1.5

            w.camber = -0.03 + (corner_bias * 0.0008 if not is_left else -corner_bias * 0.0008)

            target_p = 26.0 + (w.tire_t_c - 80.0) * 0.04
            w.tire_p += (target_p - w.tire_p) * 0.02

            heat_in = throttle * (1.2 if is_front else 1.0) + brake * (0.8 if is_front else 0.5)
            cool = 0.4
            w.tire_t_c += (heat_in - cool) * self._dt * 6.0
            w.tire_t_c = max(40.0, min(140.0, w.tire_t_c))
            skew = corner_bias * 0.05
            w.tire_t_i = w.tire_t_c + (skew if is_left else -skew) + random.uniform(-1.0, 1.0)
            w.tire_t_m = w.tire_t_c + random.uniform(-1.0, 1.0)
            w.tire_t_o = w.tire_t_c + (-skew if is_left else skew) + random.uniform(-1.0, 1.0)

            w.tire_d = min(4.0, w.tire_d + self._dt * 0.02)
            if random.random() < 0.0005:
                w.tire_d = min(4.0, w.tire_d + 0.5)

            w.tire_w = max(0.94, w.tire_w - self._dt * 0.0003)

            brake_heat_in = brake * (3.0 if is_front else 1.5)
            brake_cool = 0.4
            w.brake_t += (brake_heat_in - brake_cool) * self._dt * 30.0
            w.brake_t = max(50.0, min(900.0, w.brake_t))

            hard_brake = brake > 0.35
            slip_mock = brake > 0.5 and random.random() < 0.05
            w.lock = hard_brake and slip_mock and is_front
            w.abs_active = hard_brake and not w.lock and is_front

        self.frame.emit(self._frame)
