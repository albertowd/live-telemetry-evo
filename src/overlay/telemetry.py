from __future__ import annotations

from dataclasses import dataclass, field


WHEEL_IDS = ("FL", "FR", "RL", "RR")


@dataclass
class WheelData:
    """Per-wheel telemetry sample. Field names mirror the AC plugin so the
    porting target stays familiar; AC Evo equivalents will be wired in later."""
    abs_active: bool = False
    brake_t: float = 100.0    # brake disc temperature, C
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
    """Engine telemetry sample (RPM, turbo boost, plus the rolling maxima)."""

    max_power: float = 500.0   # HP
    max_rpm: float = 8500.0
    max_torque: float = 500.0  # Nm
    max_turbo_boost: float = 1.2
    rpm: float = 0.0
    turbo_boost: float = 0.0
    # AC1/Evo gear convention: 0=R, 1=N, 2+ = forward gears (display as N-1).
    gear: int = 1
    speed_kmh: float = 0.0
    abs_level: float = 0.0     # > 0 = ABS aid enabled
    tc_level: float = 0.0      # > 0 = traction control enabled
    pit_limiter: bool = False


@dataclass
class TelemetryFrame:
    """One full snapshot: engine state + per-wheel state for FL/FR/RL/RR."""

    engine: EngineData = field(default_factory=EngineData)
    wheels: dict[str, WheelData] = field(default_factory=lambda: {w: WheelData() for w in WHEEL_IDS})
