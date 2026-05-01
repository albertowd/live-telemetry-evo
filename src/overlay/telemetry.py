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
    compound: str = ""        # tyre compound name (e.g. "SOFT")
    height: float = 0.0       # mm, ride height
    lock: bool = False
    susp_t: float = 0.0       # current suspension travel (m)
    susp_m_t: float = 0.0     # max observed travel (m); 0 = uncalibrated
    tire_d: float = 0.0       # dirt level 0..4
    tire_l: float = 0.0       # vertical load, Newtons
    tire_p: float = 26.0      # pressure psi
    tire_p_norm: float = 1.0  # game-reported pressure / ideal-for-compound
    tire_t_c: float = 80.0    # core temperature C
    tire_t_i: float = 80.0    # inner C
    tire_t_m: float = 80.0    # middle C
    tire_t_o: float = 80.0    # outer C
    # Per-compound normalized temperatures (1.0 = ideal). When the source
    # publishes them (AC Evo's tyre_normalized_temperature_*) the colour
    # bands track the compound's real window instead of a fixed 90 C peak.
    tire_t_norm_c: float = 1.0
    tire_t_norm_i: float = 1.0
    tire_t_norm_m: float = 1.0
    tire_t_norm_o: float = 1.0
    brake_t_norm: float = 1.0  # brake disc / ideal for current pad+disc
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
    # Live values from AC Evo's graphics block (negative = unknown / not
    # available — the overlay should fall back to the synthesized power
    # curve and rpm/max_rpm ratio when these aren't filled).
    current_bhp: float = -1.0
    current_torque: float = -1.0
    rpm_percent: float = -1.0  # 0..1 fraction of redline, when known
    tc_in_action: bool = False
    abs_in_action: bool = False
    shift_up_hint: bool = False
    shift_down_hint: bool = False
    # Phase 1 driver-aid / status chips (binary). All start False so a
    # source that doesn't publish them simply leaves the chips off.
    esc_active: bool = False       # stability control engaging
    launch_active: bool = False    # launch control armed/engaging
    drs_available: bool = False    # DRS enabled in this zone
    drs_enabled: bool = False      # driver actually deployed it
    ers_charging: bool = False     # ERS / KERS / battery currently charging
    wrong_way: bool = False        # driver going against direction
    valid_lap: bool = True         # False after a cut invalidates the lap
    last_lap: bool = False         # final lap of the session
    # Phase 2 analog engine readouts (negative / zero = "not published");
    # the engine widget hides the cell when the value is non-positive.
    water_temp_c: float = 0.0
    oil_temp_c: float = 0.0
    oil_pressure_bar: float = 0.0
    fuel_pressure_bar: float = 0.0
    exhaust_temp_c: float = 0.0
    battery_voltage: float = 0.0
    fuel_liters: float = 0.0
    brake_bias: float = 0.0        # 0..1, fraction toward the front axle


@dataclass
class TelemetryFrame:
    """One full snapshot: engine state + per-wheel state for FL/FR/RL/RR."""

    engine: EngineData = field(default_factory=EngineData)
    wheels: dict[str, WheelData] = field(default_factory=lambda: {w: WheelData() for w in WHEEL_IDS})
