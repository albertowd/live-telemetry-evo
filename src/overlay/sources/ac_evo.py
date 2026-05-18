"""Assetto Corsa Evo shared-memory telemetry source.

The game publishes three named shared-memory blocks on Windows:

    Local\\acevo_pmf_physics    — updated every physics step (high-rate)
    Local\\acevo_pmf_graphics   — updated each rendered frame (HUD-rate)
    Local\\acevo_pmf_static     — written once per session

This module opens those blocks via :mod:`mmap`, parses them with
:mod:`ctypes` structs, and emits :class:`TelemetryFrame` snapshots that the
overlay widgets already understand.

NOTE ON STRUCT LAYOUT
=====================

AC Evo's full struct layout is only partially public at the time of writing.
The structs below are a best-effort starting point seeded from:

* the original AC1 ``SPageFilePhysics`` / ``SPageFileGraphic`` /
  ``SPageFileStatic`` (taken from the AC plugin SDK), which AC Evo extends;
* the public Steam guide listing the three named-mapping names and the
  embedded sub-struct names + sizes (TyreState 256 B x4, DamageState 128 B,
  …); and
* the ``acevo-shared-memory`` Rust crate, which confirms specific field
  names and types (``speedKmh: f32``, ``rpms: i32``, ``gear: i32``,
  ``fuel_liter_current_quantity: f32``, etc.).

The fields that are confirmed verbatim in the public sources are wired into
:class:`TelemetryFrame`. Anything still uncertain is read defensively (with
range clamps) so a wrong offset produces a visible-but-bounded value rather
than a crash. Once the user runs the dump tool against a live session
(``python -m overlay.sources.dump``) and confirms real offsets, this file is
the only place that needs adjusting.
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import (c_bool, c_byte, c_char, c_float, c_int8, c_int16, c_int32,
                    c_uint8, c_uint16, c_uint32, c_uint64)
from typing import Optional

from PySide6.QtCore import QObject, QTimer

from ..interpolation import Curve, DEFAULT_BRAKE_TEMP_CURVE
from ..telemetry import TelemetryFrame, WHEEL_IDS
from ._win32_mapping import NamedMapping as _NamedMapping
from .base import TelemetrySource


# Shared-memory tag names. The "Local\" namespace prefix is required on
# Windows; mmap's tagname argument accepts the bare name and prefixes Local\
# automatically, but AC Evo publishes under the explicit prefix and we match
# that to be unambiguous.
PHYSICS_TAG = "Local\\acevo_pmf_physics"
GRAPHICS_TAG = "Local\\acevo_pmf_graphics"
STATIC_TAG = "Local\\acevo_pmf_static"


# Generous upper bounds for each shared-memory segment. The actual structs
# are smaller; mapping with a slightly oversized region is harmless on
# Windows and means we don't have to know the exact size up front.
PHYSICS_SIZE = 4096
GRAPHICS_SIZE = 8192
STATIC_SIZE = 2048


class _SPageFilePhysics(ctypes.Structure):
    """AC Evo physics layout, transcribed from the official shared-memory
    documentation (Steam guide #3707421508).

    Layout matches the AC1 prefix through ``tyreTempO`` (offset 416), then
    extends with new fields: per-wheel contact geometry, brake bias, tyre
    forces/slip-ratio, in-action driver-aid flags, brake pad/disc life, and
    engine/vibration state. Total documented size is 800 bytes.

    Notable semantic differences vs AC1, per the PDF spec:
      * ``tyreWear`` is **0.0 = new, 1.0 = fully worn** (opposite of
        AC1's "0..100 % remaining"). Callers must invert when populated.
      * ``steerAngle`` is a **normalised -1..+1 ratio** (negative = left),
        not the radian value AC1 publishes.
      * ``accG`` ordering is **[lateral X, longitudinal Y, vertical Z]**.
        Gravity is subtracted from the vertical component, so accG[2]
        reads ≈0 at rest and only swings under chassis pitch / kerb hits.
      * ``physics.tc`` / ``physics.abs`` are *intervention intensity*
        (0..1 live activity). The driver-set setting level lives in
        ``graphics.electronics.tc_level`` / ``abs_level``.

    Conventions where the running build deviates from the PDF (verified
    live — apply step / source compensates):
      * ``tyreWear`` is documented but **dead-zero in current builds**.
        Source latches on the first non-zero sample and hides the wear
        bar until then.
      * ``suspensionTravel`` — PDF says compression metres (non-negative),
        but some cars publish signed displacement around a static
        reference. Source takes ``abs()``.
      * ``rideHeight`` — PDF says metres, but some cars publish mm
        directly in the same slot. Source auto-detects via
        ``|raw| >= 1.0 ⇒ already mm``.

    Graphics-block convention not stated in the PDF (verified live —
    documented here for the next reader):
      * ``SMEvoTyreState.tyre_temperature_left/right`` are car-relative
        face labels ("left" = the side of the patch facing the car's
        left), so they only line up with inner/outer for the right-side
        wheels. Left-side wheels need the L↔R values swapped before
        landing on the inner/outer slots.
    """

    _pack_ = 4
    _fields_ = [
        # AC1-compatible prefix (offsets 0..416).
        ("packetId", c_int32),
        ("gas", c_float),
        ("brake", c_float),
        ("fuel", c_float),
        ("gear", c_int32),
        ("rpms", c_int32),
        ("steerAngle", c_float),
        ("speedKmh", c_float),
        ("velocity", c_float * 3),
        ("accG", c_float * 3),
        ("wheelSlip", c_float * 4),
        ("wheelLoad", c_float * 4),
        ("wheelsPressure", c_float * 4),
        ("wheelAngularSpeed", c_float * 4),
        ("tyreWear", c_float * 4),
        ("tyreDirtyLevel", c_float * 4),
        ("tyreCoreTemperature", c_float * 4),
        # camberRAD has a per-wheel local sign convention: the setup-tool
        # "negative = top-in" convention only matches the SHM sign for
        # left-side wheels. Right-side wheels report the opposite sign
        # — verified live, e.g. setup -4° front / -3.5° rear lands as
        # FL -3.955° / FR +4.038° / RL -3.502° / RR +3.496°. Magnitudes
        # match the setup tool; only the sign on FR/RR flips. Negate
        # FR/RR if you need a uniform "negative = top-in" semantic —
        # that's all wheel_view does and it suffices for the contact-
        # patch colour bands.
        ("camberRAD", c_float * 4),
        ("suspensionTravel", c_float * 4),
        ("drs", c_float),
        ("tc", c_float),
        ("heading", c_float),
        ("pitch", c_float),
        ("roll", c_float),
        ("cgHeight", c_float),
        ("carDamage", c_float * 5),
        ("numberOfTyresOut", c_int32),
        ("pitLimiterOn", c_int32),
        ("abs", c_float),
        ("kersCharge", c_float),
        ("kersInput", c_float),
        ("autoShifterOn", c_int32),
        ("rideHeight", c_float * 2),
        ("turboBoost", c_float),
        ("ballast", c_float),
        ("airDensity", c_float),
        ("airTemp", c_float),
        ("roadTemp", c_float),
        ("localAngularVel", c_float * 3),
        ("finalFF", c_float),
        ("performanceMeter", c_float),
        ("engineBrake", c_int32),
        ("ersRecoveryLevel", c_int32),
        ("ersPowerLevel", c_int32),
        ("ersHeatCharging", c_int32),
        ("ersIsCharging", c_int32),
        ("kersCurrentKJ", c_float),
        ("drsAvailable", c_int32),
        ("drsEnabled", c_int32),
        ("brakeTemp", c_float * 4),
        ("clutch", c_float),
        ("tyreTempI", c_float * 4),
        ("tyreTempM", c_float * 4),
        ("tyreTempO", c_float * 4),
        # AC Evo additions (offset 416..800).
        ("isAIControlled", c_int32),
        ("tyreContactPoint", (c_float * 3) * 4),   # [FL,FR,RL,RR][X,Y,Z]
        ("tyreContactNormal", (c_float * 3) * 4),  # road-normal vector per wheel
        ("tyreContactHeading", (c_float * 3) * 4), # tyre heading vector per wheel
        ("brakeBias", c_float),                    # 0.56 = 56 % front
        ("localVelocity", c_float * 3),
        ("P2PActivations", c_int32),
        ("P2PStatus", c_int32),
        ("currentMaxRpm", c_int32),
        ("mz", c_float * 4),                       # self-aligning torque, Nm
        ("fx", c_float * 4),                       # longitudinal tyre force, N
        ("fy", c_float * 4),                       # lateral tyre force, N
        ("slipRatio", c_float * 4),
        ("slipAngle", c_float * 4),                # radians
        ("tcInAction", c_int32),                   # TC currently cutting
        ("absInAction", c_int32),                  # ABS currently modulating
        ("suspensionDamage", c_float * 4),         # 0..1 per corner
        ("tyreTemp", c_float * 4),                 # representative surface temp
        ("waterTemp", c_float),                    # coolant, C
        ("brakeTorque", c_float * 4),              # Nm per wheel
        ("frontBrakeCompound", c_int32),
        ("rearBrakeCompound", c_int32),
        # AC1 SDK called these padLife / discLife with 1.0 = fresh, 0.0 =
        # dead. The numeric values match the in-game pad-/disc-wear
        # readouts when scaled ×1000 (e.g. 0.029 → 29.00 in HUD), so
        # earlier we assumed AC EVO had inverted to a "wear" semantic.
        # A 4-lap A/B test proved that wrong — the values *decreased*
        # by a small consistent amount per wheel, with fronts losing
        # twice as much as rears (fronts brake harder). That's "life
        # remaining" behaviour. AC1 naming kept; semantic 1.0 = fresh,
        # decreasing toward 0.0 = dead, but the absolute scale is
        # unclear (0.029 doesn't read as "2.9 % of fresh life"; it may
        # be in some other unit the HUD multiplies by 1000 to display).
        ("padLife", c_float * 4),
        ("discLife", c_float * 4),
        ("ignitionOn", c_int32),
        ("starterEngineOn", c_int32),
        ("isEngineRunning", c_int32),
        ("kerbVibration", c_float),
        ("slipVibrations", c_float),
        ("roadVibrations", c_float),
        ("absVibrations", c_float),
    ]


class _SPageFileStatic(ctypes.Structure):
    """AC Evo SPageFileStaticEvo — session/track metadata, written once at
    session load.

    Note vs AC1: the static block is no longer car-focused. The AC1 fields
    ``maxRpm`` / ``maxPower`` / ``maxTorque`` / ``suspensionMaxTravel`` /
    ``maxTurboBoost`` are gone. Live engine values now arrive via the
    graphics block (``current_bhp``, ``rpm_percent``, ``max_turbo_boost``,
    ``max_fuel``); per-wheel suspension max travel is calibrated by
    rolling max in physics.
    """

    _pack_ = 4
    _fields_ = [
        ("sm_version", c_char * 15),
        ("ac_evo_version", c_char * 15),
        ("session", c_int32),                       # ACEVO_SESSION_TYPE enum
        ("session_name", c_char * 33),
        ("event_id", c_uint8),
        ("session_id", c_uint8),
        ("starting_grip", c_int32),                 # ACEVO_STARTING_GRIP enum
        ("starting_ambient_temperature_c", c_float),
        ("starting_ground_temperature_c", c_float),
        ("is_static_weather", c_bool),
        ("is_timed_race", c_bool),
        ("is_online", c_bool),
        ("number_of_sessions", c_int32),
        ("nation", c_char * 33),
        ("longitude", c_float),
        ("latitude", c_float),
        ("track", c_char * 33),
        ("track_configuration", c_char * 33),
        ("track_length_m", c_float),
    ]


# Order of the wheel arrays in AC1 / AC Evo: 0=FL, 1=FR, 2=RL, 3=RR.
_WHEEL_INDEX = {"FL": 0, "FR": 1, "RL": 2, "RR": 3}


# --- AC Evo graphics-block substructs ---------------------------------------
#
# Each substruct here mirrors the official ACE_SharedFileOut_Documentation_v1
# layout (Kunos, 2026-04-01). Field names follow the docs verbatim *except*
# for the two known Kunos typos, which we silently correct:
#   * SMEvoTyreState.tyre_pression   -> tyre_pressure
#   * SPageFileGraphicEvo.car_ffb_mupliplier -> car_ffb_multiplier
# Layout is unchanged — same offsets, same byte sizes.
#
# Each substruct pads itself out to the documented byte size with a trailing
# `_reserved` blob so the outer SPageFileGraphicEvo offsets match the docs
# exactly. Sizeof assertions at module load catch any field-order drift.


class _SMEvoTyreState(ctypes.Structure):
    """SMEvoTyreState [256 B] — per-corner tyre snapshot. Embedded four
    times in SPageFileGraphicEvo (lf, rf, lr, rr)."""

    _pack_ = 4
    _fields_ = [
        ("slip", c_float),
        ("lock", c_bool),
        ("tyre_pressure", c_float),                # docs typo: tyre_pression
        ("tyre_temperature_c", c_float),
        ("brake_temperature_c", c_float),
        ("brake_pressure", c_float),
        ("tyre_temperature_left", c_float),
        ("tyre_temperature_center", c_float),
        ("tyre_temperature_right", c_float),
        ("tyre_compound_front", c_char * 33),
        ("tyre_compound_rear", c_char * 33),
        ("tyre_normalized_pressure", c_float),
        ("tyre_normalized_temperature_left", c_float),
        ("tyre_normalized_temperature_center", c_float),
        ("tyre_normalized_temperature_right", c_float),
        ("brake_normalized_temperature", c_float),
        ("tyre_normalized_temperature_core", c_float),
        ("_reserved", c_byte * 128),
    ]
assert ctypes.sizeof(_SMEvoTyreState) == 256, ctypes.sizeof(_SMEvoTyreState)


class _SMEvoDamageState(ctypes.Structure):
    """SMEvoDamageState [128 B] — body and per-corner suspension damage
    levels (0.0 = undamaged, 1.0 = destroyed)."""

    _pack_ = 4
    _fields_ = [
        ("damage_front", c_float),
        ("damage_rear", c_float),
        ("damage_left", c_float),
        ("damage_right", c_float),
        ("damage_center", c_float),
        ("damage_suspension_lf", c_float),
        ("damage_suspension_rf", c_float),
        ("damage_suspension_lr", c_float),
        ("damage_suspension_rr", c_float),
        ("_reserved", c_byte * 92),
    ]
assert ctypes.sizeof(_SMEvoDamageState) == 128, ctypes.sizeof(_SMEvoDamageState)


class _SMEvoPitInfo(ctypes.Structure):
    """SMEvoPitInfo [64 B] — pit-stop service action status per item.
    −1 = will not perform, 0 = completed, 1 = in progress."""

    _pack_ = 4
    _fields_ = [
        ("damage", c_int8),
        ("fuel", c_int8),
        ("tyres_lf", c_int8),
        ("tyres_rf", c_int8),
        ("tyres_lr", c_int8),
        ("tyres_rr", c_int8),
        ("_reserved", c_byte * 58),
    ]
assert ctypes.sizeof(_SMEvoPitInfo) == 64, ctypes.sizeof(_SMEvoPitInfo)


class _SMEvoElectronics(ctypes.Structure):
    """SMEvoElectronics [128 B] — driver-adjustable electronic aid and
    setup settings. Embedded four times in SPageFileGraphicEvo (current,
    min_limit, max_limit, is_modifiable)."""

    _pack_ = 4
    _fields_ = [
        ("tc_level", c_int8),
        ("tc_cut_level", c_int8),
        ("abs_level", c_int8),
        ("esc_level", c_int8),
        ("ebb_level", c_int8),
        ("brake_bias", c_float),                   # auto-padded to offset 8
        ("engine_map_level", c_int8),
        ("turbo_level", c_float),                  # auto-padded to offset 16
        ("ers_deployment_map", c_int8),
        ("ers_recharge_map", c_float),             # auto-padded to offset 24
        ("is_ers_heat_charging_on", c_bool),
        ("is_ers_overtake_mode_on", c_bool),
        ("is_drs_open", c_bool),
        ("diff_power_level", c_int8),
        ("diff_coast_level", c_int8),
        ("front_bump_damper_level", c_int8),
        ("front_rebound_damper_level", c_int8),
        ("rear_bump_damper_level", c_int8),
        ("rear_rebound_damper_level", c_int8),
        ("is_ignition_on", c_bool),
        ("is_pitlimiter_on", c_bool),
        ("active_performance_mode", c_int8),
        ("_reserved", c_byte * 88),
    ]
assert ctypes.sizeof(_SMEvoElectronics) == 128, ctypes.sizeof(_SMEvoElectronics)


class _SMEvoInstrumentation(ctypes.Structure):
    """SMEvoInstrumentation [128 B] — cockpit light, display, and panel
    states. Embedded three times in SPageFileGraphicEvo (current,
    min_limit, max_limit). display_current_page_index grew from 9 to 16
    on 2026-03-31."""

    _pack_ = 4
    _fields_ = [
        ("main_light_stage", c_int8),
        ("special_light_stage", c_int8),
        ("cockpit_light_stage", c_int8),
        ("wiper_level", c_int8),
        ("rain_lights", c_bool),
        ("direction_light_left", c_bool),
        ("direction_light_right", c_bool),
        ("flashing_lights", c_bool),
        ("warning_lights", c_bool),
        ("selected_display_index", c_int8),
        ("display_current_page_index", c_int8 * 16),
        ("are_headlights_visible", c_bool),
        ("_reserved", c_byte * 101),
    ]
assert ctypes.sizeof(_SMEvoInstrumentation) == 128, ctypes.sizeof(_SMEvoInstrumentation)


class _SMEvoSessionState(ctypes.Structure):
    """SMEvoSessionState [256 B] — server-side session lifecycle info."""

    _pack_ = 4
    _fields_ = [
        ("phase_name", c_char * 33),
        ("time_left", c_char * 15),
        ("time_left_ms", c_int32),
        ("wait_time", c_char * 15),
        ("total_lap", c_int32),                    # auto-padded
        ("current_lap", c_int32),
        ("lights_on", c_int32),
        ("lights_mode", c_int32),
        ("lap_length_km", c_float),
        ("end_session_flag", c_int32),
        ("time_to_next_session", c_char * 15),
        ("disconnected_from_server", c_bool),
        ("restart_season_enabled", c_bool),
        ("ui_enable_drive", c_bool),
        ("ui_enable_setup", c_bool),
        ("is_ready_to_next_blinking", c_bool),
        ("show_waiting_for_players", c_bool),
        ("_reserved", c_byte * 143),
    ]
assert ctypes.sizeof(_SMEvoSessionState) == 256, ctypes.sizeof(_SMEvoSessionState)


class _SMEvoTimingState(ctypes.Structure):
    """SMEvoTimingState [256 B] — lap timing and delta values displayed
    on the HUD. *_p fields are sign markers: +1 slower, −1 faster, 0
    hidden."""

    _pack_ = 4
    _fields_ = [
        ("current_laptime", c_char * 15),
        ("delta_current", c_char * 15),
        ("delta_current_p", c_int32),              # auto-padded
        ("last_laptime", c_char * 15),
        ("delta_last", c_char * 15),
        ("delta_last_p", c_int32),                 # auto-padded
        ("best_laptime", c_char * 15),
        ("ideal_laptime", c_char * 15),
        ("total_time", c_char * 15),
        ("is_invalid", c_bool),
        ("_reserved", c_byte * 138),
    ]
assert ctypes.sizeof(_SMEvoTimingState) == 256, ctypes.sizeof(_SMEvoTimingState)


class _SMEvoAssistsState(ctypes.Structure):
    """SMEvoAssistsState [64 B] — driver-assist settings currently active
    for the player car."""

    _pack_ = 4
    _fields_ = [
        ("auto_gear", c_uint8),
        ("auto_blip", c_uint8),
        ("auto_clutch", c_uint8),
        ("auto_clutch_on_start", c_uint8),
        ("manual_ignition_e_start", c_uint8),
        ("auto_pit_limiter", c_uint8),
        ("standing_start_assist", c_uint8),
        ("auto_steer", c_float),                   # auto-padded
        ("arcade_stability_control", c_float),
        ("_reserved", c_byte * 48),
    ]
assert ctypes.sizeof(_SMEvoAssistsState) == 64, ctypes.sizeof(_SMEvoAssistsState)


class _SPageFileGraphic(ctypes.Structure):
    """AC Evo SPageFileGraphicEvo — HUD/graphics data, transcribed from the
    official ACE_SharedFileOut_Documentation_v1 (Kunos, 2026-04-01).

    All documented substructs are fully decoded (TyreState, DamageState,
    PitInfo, Electronics, Instrumentation, SessionState, TimingState,
    AssistsState). Each is padded to its documented byte size so the
    outer offsets line up exactly with what the game writes.

    Kunos typos in the official docs are corrected here:
      * car_ffb_mupliplier -> car_ffb_multiplier
    Layout is unchanged — same offsets, same field width.
    """

    _pack_ = 4
    _fields_ = [
        ("packetId", c_int32),
        ("status", c_int32),                # ACEVO_STATUS enum
        ("focused_car_id_a", c_uint64),
        ("focused_car_id_b", c_uint64),
        ("player_car_id_a", c_uint64),
        ("player_car_id_b", c_uint64),
        ("rpm", c_uint16),
        ("is_rpm_limiter_on", c_bool),
        ("is_change_up_rpm", c_bool),
        ("is_change_down_rpm", c_bool),
        ("tc_active", c_bool),
        ("abs_active", c_bool),
        ("esc_active", c_bool),
        ("launch_active", c_bool),
        ("is_ignition_on", c_bool),
        ("is_engine_running", c_bool),
        ("kers_is_charging", c_bool),
        ("is_wrong_way", c_bool),
        ("is_drs_available", c_bool),
        ("battery_is_charging", c_bool),
        ("is_max_kj_per_lap_reached", c_bool),
        ("is_max_charge_kj_per_lap_reached", c_bool),
        ("display_speed_kmh", c_int16),
        ("display_speed_mph", c_int16),
        ("display_speed_ms", c_int16),
        ("pitspeeding_delta", c_float),
        ("gear_int", c_int16),
        ("rpm_percent", c_float),
        ("gas_percent", c_float),
        ("brake_percent", c_float),
        ("handbrake_percent", c_float),
        ("clutch_percent", c_float),
        ("steering_percent", c_float),
        ("ffb_strength", c_float),
        ("car_ffb_multiplier", c_float),
        ("water_temperature_percent", c_float),
        ("water_pressure_bar", c_float),
        ("fuel_pressure_bar", c_float),
        ("water_temperature_c", c_int8),
        ("air_temperature_c", c_int8),
        ("oil_temperature_c", c_float),
        ("oil_pressure_bar", c_float),
        ("exhaust_temperature_c", c_float),
        ("g_forces_x", c_float),
        ("g_forces_y", c_float),
        ("g_forces_z", c_float),
        ("turbo_boost", c_float),
        ("turbo_boost_level", c_float),
        ("turbo_boost_perc", c_float),
        ("steer_degrees", c_int32),
        ("current_km", c_float),
        ("total_km", c_uint32),
        ("total_driving_time_s", c_uint32),
        ("time_of_day_hours", c_int32),
        ("time_of_day_minutes", c_int32),
        ("time_of_day_seconds", c_int32),
        ("delta_time_ms", c_int32),
        ("current_lap_time_ms", c_int32),
        ("predicted_lap_time_ms", c_int32),
        ("fuel_liter_current_quantity", c_float),
        ("fuel_liter_current_quantity_percent", c_float),
        ("fuel_liter_per_km", c_float),
        ("km_per_fuel_liter", c_float),
        ("current_torque", c_float),                  # Nm, live
        ("current_bhp", c_int32),                     # BHP, live
        ("tyre_lf", _SMEvoTyreState),
        ("tyre_rf", _SMEvoTyreState),
        ("tyre_lr", _SMEvoTyreState),
        ("tyre_rr", _SMEvoTyreState),
        ("npos", c_float),
        ("kers_charge_perc", c_float),
        ("kers_current_perc", c_float),
        ("control_lock_time", c_float),
        ("car_damage", _SMEvoDamageState),
        ("car_location", c_int32),                    # ACEVO_CAR_LOCATION enum
        ("pit_info", _SMEvoPitInfo),
        ("fuel_liter_used", c_float),
        ("fuel_liter_per_lap", c_float),
        ("laps_possible_with_fuel", c_float),
        ("battery_temperature", c_float),
        ("battery_voltage", c_float),
        ("instantaneous_fuel_liter_per_km", c_float),
        ("instantaneous_km_per_fuel_liter", c_float),
        ("gear_rpm_window", c_float),
        ("instrumentation", _SMEvoInstrumentation),
        ("instrumentation_min_limit", _SMEvoInstrumentation),
        ("instrumentation_max_limit", _SMEvoInstrumentation),
        ("electronics", _SMEvoElectronics),
        ("electronics_min_limit", _SMEvoElectronics),
        ("electronics_max_limit", _SMEvoElectronics),
        ("electronics_is_modifiable", _SMEvoElectronics),
        ("total_lap_count", c_int32),
        ("current_pos", c_uint32),
        ("total_drivers", c_uint32),
        ("last_laptime_ms", c_int32),
        ("best_laptime_ms", c_int32),
        ("flag", c_int32),                            # ACEVO_FLAG_TYPE enum
        ("global_flag", c_int32),
        ("max_gears", c_uint32),
        ("engine_type", c_int32),                     # ACEVO_ENGINE_TYPE enum
        ("has_kers", c_bool),
        ("is_last_lap", c_bool),
        ("performance_mode_name", c_char * 33),
        ("diff_coast_raw_value", c_float),
        ("diff_power_raw_value", c_float),
        ("race_cut_gained_time_ms", c_int32),
        ("distance_to_deadline", c_int32),
        ("race_cut_current_delta", c_float),
        ("session_state", _SMEvoSessionState),
        ("timing_state", _SMEvoTimingState),
        ("player_ping", c_int32),
        ("player_latency", c_int32),
        ("player_cpu_usage", c_int32),
        ("player_cpu_usage_avg", c_int32),
        ("player_qos", c_int32),
        ("player_qos_avg", c_int32),
        ("player_fps", c_int32),
        ("player_fps_avg", c_int32),
        ("driver_name", c_char * 33),
        ("driver_surname", c_char * 33),
        ("car_model", c_char * 33),
        ("is_in_pit_box", c_bool),
        ("is_in_pit_lane", c_bool),
        ("is_valid_lap", c_bool),
        ("car_coordinates", (c_float * 3) * 60),       # XYZ for up to 60 cars
        ("gap_ahead", c_float),
        ("gap_behind", c_float),
        ("active_cars", c_uint8),
        ("fuel_per_lap", c_float),
        ("fuel_estimated_laps", c_float),
        ("assists_state", _SMEvoAssistsState),
        ("max_fuel", c_float),
        ("max_turbo_boost", c_float),
        ("use_single_compound", c_bool),
        # Added 2026-04-01: maps each car_coordinates[i] slot to the UID
        # of the driver occupying it ([:][0] = lower 64, [:][1] = upper 64
        # of the 128-bit driver UID). 0/0 = empty slot.
        ("car_ids", (c_uint64 * 2) * 60),
    ]


class AcEvoSharedMemoryReader:
    """Opens the three AC Evo shared-memory segments and exposes typed views.

    Designed to be safe to construct even when the game is not running:
    failures during ``open()`` raise :class:`OSError` and the source treats
    them as "not connected yet" — the overlay then keeps showing the last
    frame (or the synthetic fallback if configured).
    """

    def __init__(self) -> None:
        self._physics_mm: Optional[_NamedMapping] = None
        self._graphics_mm: Optional[_NamedMapping] = None
        self._static_mm: Optional[_NamedMapping] = None

    def open(self) -> None:
        """Attach to the three named shared-memory blocks.

        Raises :class:`FileNotFoundError` when the game is not running, so
        the caller can distinguish "not connected yet" from real errors.
        """
        self._physics_mm = _NamedMapping(PHYSICS_TAG, PHYSICS_SIZE)
        try:
            self._graphics_mm = _NamedMapping(GRAPHICS_TAG, GRAPHICS_SIZE)
            self._static_mm = _NamedMapping(STATIC_TAG, STATIC_SIZE)
        except OSError:
            self.close()
            raise

    def close(self) -> None:
        for mm in (self._physics_mm, self._graphics_mm, self._static_mm):
            if mm is not None:
                mm.close()
        self._physics_mm = self._graphics_mm = self._static_mm = None

    @property
    def is_open(self) -> bool:
        return self._physics_mm is not None

    def read_physics(self) -> _SPageFilePhysics:
        if self._physics_mm is None:
            raise RuntimeError("reader is not open")
        return _SPageFilePhysics.from_buffer_copy(self._physics_mm.read(), 0)

    def read_static(self) -> _SPageFileStatic:
        if self._static_mm is None:
            raise RuntimeError("reader is not open")
        return _SPageFileStatic.from_buffer_copy(self._static_mm.read(), 0)

    def read_graphics(self) -> _SPageFileGraphic:
        if self._graphics_mm is None:
            raise RuntimeError("reader is not open")
        return _SPageFileGraphic.from_buffer_copy(self._graphics_mm.read(), 0)

    def read_raw(self, segment: str) -> bytes:
        """Return the raw bytes of one segment for inspection / debugging."""
        mm = {"physics": self._physics_mm,
              "graphics": self._graphics_mm,
              "static": self._static_mm}.get(segment)
        if mm is None:
            raise ValueError(f"unknown or unopened segment: {segment!r}")
        return mm.read()


class AcEvoTelemetrySource(TelemetrySource):
    """Polls AC Evo shared memory and emits :class:`TelemetryFrame`.

    Connection is best-effort: if the game isn't running, ``start()`` keeps
    polling silently and connects as soon as the SHM blocks appear. The
    overlay widgets keep showing whatever they last saw in the meantime.
    """

    def __init__(self, hz: int = 60, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._reader = AcEvoSharedMemoryReader()
        self._frame = TelemetryFrame()
        # Curve fallback for brake_t_norm. AC EVO publishes
        # `brake_normalized_temperature` only intermittently — it's 0.0
        # for many cars / states, especially early in a session — and a
        # 0.0 read leaves brake_t_norm at its default 1.0 (which the
        # widget reads as "ideal", i.e. green). We interpolate the same
        # curve the synthetic source uses so the icon reads correctly
        # whenever the live norm is unavailable.
        self._brake_curve = Curve(DEFAULT_BRAKE_TEMP_CURVE)
        # PDF documents ``physics.tyreWear`` but current AC EVO builds
        # still leave the slot dead at 0.0 all session (verified live).
        # Latch True the first time any corner publishes a non-zero so
        # we surface the bar automatically when a future build wires it
        # up — without keeping it permanently at "100 % remaining" today.
        # Tyre wear is monotonic, so a one-way latch is safe.
        self._tire_wear_live = False
        # State for hybrid-deploy-power derivation (see
        # _update_kers_deploy). AC Evo publishes ``current_bhp`` from
        # the game so the widget doesn't need this for HP, but the
        # battery bar consumes it for the "deploying" colour cue.
        self._prev_kers_current_kj: Optional[float] = None
        self._last_kers_tick: Optional[float] = None
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / hz))
        # pylint: disable-next=no-member  # QTimer.timeout is a PySide6 Signal
        self._timer.timeout.connect(self._tick)
        self._reconnect_countdown = 0

    def start(self) -> None:
        self._try_connect()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._reader.close()

    def set_hz(self, hz: int) -> None:
        self._timer.setInterval(int(1000 / max(1, int(hz))))

    def _try_connect(self) -> bool:
        if self._reader.is_open:
            return True
        try:
            self._reader.open()
            self._apply_static(self._reader.read_static())
            print("[ac-evo] connected to shared memory")
            return True
        except (OSError, RuntimeError) as exc:
            # File-not-found is the common "game not running" case; surface
            # other errors so misconfiguration is debuggable.
            if not isinstance(exc, FileNotFoundError):
                print(f"[ac-evo] connect failed: {exc}", file=sys.stderr)
            return False

    def _tick(self) -> None:
        if not self._reader.is_open:
            # Throttle reconnect attempts to once a second to avoid spamming
            # logs when the game is closed.
            self._reconnect_countdown -= 1
            if self._reconnect_countdown <= 0:
                self._reconnect_countdown = 60
                if not self._try_connect():
                    return
            else:
                return

        try:
            phys = self._reader.read_physics()
            graphics = self._reader.read_graphics()
        except (OSError, ValueError) as exc:
            print(f"[ac-evo] read failed, dropping connection: {exc}", file=sys.stderr)
            self._reader.close()
            return

        # Graphics first so physics can read the per-wheel lock flag it
        # sets (abs_active gates on `not w.lock`). Hybrid deploy power
        # depends on both blocks (charge from graphics, throughput
        # counter from physics) so it runs after both apply steps.
        self._apply_graphics(graphics)
        self._apply_physics(phys)
        self._update_kers_deploy(self._frame.engine)
        if self._bus is not None:
            self._bus.publish(self._frame)
        self.frame.emit(self._frame)

    def _apply_static(self, st: _SPageFileStatic) -> None:
        # AC Evo's static block is session/track metadata only — the AC1
        # car-spec fields (maxRpm/Power/Torque, suspensionMaxTravel) are
        # gone. The overlay sources its peaks from graphics (current_bhp,
        # rpm_percent, max_turbo_boost) and from rolling-max calibration in
        # physics, so there is nothing to apply here yet. Track name /
        # ambient temperature could feed future widgets.
        del st

    def _update_kers_deploy(self, e) -> None:
        """Derive ``kers_deploy_kw`` from the throughput-counter delta,
        gated by the documented ``ersIsCharging`` flag (AC1 had to infer
        the directional signal from a SoC drop because the flag wasn't
        published — see ac1.py for the long-form rationale).

        The counter is monotonic and ticks during both charge and deploy,
        so the flag is the only thing separating the two — when
        ``e.ers_charging`` is false and the counter ticks, it must be
        deploy energy.

        Using the explicit flag also fixes the SoC-drop heuristic's blind
        spots: it failed when SoC was pinned at 0/1 and was fragile under
        single-precision quantisation jitter."""
        now = time.monotonic()
        if (self._last_kers_tick is None
                or self._prev_kers_current_kj is None):
            raw_kw = 0.0
        else:
            delta_t = now - self._last_kers_tick
            if delta_t > 0.0 and not e.ers_charging:
                raw_kw = max(0.0,
                             (e.kers_current_kj - self._prev_kers_current_kj)
                             / delta_t)
            else:
                raw_kw = 0.0
        e.kers_deploy_kw = 0.7 * e.kers_deploy_kw + 0.3 * raw_kw
        self._prev_kers_current_kj = e.kers_current_kj
        self._last_kers_tick = now

    def _apply_physics(self, ph: _SPageFilePhysics) -> None:
        e = self._frame.engine
        e.rpm = float(ph.rpms)
        e.turbo_boost = float(ph.turboBoost)
        # A rolling observed max keeps the boost bar usable even when the
        # static maxTurboBoost is missing or wrong (some mods report 0).
        e.max_turbo_boost = max(e.max_turbo_boost, e.turbo_boost)
        e.gear = int(ph.gear)
        e.speed_kmh = float(ph.speedKmh)
        # AC Evo dropped the static maxRpm field; the per-tick currentMaxRpm
        # is the canonical replacement. Populating e.max_rpm makes the
        # rpm-bar fallback (rpm/max_rpm) accurate per car instead of pinned
        # to the generic 8500 default.
        if ph.currentMaxRpm > 0:
            e.max_rpm = float(ph.currentMaxRpm)
        # Per PDF, physics.tc / physics.abs are *intervention intensity*
        # (0..1, live activity), not the driver-set setting level. The
        # setting level lives in graphics.electronics.{tc,abs}_level and
        # is what the engine widget's "is the aid enabled?" check wants;
        # populated in _apply_graphics. The active-cut blink is keyed off
        # tc_in_action / abs_in_action below.
        e.pit_limiter = bool(ph.pitLimiterOn)
        # "Currently cutting" — merged from every signal the game
        # publishes:
        #   * physics.tcInAction / absInAction — documented int flags
        #   * physics.tc / abs — intervention intensity (0..1 float)
        #   * graphics.tc_active / abs_active — HUD-level bool flags
        #     (set in _apply_graphics; we OR them in here)
        # Some car/build combos leave the physics ints permanently 0
        # even with ABS actively modulating — verified live, this is
        # why the wheel-widget brake disk wasn't blinking blue. The
        # float intensity and the graphics bool both fire reliably in
        # those cases, so OR-ing all three keeps the cue responsive.
        e.tc_in_action = (e.tc_in_action
                          or ph.tcInAction != 0
                          or float(ph.tc) > 0.0)
        e.abs_in_action = (e.abs_in_action
                           or ph.absInAction != 0
                           or float(ph.abs) > 0.0)
        # Driver-set values that live in physics rather than graphics.
        e.brake_bias = float(ph.brakeBias)
        # AC Evo's `drs` is 0..1 deploy state; treat anything > 0.5 as
        # "deployed" so the chip's bright-vs-dim differentiation matches
        # what the driver is actually doing.
        e.drs_enabled = bool(ph.drsEnabled) or float(ph.drs) > 0.5

        # Hybrid telemetry — raw physics fields here; kers_charge as a
        # 0..1 fraction comes from graphics.kers_charge_perc in
        # _apply_graphics (the physics ``kersCharge`` is also 0..1 but
        # graphics has the value the game's own HUD uses). Static block
        # doesn't carry kersMaxJ on AC Evo, so the widget falls back to
        # the % readout for cars with a battery.
        e.kers_current_kj = float(ph.kersCurrentKJ)
        e.kers_input = float(ph.kersInput)
        # ersIsCharging is the documented directional flag (0 = deploying,
        # 1 = charging). OR with the graphics-side flags already set in
        # _apply_graphics so any source of "charging now" wins — physics
        # ticks faster than graphics, graphics covers cars where the
        # physics flag isn't wired. _update_kers_deploy reads the merged
        # value as the deploy gate.
        e.ers_charging = e.ers_charging or bool(ph.ersIsCharging)

        # Phase 3 — driver inputs / dynamics / car state. Pedals come in
        # as 0..1 from physics; the graphics block also publishes _percent
        # variants but physics fires every tick so we prefer it here.
        i = self._frame.inputs
        i.throttle = max(0.0, min(1.0, float(ph.gas)))
        i.brake = max(0.0, min(1.0, float(ph.brake)))
        i.clutch = max(0.0, min(1.0, float(ph.clutch)))
        # Per PDF, ph.steerAngle is already a normalised -1..+1 input
        # ratio (negative = left). The signed-degree readout is populated
        # from graphics.steer_degrees in _apply_graphics.
        i.steering = max(-1.0, min(1.0, float(ph.steerAngle)))
        i.ffb = max(0.0, min(1.0, abs(float(ph.finalFF))))
        # Per PDF, accG layout is [lateral X, longitudinal Y, vertical Z].
        # AC Evo subtracts gravity from accG[2], so vertical reads ≈0 at
        # rest and only swings under chassis pitch / kerb hits.
        i.g_lat = float(ph.accG[0])
        i.g_long = float(ph.accG[1])
        i.g_vert = float(ph.accG[2])
        i.damage = tuple(float(ph.carDamage[k]) for k in range(5))
        i.tyres_out = int(ph.numberOfTyresOut)

        braking = ph.brake > 0.0
        # Use the merged in-action flag (graphics bool OR physics int OR
        # physics intensity) rather than the raw int alone — some cars
        # leave absInAction dead but still fire the other two signals.
        abs_modulating = e.abs_in_action
        # Latch the tyre-wear-is-live flag once any corner publishes a
        # non-zero. Current builds leave tyreWear dead; this surfaces
        # the bar automatically when a future build starts populating it.
        if not self._tire_wear_live and any(ph.tyreWear[k] > 0.0 for k in range(4)):
            self._tire_wear_live = True

        for wid in WHEEL_IDS:
            idx = _WHEEL_INDEX[wid]
            w = self._frame.wheels[wid]

            # Per-wheel slip — prefer EVO's documented slipRatio (signed
            # longitudinal slip ratio) over the legacy AC1 ``wheelSlip``.
            # Take the max magnitude of both so the heuristic still works
            # on cars where one field is dead.
            slip = max(abs(float(ph.slipRatio[idx])),
                       abs(float(ph.wheelSlip[idx])))
            # ABS active on this wheel = system is modulating + this wheel
            # has measurable slip. The previous 0.10 threshold meant the
            # cue only lit up *after* ABS lost the wheel — by design ABS
            # keeps slip right around the ideal ~10 %, so a high threshold
            # never fires during normal modulation. 0.03 catches the cue
            # while the system is actively controlling. Per-wheel ``lock``
            # is sourced from the graphics tyre states in _apply_graphics.
            w.abs_active = abs_modulating and braking and not w.lock and slip > 0.03

            w.camber = float(ph.camberRAD[idx])
            # PDF says suspensionTravel is "compression in metres" (i.e.
            # non-negative from full extension). Most cars conform, but
            # some chassis (cars with active / electronically managed
            # suspension are a known case) publish a signed displacement
            # around a static reference instead — verified live: values
            # cluster around −0.03 at rest and swing a couple of cm on
            # kerbs. ``abs()`` collapses both conventions to a magnitude
            # so the rolling-max heuristic below calibrates either way.
            w.susp_t = abs(float(ph.suspensionTravel[idx]))
            # AC Evo no longer publishes the per-car suspensionMaxTravel
            # in its static block, so we're always in dynamic mode —
            # plain rolling max from observed travel, flag ``susp_v``
            # so the widget paints the middle band blue (calibrating)
            # rather than white. Same convention as the AC1 source's
            # "no static available" branch; see ac1.py for rationale.
            if w.susp_t > w.susp_m_t:
                w.susp_m_t = w.susp_t
            w.susp_v = True

            # Ride height per axle (rideHeight[0]=front, [1]=rear).
            # PDF documents this as metres, but some chassis publish mm
            # directly in the same slot — verified live. No physical car
            # has ≥ 1 m of ride height, so |raw| >= 1.0 is a clean "this
            # is already mm" tell. WheelData.height is mm regardless.
            axle = idx // 2
            raw = float(ph.rideHeight[axle])
            height_mm = raw if abs(raw) >= 1.0 else raw * 1000.0
            # Body-roll correction — same logic as the AC1 source and
            # the LiveTelemetry plugin. Splits the per-axle rideHeight
            # into per-wheel values using the relative suspension
            # travel across the axle. Raw signed travel (not w.susp_t)
            # so the diff stays correct on active-suspension cars that
            # publish signed displacement around a non-zero rest.
            opposite_idx = idx ^ 1  # FL↔FR (0↔1), RL↔RR (2↔3)
            susp_diff = (float(ph.suspensionTravel[idx])
                         - float(ph.suspensionTravel[opposite_idx]))
            # Clamp at 0: heavy body roll can push the corrected value
            # below the axle midpoint, but a negative ride height isn't
            # physical. Floor it so the widget never reads negative.
            w.height = max(0.0, height_mm - (susp_diff / 2.0) * 1000.0)

            w.tire_d = float(ph.tyreDirtyLevel[idx]) * 4.0
            w.tire_l = float(ph.wheelLoad[idx])  # Newtons
            w.tire_p = float(ph.wheelsPressure[idx])
            w.tire_t_c = float(ph.tyreCoreTemperature[idx])
            # Per-face I/M/O temperatures live in the graphics-block
            # TyreState (tyre_temperature_left/center/right) — the legacy
            # AC1 ph.tyreTempI/M/O slots read 0.0 on AC EVO, so populating
            # tire_t_i/m/o here would just clobber the graphics values.
            w.brake_t = float(ph.brakeTemp[idx])
            w.pad_w = float(ph.padLife[idx])
            w.disc_w = float(ph.discLife[idx])
            # ph.tyreWear semantic per official docs: 0.0 = new, 1.0 =
            # fully worn. WheelData.tire_w uses the opposite convention
            # (1.0 = new) — invert. Current builds leave the slot dead
            # at 0.0, so we hide the bar until the latch flips.
            if self._tire_wear_live:
                w.tire_w = max(0.0, 1.0 - float(ph.tyreWear[idx]))
                w.has_tire_wear = True
            else:
                w.has_tire_wear = False

    def _apply_graphics(self, gr: _SPageFileGraphic) -> None:
        """Pull HUD/graphics fields that complement (and in places replace)
        the synthesized values fed from physics+static.

        The big wins here are live engine output (``current_bhp``,
        ``current_torque``) and the documented car-spec maxima
        (``max_turbo_boost``, ``max_fuel``) — AC Evo's static block doesn't
        carry car specs anymore, only session/track metadata.
        """
        e = self._frame.engine
        # Live engine output — far better than interpolating a fictional
        # torque curve. We treat it as the *boosted* HP at the current RPM
        # so engine_view can use it directly without the ``(1+boost)`` hack.
        e.current_bhp = float(gr.current_bhp)
        e.current_torque = float(gr.current_torque)
        # max_turbo_boost from the graphics block trumps the rolling-max
        # heuristic in physics; falls back to the heuristic when the value
        # is missing (mods, AI cars, etc.).
        if gr.max_turbo_boost > 0.0:
            e.max_turbo_boost = float(gr.max_turbo_boost)
        # rpm_percent (0..1) is RPM as a fraction of redline — lets the bar
        # fill correctly even when we never learn the absolute redline.
        rpm_percent = float(gr.rpm_percent)
        if rpm_percent > 0.0:
            e.rpm_percent = rpm_percent
        e.shift_up_hint = bool(gr.is_change_up_rpm)
        e.shift_down_hint = bool(gr.is_change_down_rpm)

        # Phase 1 — driver-aid / status chips (binary).
        e.esc_active = bool(gr.esc_active)
        e.launch_active = bool(gr.launch_active)
        e.drs_available = bool(gr.is_drs_available)
        # tc_active / abs_active are the HUD-level "actively intervening
        # this frame" bools. Seed the in-action flags here; _apply_physics
        # ORs in the physics-side signals (ints + intensity floats) after.
        e.tc_in_action = bool(gr.tc_active)
        e.abs_in_action = bool(gr.abs_active)
        # Either the discrete KERS-charging flag or the broader battery
        # one is enough to light the ERS chip — different engine types
        # populate different fields.
        e.ers_charging = bool(gr.kers_is_charging) or bool(gr.battery_is_charging)
        # SoC from the graphics block (0..1 fraction the game's own HUD
        # uses). Static block has no kersMaxJ on AC Evo, so the widget
        # auto-falls-back to a %-of-charge readout.
        e.kers_charge = float(gr.kers_charge_perc)
        # Direct hybrid-equipped flag — preferred over the engine widget's
        # activity-based auto-detect, since it's correct even before the
        # battery has been touched.
        e.has_kers = bool(gr.has_kers)
        e.battery_temp_c = float(gr.battery_temperature)
        # Per-lap energy caps — set when the regulation budget for this
        # lap is exhausted (deploy) or saturated (charge). Drives the
        # KMAX/CMAX chips so the driver knows why deploy/charge stopped.
        e.kers_lap_deploy_capped = bool(gr.is_max_kj_per_lap_reached)
        e.kers_lap_charge_capped = bool(gr.is_max_charge_kj_per_lap_reached)
        # ERS strategy state from the electronics substruct.
        e.ers_overtake_mode = bool(gr.electronics.is_ers_overtake_mode_on)
        e.ers_heat_charging = bool(gr.electronics.is_ers_heat_charging_on)
        e.ers_deployment_map = int(gr.electronics.ers_deployment_map)
        e.ers_recharge_map = float(gr.electronics.ers_recharge_map)
        e.wrong_way = bool(gr.is_wrong_way)
        e.valid_lap = bool(gr.is_valid_lap)
        e.last_lap = bool(gr.is_last_lap)

        # Phase 2 — analog engine readouts. The graphics block stores
        # water/air temps as int8 °C; cast to float so downstream code
        # can format uniformly.
        e.water_temp_c = float(gr.water_temperature_c)
        e.oil_temp_c = float(gr.oil_temperature_c)
        e.oil_pressure_bar = float(gr.oil_pressure_bar)
        e.fuel_pressure_bar = float(gr.fuel_pressure_bar)
        e.exhaust_temp_c = float(gr.exhaust_temperature_c)
        e.battery_voltage = float(gr.battery_voltage)
        e.fuel_liters = float(gr.fuel_liter_current_quantity)

        # Driver-aid setting levels from the electronics substruct.
        # PDF: graphics.electronics.{tc,abs}_level are the driver-set
        # levels (0 = off, 1+ = enabled). The chip's "is this aid on?"
        # check (`tc_level > 0`) needs the setting level, not the live
        # intervention intensity in physics.tc / physics.abs.
        e.tc_level = float(gr.electronics.tc_level)
        e.abs_level = float(gr.electronics.abs_level)

        # Phase 3 — graphics-block fields for the inputs widget.
        i = self._frame.inputs
        i.handbrake = max(0.0, min(1.0, float(gr.handbrake_percent)))
        # Per PDF, graphics.steer_degrees is the wheel rotation in
        # degrees from centre (signed). Cleaner than computing degrees
        # from physics.steerAngle, which is a -1..+1 ratio.
        i.steering_deg = float(gr.steer_degrees)
        # The c_char array comes back null-padded; strip and decode.
        mode_raw = bytes(gr.performance_mode_name).rstrip(b"\x00")
        i.performance_mode = mode_raw.decode("ascii", errors="ignore").strip()

        # Per-wheel data published as embedded TyreState blocks:
        #   * lock — game-supplied, replaces the slip/angular-speed
        #     heuristic and its standstill false positives.
        #   * tyre_normalized_pressure / temperature_* — ratios against the
        #     compound's ideal (1.0 = on target). Lets colour bands track
        #     per-compound targets instead of hard-coded reference points.
        # PDF documents tyre_temperature_left = inner, _right = outer,
        # but verified live the field name is car-relative ("left" = the
        # side of the patch facing the car's left). That happens to land
        # on inner for FR/RR but on outer for FL/RL — so left wheels
        # need the L↔R values swapped to land on the inner/outer slots.
        for wid, ts in (("FL", gr.tyre_lf), ("FR", gr.tyre_rf),
                        ("RL", gr.tyre_lr), ("RR", gr.tyre_rr)):
            w = self._frame.wheels[wid]
            w.lock = bool(ts.lock)
            if ts.tyre_normalized_pressure > 0.0:
                w.tire_p_norm = float(ts.tyre_normalized_pressure)
            if ts.tyre_normalized_temperature_core > 0.0:
                w.tire_t_norm_c = float(ts.tyre_normalized_temperature_core)
            if ts.tyre_normalized_temperature_center > 0.0:
                w.tire_t_norm_m = float(ts.tyre_normalized_temperature_center)
            if ts.tyre_temperature_center > 0.0:
                w.tire_t_m = float(ts.tyre_temperature_center)
            is_left = wid[1] == "L"
            inner_norm = (ts.tyre_normalized_temperature_right if is_left
                          else ts.tyre_normalized_temperature_left)
            outer_norm = (ts.tyre_normalized_temperature_left if is_left
                          else ts.tyre_normalized_temperature_right)
            inner_temp = (ts.tyre_temperature_right if is_left
                          else ts.tyre_temperature_left)
            outer_temp = (ts.tyre_temperature_left if is_left
                          else ts.tyre_temperature_right)
            if inner_norm > 0.0:
                w.tire_t_norm_i = float(inner_norm)
            if outer_norm > 0.0:
                w.tire_t_norm_o = float(outer_norm)
            if inner_temp > 0.0:
                w.tire_t_i = float(inner_temp)
            if outer_temp > 0.0:
                w.tire_t_o = float(outer_temp)
            if ts.brake_normalized_temperature > 0.0:
                w.brake_t_norm = float(ts.brake_normalized_temperature)
            else:
                # AC EVO leaves this at 0.0 for cars / states it doesn't
                # compute — without a fallback the field would stick at
                # its default 1.0 and paint the icon green at any disc
                # temperature. Use the prior frame's raw brake_t (one
                # ~16 ms tick stale, irrelevant at brake-temp dynamics)
                # against the same curve the synthetic source uses.
                w.brake_t_norm = self._brake_curve.interpolate(w.brake_t)

        # Tyre compound names — duplicated across all 4 TyreStates, so we
        # read them once. ctypes c_char arrays come back as null-padded
        # bytes; strip nulls before decoding.
        front_raw = bytes(gr.tyre_lf.tyre_compound_front).rstrip(b"\x00")
        rear_raw = bytes(gr.tyre_lf.tyre_compound_rear).rstrip(b"\x00")
        front_compound = front_raw.decode("ascii", errors="ignore").strip()
        rear_compound = rear_raw.decode("ascii", errors="ignore").strip()
        self._frame.wheels["FL"].compound = front_compound
        self._frame.wheels["FR"].compound = front_compound
        self._frame.wheels["RL"].compound = rear_compound
        self._frame.wheels["RR"].compound = rear_compound
