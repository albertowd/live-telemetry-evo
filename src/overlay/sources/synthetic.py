from __future__ import annotations

import math
import random

from PySide6.QtCore import QObject, QTimer

from ..interpolation import (Curve, DEFAULT_BRAKE_TEMP_CURVE,
                              DEFAULT_TIRE_TEMP_CURVE)
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
        for wid, w in self._frame.wheels.items():
            w.susp_m_t = 0.1
            w.compound = "MEDIUM" if wid[0] == "F" else "HARD"
        # Synthetic stand-ins for the game's per-compound normalized
        # temperatures. Driving the mock fields off the same curves the
        # widget used to use keeps the mock view colour-bands matching
        # what the real source produces.
        self._tire_curve = Curve(DEFAULT_TIRE_TEMP_CURVE)
        self._brake_curve = Curve(DEFAULT_BRAKE_TEMP_CURVE)
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
        e.speed_kmh = max(0.0, throttle * 230.0 - brake * 40.0
                          + math.sin(t * 0.8) * 15.0)
        # Cycle through gears 1..5 over time so the readout actually moves.
        e.gear = 2 + int((t * 0.4) % 5)
        e.abs_level = 1.0
        e.tc_level = 1.0
        # Aids "engage" when the corresponding stress is high — TC under
        # heavy throttle, ABS under heavy braking. Lets the overlay show
        # the dim/bright chip differentiation without a running game.
        e.tc_in_action = throttle > 0.85
        e.abs_in_action = brake > 0.55
        # Pit limiter pulses on briefly every ~30 s so the chip is visible.
        e.pit_limiter = (math.sin(t * 0.21) > 0.97)
        # Shift hints fire near the redline / off-throttle window so the
        # RPM bar's shift-light colour change is visible in synthetic mode.
        rpm_ratio = e.rpm / e.max_rpm if e.max_rpm > 0.0 else 0.0
        e.shift_up_hint = rpm_ratio > 0.93
        e.shift_down_hint = rpm_ratio < 0.20 and throttle > 0.5

        # Phase 1 driver-aid / status chips — driven on slow oscillators
        # so each chip blinks in/out in the mock view, letting the user
        # eyeball every code path without a running game.
        e.esc_active = math.sin(t * 0.42) > 0.85
        e.launch_active = t < 5.0
        e.drs_available = math.sin(t * 0.18) > 0.0
        e.drs_enabled = e.drs_available and throttle > 0.6
        e.ers_charging = math.sin(t * 0.31) > 0.0
        e.wrong_way = math.sin(t * 0.05) < -0.97
        e.valid_lap = math.sin(t * 0.07) > -0.95
        e.last_lap = math.sin(t * 0.04) > 0.97

        # Phase 2 analog readouts — plausible idle/cruise values that
        # drift over time so the readout chips are visible and update.
        e.water_temp_c = 88.0 + math.sin(t * 0.10) * 8.0 + throttle * 6.0
        e.oil_temp_c = 95.0 + math.sin(t * 0.08) * 12.0 + throttle * 10.0
        e.oil_pressure_bar = 4.0 + (e.rpm / e.max_rpm) * 1.5
        e.fuel_pressure_bar = 3.0 + math.sin(t * 0.7) * 0.2
        e.exhaust_temp_c = 550.0 + throttle * 250.0 + math.sin(t * 0.3) * 30.0
        e.battery_voltage = 13.6 + math.sin(t * 0.05) * 0.4
        # Drain ~1 L every 10 s, refill at 50 L when empty so the readout
        # cycles within a single dev session.
        e.fuel_liters = max(0.0, 50.0 - (t * 0.1) % 50.0)
        e.brake_bias = 0.55 + math.sin(t * 0.02) * 0.04

        # Phase 3 — driver inputs / dynamics / car state for the inputs
        # widget. Re-use the existing throttle/brake oscillators so the
        # inputs widget stays coherent with the engine bar's RPM output.
        i = self._frame.inputs
        i.throttle = throttle
        i.brake = brake
        i.clutch = max(0.0, min(1.0, -math.sin(t * 0.6) * 0.4))  # blip in shifts
        i.handbrake = 0.0
        # Steering follows the cornering oscillator, in -1..1 input units
        # plus a synthesized degree value (typical max wheel lock 540°/2).
        i.steering = max(-1.0, min(1.0, cornering * 0.7))
        i.steering_deg = i.steering * 270.0
        i.ffb = max(0.0, min(1.0, abs(cornering) * 0.6 + brake * 0.3))
        # G-forces from the same lateral / longitudinal model.
        i.g_lat = cornering * 1.5                 # up to ~1.5 g of lateral
        i.g_long = -brake * 1.8 + throttle * 0.6  # braking decel beats accel
        i.g_vert = 1.0 + math.sin(t * 7.0) * 0.05  # gentle suspension noise
        # Slowly accumulating damage so the chips light up over time.
        front = min(1.0, brake * 0.0005 + (t * 0.0001))
        i.damage = (front, front * 0.3, 0.0, 0.0, 0.0)
        # Tyres-out fires briefly during heavy cornering so the chip is
        # visible without the dev needing to drive off-track.
        i.tyres_out = 2 if abs(cornering) > 0.95 else 0
        i.performance_mode = "WET" if math.sin(t * 0.07) > 0.0 else "QUAL"

        for wid, w in self._frame.wheels.items():
            is_front = wid[0] == "F"
            is_left = wid[1] == "L"

            # Synthetic loads in Newtons — typical static corner load ~2500 N,
            # peaking near 5000 N under combined braking + cornering.
            load_base = 2500.0
            brake_bias = 1500.0 * brake * (1.0 if is_front else 0.4)
            corner_bias = 1250.0 * (cornering if not is_left else -cornering)
            corner_bias = max(0.0, corner_bias)
            w.tire_l = load_base + brake_bias + corner_bias + random.uniform(-100.0, 100.0)

            w.susp_t = min(w.susp_m_t, max(0.005, (w.tire_l / 10000.0) * w.susp_m_t))

            w.height = 30.0 - (w.tire_l - 2500.0) * 0.001 + math.sin(t * 3.1 + (0 if is_front else 1.0)) * 1.5

            w.camber = -0.03 + (corner_bias * 0.0008 if not is_left else -corner_bias * 0.0008)

            target_p = 26.0 + (w.tire_t_c - 80.0) * 0.04
            w.tire_p += (target_p - w.tire_p) * 0.02
            # Synthetic ideal = 26 psi; mirrors what the game's
            # tyre_normalized_pressure would publish so the pressure
            # widget colour-bands the same in mock mode.
            w.tire_p_norm = w.tire_p / 26.0

            heat_in = throttle * (1.2 if is_front else 1.0) + brake * (0.8 if is_front else 0.5)
            cool = 0.4
            w.tire_t_c += (heat_in - cool) * self._dt * 6.0
            w.tire_t_c = max(40.0, min(140.0, w.tire_t_c))
            skew = corner_bias * 0.05
            w.tire_t_i = w.tire_t_c + (skew if is_left else -skew) + random.uniform(-1.0, 1.0)
            w.tire_t_m = w.tire_t_c + random.uniform(-1.0, 1.0)
            w.tire_t_o = w.tire_t_c + (-skew if is_left else skew) + random.uniform(-1.0, 1.0)
            w.tire_t_norm_c = self._tire_curve.interpolate(w.tire_t_c)
            w.tire_t_norm_i = self._tire_curve.interpolate(w.tire_t_i)
            w.tire_t_norm_m = self._tire_curve.interpolate(w.tire_t_m)
            w.tire_t_norm_o = self._tire_curve.interpolate(w.tire_t_o)

            w.tire_d = min(4.0, w.tire_d + self._dt * 0.02)
            if random.random() < 0.0005:
                w.tire_d = min(4.0, w.tire_d + 0.5)

            w.tire_w = max(0.94, w.tire_w - self._dt * 0.0003)
            # Brake pad/disc wear: pad decays faster than disc and fronts
            # decay faster than rears, mirroring AC EVO's observed pattern.
            pad_rate = 0.0008 if is_front else 0.0004
            disc_rate = 0.0003 if is_front else 0.00015
            w.pad_w = max(0.0, w.pad_w - brake * self._dt * pad_rate)
            w.disc_w = max(0.0, w.disc_w - brake * self._dt * disc_rate)

            brake_heat_in = brake * (3.0 if is_front else 1.5)
            brake_cool = 0.4
            w.brake_t += (brake_heat_in - brake_cool) * self._dt * 30.0
            w.brake_t = max(50.0, min(900.0, w.brake_t))
            w.brake_t_norm = self._brake_curve.interpolate(w.brake_t)

            hard_brake = brake > 0.35
            slip_mock = brake > 0.5 and random.random() < 0.05
            w.lock = hard_brake and slip_mock and is_front
            w.abs_active = hard_brake and not w.lock and is_front

        self.frame.emit(self._frame)
