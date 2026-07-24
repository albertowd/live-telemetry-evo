from __future__ import annotations

from dataclasses import dataclass, field


WHEEL_IDS = ("FL", "FR", "RL", "RR")


@dataclass
class WheelData:
    """Per-wheel telemetry sample. Field names mirror the AC plugin so the
    porting target stays familiar; AC Evo equivalents will be wired in later."""
    abs_active: bool = False
    brake_t: float = 100.0    # brake disc temperature, C
    camber: float = 0.0       # radians, raw per-wheel local frame (sign meaning is wheel-side dependent on AC Evo — see ac_evo.py)
    compound: str = ""        # tyre compound name (e.g. "SOFT")
    height: float = 0.0       # mm, ride height
    lock: bool = False
    susp_t: float = 0.0       # current suspension travel (m)
    susp_m_t: float = 0.0     # max observed travel (m); 0 = uncalibrated
    # True when ``susp_m_t`` came from a rolling-max calibration rather
    # than a static-supplied limit (typically: mod cars or AC EVO, whose
    # static block dropped the field). The suspension widget colours
    # this case blue in the middle band so the user can see the bar is
    # tracking observed peaks rather than the engineered limit — and so
    # the brief "ratio==1.0" at calibration startup doesn't read as a
    # false bottoming-out red.
    susp_v: bool = False
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
    # Brake pad / disc life remaining. AC EVO publishes these via
    # ph.padLife / ph.discLife with AC1 semantics (1.0 = fresh, decreasing
    # toward 0.0 = dead); the absolute scale is unclear, so the widget
    # self-calibrates against the per-wheel max it has seen this session.
    pad_w: float = 1.0
    disc_w: float = 1.0
    # Source-capability hints. Default True (the field IS published).
    # A source that knows it never writes to one of these slots flips
    # the flag False so the widget hides the corresponding indicator
    # instead of rendering a stuck zero. ACC for example writes nothing
    # to wheelLoad or rideHeight, so its source sets both to False.
    # has_camber gates the contact-patch bars (whose height heuristic
    # is camber × pressure × load — without camber the bars over-promise
    # what they're showing). Doesn't affect the camber-driven tire
    # rotation, which renders correctly when camber is 0 (just upright).
    has_wheel_load: bool = True
    has_ride_height: bool = True
    has_camber: bool = True
    # AC EVO leaves tyreWear at 0 in current builds; AC1 has no padLife /
    # discLife in its SDK. Sources flip the matching flag false so the
    # widget hides the bar instead of rendering a stuck-full indicator.
    has_tire_wear: bool = True
    has_pad_wear: bool = True
    has_disc_wear: bool = True
    # AC1's ph.brakeTemp slot exists in the struct but the game never
    # writes to it, so it reads the initial ambient (~12 °C) all session.
    # AC1 flips this False so the widget skips the temperature label and
    # draws the brake icon in a neutral tint instead of pretending the
    # disc is permanently cold.
    has_brake_temp: bool = True
    # AC1 doesn't publish a normalised tyre pressure — sources that lack
    # it synthesise one from raw psi over a hard-coded ideal, which is
    # good enough for the pressure icon's colour bands but too brittle
    # for the contact-patch heuristic (a 30 % deviation zeros a zone).
    # Sources flip this False so the contact-patch math neutralises the
    # pressure axis and renders all three segments based on camber+load.
    has_pressure_norm: bool = True
    # Per-compound thermal performance curve (temp °C → grip fraction)
    # for this wheel, when the source can supply one (AC1 reads
    # PERFORMANCE_CURVE from tyres.ini's THERMAL_<section>). Empty =
    # no per-car curve; the wheel widget falls back to the default
    # tire-temp curve. Widget rebuilds its colour curve by list
    # identity (`is not`) so per-frame writes are cheap.
    temp_curve_pts: list[tuple[float, float]] = field(default_factory=list)
    # Per-compound ideal cold pressure (psi) from PRESSURE_IDEAL —
    # sources already fold this into tire_p_norm, so this is the raw
    # value for display / future use. 0 = unknown.
    ideal_pressure_psi: float = 0.0


@dataclass
class EngineData:
    """Engine telemetry sample (RPM, turbo boost, plus the rolling maxima)."""

    max_power: float = 500.0   # HP
    max_rpm: float = 8500.0
    max_torque: float = 500.0  # Nm
    # Default 0.0 = naturally aspirated. Sources that detect a turbo
    # set this from the static block (AC1/ACC/AC Rally) or the graphics
    # block (AC EVO); the engine widget's visibility gate (> 0.05)
    # then keeps NA cars from showing an empty black boost slot.
    max_turbo_boost: float = 0.0
    rpm: float = 0.0
    turbo_boost: float = 0.0
    # Per-car torque-vs-RPM curve from the source when available (AC1
    # parses engine.ini's POWER_CURVE .lut). Empty = no per-car curve;
    # the engine widget falls back to a default or to a self-calibrated
    # band from observed BHP. The widget rebuilds its colour curve when
    # this list reference changes (sources assign a fresh list per car
    # load), so per-frame writes are cheap.
    torque_curve_nm: list[tuple[float, float]] = field(default_factory=list)
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
    # KERS / hybrid battery state (zero on pure ICE cars). ``kers_max_j``
    # is the battery capacity in joules (AC1 publishes it, AC Evo's
    # static block dropped it — 0 = unknown, widget falls back to %).
    # ``kers_current_kj`` is the game's monotonic throughput counter
    # (ticks during both deploy and regen). ``kers_deploy_kw`` is the
    # EMA-smoothed deploy power the source derives from the throughput
    # counter while the SoC is dropping; 0 otherwise. Sources should
    # leave the whole group at default on pure ICE cars so the engine
    # widget can auto-detect hybrids by activity (charge moved /
    # throughput ticked / max_j > 0).
    kers_charge: float = 0.0       # 0..1 state of charge
    kers_max_j: float = 0.0        # battery capacity in joules (0 = unknown)
    kers_current_kj: float = 0.0   # cumulative throughput counter
    kers_input: float = 0.0        # driver deploy request 0..1
    kers_deploy_kw: float = 0.0    # smoothed deploy power, derived in source
    # AC EVO graphics block surfaces a direct hybrid-equipped flag plus
    # per-lap energy caps and battery thermals — AC1 has none of these,
    # so defaults keep its behaviour. ``has_kers`` is the strongest
    # battery-bar auto-detect signal; the lap-cap flags drive chip
    # indicators so the driver knows why deploy/charge stopped.
    has_kers: bool = False
    battery_temp_c: float = 0.0
    kers_lap_deploy_capped: bool = False
    kers_lap_charge_capped: bool = False
    # ERS strategy state — overtake mode (max-deploy) and heat charging
    # come from the electronics substruct. Map levels are -1 = unknown so
    # a future ERS-panel widget can hide cells the source can't fill.
    ers_overtake_mode: bool = False
    ers_heat_charging: bool = False
    ers_deployment_map: int = -1
    ers_recharge_map: float = -1.0


@dataclass
class InputsData:
    """Driver-input + dynamics + car-state sample (Phase 3 widget).

    Pedals are 0..1, steering is -1..1 (negative = left), g-forces are in g.
    All defaults are 0/empty so a source that doesn't publish a given
    field renders as "idle" rather than as garbage.
    """

    throttle: float = 0.0
    brake: float = 0.0
    clutch: float = 0.0
    handbrake: float = 0.0
    steering: float = 0.0          # -1..1
    steering_deg: float = 0.0      # signed degrees of wheel deflection
    ffb: float = 0.0               # 0..1; 1.0 = clipping
    g_lat: float = 0.0
    g_long: float = 0.0
    g_vert: float = 0.0
    # Five-zone body damage (0 = pristine, 1 = wreckage). AC Evo's
    # ``carDamage[5]`` ordering is front / rear / left / right / centre.
    damage: tuple[float, ...] = (0.0, 0.0, 0.0, 0.0, 0.0)
    tyres_out: int = 0             # number of tyres off-track, 0..4
    performance_mode: str = ""     # car preset name (e.g. "WET", "QUAL")


@dataclass
class TelemetryFrame:
    """One full snapshot: engine state + per-wheel state for FL/FR/RL/RR
    plus driver-input / dynamics / car-state for the inputs widget."""

    engine: EngineData = field(default_factory=EngineData)
    inputs: InputsData = field(default_factory=InputsData)
    wheels: dict[str, WheelData] = field(default_factory=lambda: {w: WheelData() for w in WHEEL_IDS})
