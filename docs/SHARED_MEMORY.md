# Assetto Corsa EVO — Shared Memory Reference

> A field-by-field reference for the three named shared-memory blocks
> Assetto Corsa EVO publishes on Windows. Intended as a long-lived study
> document — every field this overlay reads, every field still opaque,
> and every quirk worth knowing about future struct extensions.

This document is a consolidated transcription of the publicly available
information about AC EVO's shared-memory layout, cross-referenced with
the live-telemetry overlay's own ctypes structs in
[`src/overlay/sources/ac_evo.py`](../src/overlay/sources/ac_evo.py).

The canonical upstream source is Kunos's official guide on Steam:
**[Assetto Corsa EVO — Shared Memory documentation (#3707421508)](https://steamcommunity.com/sharedfiles/filedetails/?id=3707421508).**
Where this document and the Steam guide disagree, the guide wins — file
an issue or PR so the overlay catches up.

---

## Table of contents

1. [Transport](#1-transport)
2. [Block sizes & versions](#2-block-sizes--versions)
3. [Conventions used in every block](#3-conventions-used-in-every-block)
4. [Physics block — `Local\acevo_pmf_physics`](#4-physics-block--localacevo_pmf_physics)
5. [Graphics block — `Local\acevo_pmf_graphics`](#5-graphics-block--localacevo_pmf_graphics)
6. [Static block — `Local\acevo_pmf_static`](#6-static-block--localacevo_pmf_static)
7. [Embedded substructs](#7-embedded-substructs)
8. [Enumerations](#8-enumerations)
9. [Semantic gotchas vs Assetto Corsa 1](#9-semantic-gotchas-vs-assetto-corsa-1)
10. [Still-opaque areas worth investigating](#10-still-opaque-areas-worth-investigating)
11. [Inspecting & extending the layout](#11-inspecting--extending-the-layout)
12. [References](#12-references)

---

## 1. Transport

AC EVO publishes telemetry through three Windows named file-mappings under
the `Local\` namespace:

| Tag                            | Cadence              | Purpose                                |
| ------------------------------ | -------------------- | -------------------------------------- |
| `Local\acevo_pmf_physics`      | Every physics step (typically 333 Hz) | Fast-changing dynamics — pedals, RPM, wheels, forces |
| `Local\acevo_pmf_graphics`     | Every rendered frame (HUD-rate)       | Slower HUD/UI data — laps, fuel, position, electronics |
| `Local\acevo_pmf_static`       | Once per session                       | Track / session metadata that won't change mid-session |

**Important:** Python's stdlib `mmap.mmap(-1, size, tagname=name)` will
*create* a mapping under the requested name if one doesn't exist. That
is the wrong behaviour for "did the game start yet?": you'd silently
attach to an empty mapping and read zeros. Use Win32
`OpenFileMappingW` directly — it returns `NULL` (with
`ERROR_FILE_NOT_FOUND == 2`) when the name does not exist. See
[`_NamedMapping`](../src/overlay/sources/ac_evo.py) for the canonical
read-only attach.

The `Local\` prefix is mandatory in the API call. Some references show
the bare names (`acevo_pmf_*`); both work because Windows resolves bare
names to the per-session `Local\` namespace, but the explicit form is
unambiguous and matches what AC EVO publishes.

**Endianness.** Little-endian on all supported platforms (Windows
x86-64). All multi-byte integers and floats follow the host
representation.

**Concurrency.** AC EVO writes the blocks without any documented
producer/consumer synchronisation. Readers should:

* tolerate torn reads — verify `packetId` jumps by at most one between
  consecutive reads when ordering matters;
* never assume a sub-struct is consistent across reads if the producer
  is mid-write — the overlay re-reads the full block per tick rather
  than per field.

---

## 2. Block sizes & versions

| Block      | Documented size | Overlay map size† |
| ---------- | --------------- | ----------------- |
| `physics`  | 800 bytes       | 4096 bytes        |
| `graphics` | ~6–8 kB (varies — embeds 60-car coord table) | 8192 bytes |
| `static`   | ~210 bytes      | 2048 bytes        |

† *The overlay maps a generously-sized region. Mapping more than the
producer wrote is harmless on Windows (the trailing pages are
zero-filled), and it means the reader doesn't have to know the exact
byte size up-front.*

The static block carries two version strings:

* `sm_version` — shared-memory format version (e.g. `"1.0"`, `"1.1"`).
  Bump when offsets change.
* `ac_evo_version` — game build version. Useful for matching dumps
  against game patches.

Always log both at session start when investigating a layout discrepancy.

---

## 3. Conventions used in every block

* **Wheel ordering.** Every per-wheel array is ordered
  `[FL, FR, RL, RR]`. Both AC1 and AC EVO agree on this.
* **Gear convention.** `0 = Reverse`, `1 = Neutral`, `2..N = forward
  gears`. Display gear = `gear − 1` for forward gears.
* **Pedal scale.** Physics-block pedals (`gas`, `brake`, `clutch`) are
  `0.0..1.0`. Graphics-block `*_percent` mirrors of the same values are
  also `0.0..1.0` (despite the `_percent` suffix — the names come from
  AC1).
* **Steering scale.** `physics.steerAngle` is in **radians**, signed
  (negative = left). The graphics block exposes a `steer_degrees` int
  for HUD use.
* **Angles.** Pitch / roll / heading / camber are in **radians**.
* **Forces & torques.** Newtons (`fx`, `fy`, `wheelLoad`) and
  Newton-metres (`mz`, `brakeTorque`).
* **Temperatures.** °C unless suffixed otherwise.
* **Pressures.** psi for tyres (`wheelsPressure`,
  `tyre_pressure`); bar for fluids (`oil_pressure_bar`,
  `water_pressure_bar`, `fuel_pressure_bar`).
* **Distance.** Track length in metres; lap distance in kilometres
  (`current_km`, `total_km`); ride heights in **metres or millimetres
  depending on the chassis** — the overlay auto-detects (any value with
  `|height| ≥ 1.0` is treated as mm; below that it's metres × 1000).
* **Boolean encoding.** Booleans are 1-byte `c_bool` in the graphics &
  static blocks; the physics block uses `c_int32` flags
  (`pitLimiterOn`, `tcInAction`, `absInAction`, `drsAvailable`,
  `drsEnabled`, `ignitionOn`, `starterEngineOn`, `isEngineRunning`,
  `isAIControlled`).
* **String encoding.** Most strings are fixed-length `char[33]`
  null-padded ASCII. Decode by stripping trailing `\x00` then ASCII
  decode (`bytes(...).rstrip(b'\x00').decode('ascii', errors='ignore')`).
  The version strings in the static block are `char[15]`.
* **Struct packing.** All structs are `_pack_ = 4`. ctypes preserves
  natural alignment within that bound — i.e. `int32`/`float` align on
  4, but `char[33]` does not, and `c_bool` is 1 byte. This means the
  graphics and static blocks have implicit padding before any 4-byte
  field that follows a `char[N]` whose `N` is not a multiple of 4.

---

## 4. Physics block — `Local\acevo_pmf_physics`

Total documented size: **800 bytes**. The first 416 bytes are
AC1-compatible (so AC1 plugins that only read up to `tyreTempO` continue
to work); the remaining 384 bytes are AC EVO additions.

Offsets below are computed from `_pack_ = 4` natural alignment of
`int32`/`float`/`int32[N]`/`float[N]` fields — the layout is regular
because there are no `char[N]` or `bool` fields in this block.

### 4.1 AC1-compatible prefix (offsets 0–415)

| Offset | Size | Type        | Field                  | Units / Range            | Notes |
| -----: | ---: | ----------- | ---------------------- | ------------------------ | ----- |
|      0 |    4 | `int32`     | `packetId`             | monotonically increasing | Sequence number — useful for change-detection and torn-read guards. |
|      4 |    4 | `float`     | `gas`                  | 0..1                     | Throttle pedal. |
|      8 |    4 | `float`     | `brake`                | 0..1                     | Brake pedal. |
|     12 |    4 | `float`     | `fuel`                 | litres                   | **Deprecated mirror** — graphics `fuel_liter_current_quantity` is the current source. |
|     16 |    4 | `int32`     | `gear`                 | 0=R, 1=N, 2..N=forward   | See gear convention. |
|     20 |    4 | `int32`     | `rpms`                 | RPM                      | Live engine speed. |
|     24 |    4 | `float`     | `steerAngle`           | rad, signed              | Negative = left. |
|     28 |    4 | `float`     | `speedKmh`             | km/h                     | Ground speed. |
|     32 |   12 | `float[3]`  | `velocity`             | m/s, world XYZ           | World-frame velocity. |
|     44 |   12 | `float[3]`  | `accG`                 | g (≈9.81 m/s²)           | `[0]=lateral, [1]=vertical, [2]=longitudinal`. **Note ordering** — graphics `g_forces_x/y/z` exposes the same triple. |
|     56 |   16 | `float[4]`  | `wheelSlip`            | per-wheel                | Combined slip metric. |
|     72 |   16 | `float[4]`  | `wheelLoad`            | N                        | Vertical load per corner. |
|     88 |   16 | `float[4]`  | `wheelsPressure`       | psi                      | Inflation pressure. |
|    104 |   16 | `float[4]`  | `wheelAngularSpeed`    | rad/s                    | Per-wheel angular speed. |
|    120 |   16 | `float[4]`  | `tyreWear`             | 0..1, **0 = new**        | **Inverted vs AC1.** AC1 had `0..100 = % remaining`. AC EVO writes `0.0` on new tyres and `1.0` on fully worn — flip if you want a "remaining grip" semantic. |
|    136 |   16 | `float[4]`  | `tyreDirtyLevel`       | 0..4 (approximate)        | Surface contamination from off-track grass/sand/marbles. |
|    152 |   16 | `float[4]`  | `tyreCoreTemperature`  | °C                       | Carcass core temperature. |
|    168 |   16 | `float[4]`  | `camberRAD`            | rad                      | Negative = top-in (typical). |
|    184 |   16 | `float[4]`  | `suspensionTravel`     | m                        | AC EVO no longer publishes a per-car max — calibrate by rolling-max. **Sign convention varies by car**: most cars report positive metres from full extension, but cars with active / electronically managed suspension report signed displacement from a static reference (rest near 0, compression negative). Take `abs()` before calibrating to handle both. |
|    200 |    4 | `float`     | `drs`                  | 0..1                     | DRS deploy state. |
|    204 |    4 | `float`     | `tc`                   | 0..1                     | Traction-control aid **strength setting** (not "currently cutting" — see `tcInAction`). |
|    208 |    4 | `float`     | `heading`              | rad                      | Yaw. |
|    212 |    4 | `float`     | `pitch`                | rad                      | |
|    216 |    4 | `float`     | `roll`                 | rad                      | |
|    220 |    4 | `float`     | `cgHeight`             | m                        | Centre-of-gravity height. |
|    224 |   20 | `float[5]`  | `carDamage`            | 0..1 per zone            | `[0]=front, [1]=rear, [2]=left, [3]=right, [4]=centre`. |
|    244 |    4 | `int32`     | `numberOfTyresOut`     | 0..4                     | Tyres currently off-track. |
|    248 |    4 | `int32`     | `pitLimiterOn`         | 0/1                      | |
|    252 |    4 | `float`     | `abs`                  | 0..1                     | ABS aid strength setting (see `absInAction`). |
|    256 |    4 | `float`     | `kersCharge`           | 0..1                     | KERS state-of-charge. |
|    260 |    4 | `float`     | `kersInput`            | 0..1                     | Driver-requested KERS deploy. |
|    264 |    4 | `int32`     | `autoShifterOn`        | 0/1                      | |
|    268 |    8 | `float[2]`  | `rideHeight`           | m or mm — auto-detect    | `[0]=front, [1]=rear`. Some chassis publish mm directly; the overlay's auto-detect threshold is `|h| ≥ 1.0`. |
|    276 |    4 | `float`     | `turboBoost`           | bar (relative)           | |
|    280 |    4 | `float`     | `ballast`              | kg                       | |
|    284 |    4 | `float`     | `airDensity`           | kg/m³                    | |
|    288 |    4 | `float`     | `airTemp`              | °C                       | Ambient. |
|    292 |    4 | `float`     | `roadTemp`             | °C                       | Track surface. |
|    296 |   12 | `float[3]`  | `localAngularVel`      | rad/s, body-frame         | |
|    308 |    4 | `float`     | `finalFF`              | -1..1                    | Force-feedback signal, signed; the overlay uses `abs()`. |
|    312 |    4 | `float`     | `performanceMeter`     | s                        | Delta to reference lap, in seconds (positive = slower). |
|    316 |    4 | `int32`     | `engineBrake`          | 0..N                     | Engine-brake setting index. |
|    320 |    4 | `int32`     | `ersRecoveryLevel`     | 0..N                     | |
|    324 |    4 | `int32`     | `ersPowerLevel`        | 0..N                     | |
|    328 |    4 | `int32`     | `ersHeatCharging`      | enum                     | Heat-vs-MGUH harvest mode. |
|    332 |    4 | `int32`     | `ersIsCharging`        | 0/1                      | |
|    336 |    4 | `float`     | `kersCurrentKJ`        | kJ                       | Energy in the KERS this lap. |
|    340 |    4 | `int32`     | `drsAvailable`         | 0/1                      | DRS allowed at current track position. |
|    344 |    4 | `int32`     | `drsEnabled`           | 0/1                      | Driver toggled DRS. |
|    348 |   16 | `float[4]`  | `brakeTemp`            | °C                       | Disc temperature per corner. |
|    364 |    4 | `float`     | `clutch`               | 0..1                     | Clutch pedal. |
|    368 |   16 | `float[4]`  | `tyreTempI`            | °C                       | Inner contact-patch face. |
|    384 |   16 | `float[4]`  | `tyreTempM`            | °C                       | Middle contact-patch face. |
|    400 |   16 | `float[4]`  | `tyreTempO`            | °C                       | Outer contact-patch face. |

### 4.2 AC EVO additions (offsets 416–799)

| Offset | Size | Type             | Field                 | Units / Range          | Notes |
| -----: | ---: | ---------------- | --------------------- | ---------------------- | ----- |
|    416 |    4 | `int32`          | `isAIControlled`      | 0/1                    | Player vs AI for the focused car. |
|    420 |   48 | `float[4][3]`    | `tyreContactPoint`    | world XYZ, m           | Contact point per wheel. |
|    468 |   48 | `float[4][3]`    | `tyreContactNormal`   | unit vector            | Road normal at contact patch. |
|    516 |   48 | `float[4][3]`    | `tyreContactHeading`  | unit vector            | Tyre heading at contact patch (combined with normal → full contact frame). |
|    564 |    4 | `float`          | `brakeBias`           | 0..1, fraction front   | `0.56` = 56 % front. |
|    568 |   12 | `float[3]`       | `localVelocity`       | m/s, body-frame        | |
|    580 |    4 | `int32`          | `P2PActivations`      | count                  | Push-to-pass uses left this race/session. |
|    584 |    4 | `int32`          | `P2PStatus`           | enum                   | Idle / armed / active. |
|    588 |    4 | `int32`          | `currentMaxRpm`       | RPM                    | **Canonical replacement** for AC1's static `maxRpm` (gone in EVO). Per-tick — varies with engine state. |
|    592 |   16 | `float[4]`       | `mz`                  | Nm                     | Self-aligning torque per tyre. |
|    608 |   16 | `float[4]`       | `fx`                  | N                      | Longitudinal tyre force. |
|    624 |   16 | `float[4]`       | `fy`                  | N                      | Lateral tyre force. |
|    640 |   16 | `float[4]`       | `slipRatio`           | dimensionless          | Longitudinal slip. |
|    656 |   16 | `float[4]`       | `slipAngle`           | rad                    | Lateral slip angle. |
|    672 |    4 | `int32`          | `tcInAction`          | 0/1                    | TC currently cutting power. **Use this for the visual chip blink, not `tc`.** |
|    676 |    4 | `int32`          | `absInAction`         | 0/1                    | ABS currently modulating. |
|    680 |   16 | `float[4]`       | `suspensionDamage`    | 0..1 per corner        | |
|    696 |   16 | `float[4]`       | `tyreTemp`            | °C                     | Representative surface temp (different sampling vs `tyreTempI/M/O`). |
|    712 |    4 | `float`          | `waterTemp`           | °C                     | Coolant. |
|    716 |   16 | `float[4]`       | `brakeTorque`         | Nm per wheel           | |
|    732 |    4 | `int32`          | `frontBrakeCompound`  | enum                   | Brake-pad compound index. |
|    736 |    4 | `int32`          | `rearBrakeCompound`   | enum                   | |
|    740 |   16 | `float[4]`       | `padLife`             | 0..1, 1 = fresh        | |
|    756 |   16 | `float[4]`       | `discLife`            | 0..1, 1 = fresh        | |
|    772 |    4 | `int32`          | `ignitionOn`          | 0/1                    | |
|    776 |    4 | `int32`          | `starterEngineOn`     | 0/1                    | |
|    780 |    4 | `int32`          | `isEngineRunning`     | 0/1                    | |
|    784 |    4 | `float`          | `kerbVibration`       | 0..1                   | Force-feedback effect strength — kerb. |
|    788 |    4 | `float`          | `slipVibrations`      | 0..1                   | FFB — tyre slip. |
|    792 |    4 | `float`          | `roadVibrations`      | 0..1                   | FFB — road texture. |
|    796 |    4 | `float`          | `absVibrations`       | 0..1                   | FFB — ABS pulse feel. |

---

## 5. Graphics block — `Local\acevo_pmf_graphics`

Largest of the three blocks. The HUD-rate cadence makes it a poorer
choice than physics for closed-loop control, but it is the only source
for many fields: lap timing, position, fuel projection, electronics
state, and live engine output.

Listed in declaration order. Offsets are not included because the
embedded `char[33]` and `c_bool` fields induce alignment padding that
varies if Kunos re-orders or extends a substruct. Compute a current
offset table at runtime with
`ctypes.sizeof(_SPageFileGraphic) / .from_buffer_copy(...).field.offset`
or use the `dump` tool (§11).

### 5.1 Header & focus

| Type        | Field              | Notes |
| ----------- | ------------------ | ----- |
| `int32`     | `packetId`         | Sequence number. |
| `int32`     | `status`           | `ACEVO_STATUS` enum (§8.1). |
| `uint64`    | `focused_car_id_a` | 128-bit GUID, low half. |
| `uint64`    | `focused_car_id_b` | 128-bit GUID, high half. |
| `uint64`    | `player_car_id_a`  | Same shape, the local player. |
| `uint64`    | `player_car_id_b`  | |

### 5.2 Engine & shift state

| Type      | Field                                | Notes |
| --------- | ------------------------------------ | ----- |
| `uint16`  | `rpm`                                | Live RPM (HUD-rate copy). |
| `bool`    | `is_rpm_limiter_on`                  | |
| `bool`    | `is_change_up_rpm`                   | Shift-up hint — overlay forces RPM bar to red. |
| `bool`    | `is_change_down_rpm`                 | Shift-down hint — overlay forces RPM bar to blue. |
| `bool`    | `tc_active`                          | HUD-level TC active flag (use `tcInAction` from physics for fine timing). |
| `bool`    | `abs_active`                         | |
| `bool`    | `esc_active`                         | Stability control engaging. |
| `bool`    | `launch_active`                      | Launch control armed/engaging. |
| `bool`    | `is_ignition_on`                     | |
| `bool`    | `is_engine_running`                  | |
| `bool`    | `kers_is_charging`                   | KERS recovery in progress. |
| `bool`    | `is_wrong_way`                       | Driver going against direction. |
| `bool`    | `is_drs_available`                   | DRS allowed in this zone. |
| `bool`    | `battery_is_charging`                | Generic battery charging (hybrid). |
| `bool`    | `is_max_kj_per_lap_reached`          | F1-style energy cap reached. |
| `bool`    | `is_max_charge_kj_per_lap_reached`   | |

### 5.3 Speed, pedals & inputs

| Type    | Field                  | Notes |
| ------- | ---------------------- | ----- |
| `int16` | `display_speed_kmh`    | Same value, three units — pre-rounded. |
| `int16` | `display_speed_mph`    | |
| `int16` | `display_speed_ms`     | |
| `float` | `pitspeeding_delta`    | km/h over the pit-lane limit. |
| `int16` | `gear_int`             | |
| `float` | `rpm_percent`          | 0..1 of redline — best source for RPM bar fill. |
| `float` | `gas_percent`          | 0..1. |
| `float` | `brake_percent`        | |
| `float` | `handbrake_percent`    | |
| `float` | `clutch_percent`       | |
| `float` | `steering_percent`     | -1..1. |
| `float` | `ffb_strength`         | 0..1, 1.0 = clipping. |
| `float` | `car_ffb_multiplier`   | Per-car FFB scale. |

### 5.4 Engine analog readouts

| Type    | Field                       | Units / Notes |
| ------- | --------------------------- | -------------- |
| `float` | `water_temperature_percent` | 0..1 of redline temp. |
| `float` | `water_pressure_bar`        | bar. |
| `float` | `fuel_pressure_bar`         | bar. |
| `int8`  | `water_temperature_c`       | °C — int8, cast before use. |
| `int8`  | `air_temperature_c`         | °C — int8. |
| `float` | `oil_temperature_c`         | °C. |
| `float` | `oil_pressure_bar`          | bar. |
| `float` | `exhaust_temperature_c`     | °C. |
| `float` | `g_forces_x`                | g, lateral. |
| `float` | `g_forces_y`                | g, vertical. |
| `float` | `g_forces_z`                | g, longitudinal. |
| `float` | `turbo_boost`               | bar (relative). |
| `float` | `turbo_boost_level`         | 0..N — driver-set boost map index, fractional. |
| `float` | `turbo_boost_perc`          | 0..1 of `max_turbo_boost`. |
| `int32` | `steer_degrees`             | Total wheel rotation, signed degrees. |

### 5.5 Distance, time & lap timing

| Type     | Field                       | Units / Notes |
| -------- | --------------------------- | -------------- |
| `float`  | `current_km`                | Distance covered this lap, km. |
| `uint32` | `total_km`                  | Total session distance, km. |
| `uint32` | `total_driving_time_s`      | s. |
| `int32`  | `time_of_day_hours`         | 0..23. |
| `int32`  | `time_of_day_minutes`       | 0..59. |
| `int32`  | `time_of_day_seconds`       | 0..59. |
| `int32`  | `delta_time_ms`             | Delta to reference lap, ms (positive = slower). |
| `int32`  | `current_lap_time_ms`       | ms. |
| `int32`  | `predicted_lap_time_ms`     | ms — projection based on current sectors. |

### 5.6 Fuel

| Type    | Field                                 | Units / Notes |
| ------- | ------------------------------------- | -------------- |
| `float` | `fuel_liter_current_quantity`         | L. |
| `float` | `fuel_liter_current_quantity_percent` | 0..1 of `max_fuel`. |
| `float` | `fuel_liter_per_km`                   | Smoothed consumption rate. |
| `float` | `km_per_fuel_liter`                   | Reciprocal. |

### 5.7 Live engine output

| Type    | Field            | Units / Notes |
| ------- | ---------------- | -------------- |
| `float` | `current_torque` | Nm — boosted, at current RPM. |
| `int32` | `current_bhp`    | BHP — boosted. **Replaces** AC1's interpolated power-curve hack `(power × (1 + boost))`. |

### 5.8 Tyre states (4 × 256 B)

```
tyre_lf : SMEvoTyreState (256 B)
tyre_rf : SMEvoTyreState (256 B)
tyre_lr : SMEvoTyreState (256 B)
tyre_rr : SMEvoTyreState (256 B)
```

See [§7.1](#71-tyre-state-256-b-per-corner) for the per-corner field
list. Note the wheel-name mapping vs the physics arrays:

| Graphics field | Wheel | Index in physics arrays |
| -------------- | ----- | ----------------------- |
| `tyre_lf`      | FL    | 0                       |
| `tyre_rf`      | FR    | 1                       |
| `tyre_lr`      | RL    | 2                       |
| `tyre_rr`      | RR    | 3                       |

### 5.9 Position, KERS, control state

| Type    | Field                 | Notes |
| ------- | --------------------- | ----- |
| `float` | `npos`                | Normalised lap position, 0..1. |
| `float` | `kers_charge_perc`    | 0..1 of full charge. |
| `float` | `kers_current_perc`   | 0..1 of current deploy budget. |
| `float` | `control_lock_time`   | s — input lockout (after a spin/contact). |

### 5.10 Damage & pit (opaque substructs)

| Type            | Field        | Notes |
| --------------- | ------------ | ----- |
| `byte[128]`     | `car_damage` | `SMEvoDamageState`, see §7.2 — **opaque** in this overlay. |
| `int32`         | `car_location` | `ACEVO_CAR_LOCATION` enum (§8.2). |
| `byte[64]`      | `pit_info`   | `SMEvoPitInfo`, see §7.3 — **opaque**. |

### 5.11 Fuel projections & battery

| Type    | Field                              | Units / Notes |
| ------- | ---------------------------------- | -------------- |
| `float` | `fuel_liter_used`                  | L this stint. |
| `float` | `fuel_liter_per_lap`               | Recent average. |
| `float` | `laps_possible_with_fuel`          | Including the current partial lap. |
| `float` | `battery_temperature`              | °C. |
| `float` | `battery_voltage`                  | V. |
| `float` | `instantaneous_fuel_liter_per_km`  | Live, unsmoothed. |
| `float` | `instantaneous_km_per_fuel_liter`  | |
| `float` | `gear_rpm_window`                  | RPM band where the current gear is optimal. |

### 5.12 Instrumentation & electronics (opaque substructs)

Each of these is a 128-byte block of car-dependent values. The overlay
maps them as opaque `byte[128]` so the surrounding offsets stay
correct; decoding will need a per-car schema (see §10).

| Field                             | Notes |
| --------------------------------- | ----- |
| `instrumentation` (128 B)         | Live values. |
| `instrumentation_min_limit` (128 B) | Per-channel min. |
| `instrumentation_max_limit` (128 B) | Per-channel max. |
| `electronics` (128 B)             | Driver-set electronics map (engine map, brake migration, diff entry/mid/exit, etc.). |
| `electronics_min_limit` (128 B)   | |
| `electronics_max_limit` (128 B)   | |
| `electronics_is_modifiable` (128 B) | Per-channel "can the driver change this?". |

### 5.13 Standings & laps

| Type     | Field             | Notes |
| -------- | ----------------- | ----- |
| `int32`  | `total_lap_count` | Session lap count. |
| `uint32` | `current_pos`     | 1-based finishing position. |
| `uint32` | `total_drivers`   | |
| `int32`  | `last_laptime_ms` | ms; -1 / 0 if no completed lap. |
| `int32`  | `best_laptime_ms` | ms. |
| `int32`  | `flag`            | `ACEVO_FLAG_TYPE` (§8.3) — flag shown to player. |
| `int32`  | `global_flag`     | `ACEVO_FLAG_TYPE` — session-global flag (e.g. SC, FCY). |

### 5.14 Car-spec & engine type

| Type     | Field                    | Notes |
| -------- | ------------------------ | ----- |
| `uint32` | `max_gears`              | Forward gears (does not include R or N). |
| `int32`  | `engine_type`            | `ACEVO_ENGINE_TYPE` (§8.4). |
| `bool`   | `has_kers`               | |
| `bool`   | `is_last_lap`            | Final lap of the session. |
| `char[33]` | `performance_mode_name` | Driver preset (`"WET"`, `"QUAL"`, etc.). Empty on cars without presets. |
| `float`  | `diff_coast_raw_value`   | Differential coast setting, raw (car-dependent units). |
| `float`  | `diff_power_raw_value`   | Differential power setting. |

### 5.15 Race-cut / track-limits

| Type    | Field                       | Notes |
| ------- | --------------------------- | ----- |
| `int32` | `race_cut_gained_time_ms`   | Time gained by an off-track ms — basis of the give-back deadline. |
| `int32` | `distance_to_deadline`      | Distance remaining to give the time back. |
| `float` | `race_cut_current_delta`    | Delta vs. the cleanly-driven reference. |

### 5.16 Session & timing (opaque substructs)

| Type        | Field            | Notes |
| ----------- | ---------------- | ----- |
| `byte[256]` | `session_state`  | `SMEvoSessionState`, see §7.5 — **opaque**. |
| `byte[256]` | `timing_state`   | `SMEvoTimingState`, see §7.6 — **opaque**. |

### 5.17 Network / performance

| Type    | Field                 | Notes |
| ------- | --------------------- | ----- |
| `int32` | `player_ping`         | ms (online only). |
| `int32` | `player_latency`      | ms. |
| `int32` | `player_cpu_usage`    | 0..100. |
| `int32` | `player_cpu_usage_avg`| Rolling avg. |
| `int32` | `player_qos`          | 0..N quality-of-service rating. |
| `int32` | `player_qos_avg`      | |
| `int32` | `player_fps`          | |
| `int32` | `player_fps_avg`      | |

### 5.18 Player & car identity

| Type        | Field            | Notes |
| ----------- | ---------------- | ----- |
| `char[33]`  | `driver_name`    | Given name, ASCII. |
| `char[33]`  | `driver_surname` | |
| `char[33]`  | `car_model`      | Internal model id. |
| `bool`      | `is_in_pit_box`  | Garage. |
| `bool`      | `is_in_pit_lane` | |
| `bool`      | `is_valid_lap`   | False after the lap is invalidated by a cut. |

### 5.19 Multi-car coordinates

| Type             | Field             | Notes |
| ---------------- | ----------------- | ----- |
| `float[60][3]`   | `car_coordinates` | World XYZ for up to 60 cars. **720 bytes.** Cars beyond `active_cars` are zero-filled. |
| `float`          | `gap_ahead`       | s, signed. |
| `float`          | `gap_behind`      | s. |
| `uint8`          | `active_cars`     | Number of valid entries in `car_coordinates`. |

### 5.20 Fuel summary

| Type    | Field                 | Notes |
| ------- | --------------------- | ----- |
| `float` | `fuel_per_lap`        | Average across the session. |
| `float` | `fuel_estimated_laps` | Best-estimate laps remaining at current pace. |

### 5.21 Assists, max fuel, compound mode

| Type        | Field                  | Notes |
| ----------- | ---------------------- | ----- |
| `byte[64]`  | `assists_state`        | `SMEvoAssistsState`, see §7.7 — **opaque**. |
| `float`     | `max_fuel`             | L — replaces AC1's static `maxFuel`. |
| `float`     | `max_turbo_boost`      | Replaces AC1's static `maxTurboBoost`. 0 on naturally-aspirated. |
| `bool`      | `use_single_compound`  | Mandatory single-compound rule active. |

---

## 6. Static block — `Local\acevo_pmf_static`

Written once when the session loads. AC EVO has **drastically slimmed
this block down vs AC1**: the car-spec fields (`maxRpm`, `maxPower`,
`maxTorque`, `maxTurboBoost`, `suspensionMaxTravel`, etc.) are gone
entirely. What remains is session and track metadata.

| Type        | Field                              | Units / Notes |
| ----------- | ---------------------------------- | -------------- |
| `char[15]`  | `sm_version`                       | Shared-memory format version (e.g. `"1.0"`). |
| `char[15]`  | `ac_evo_version`                   | Game build version. |
| `int32`     | `session`                          | `ACEVO_SESSION_TYPE` enum (§8.5). |
| `char[33]`  | `session_name`                     | UI-facing name. |
| `uint8`     | `event_id`                         | Event index in a championship/weekend. |
| `uint8`     | `session_id`                       | Session index within the event. |
| `int32`     | `starting_grip`                    | `ACEVO_STARTING_GRIP` enum (§8.6). |
| `float`     | `starting_ambient_temperature_c`   | °C. |
| `float`     | `starting_ground_temperature_c`    | °C. |
| `bool`      | `is_static_weather`                | Weather frozen for the session. |
| `bool`      | `is_timed_race`                    | True for a timed (vs lap-count) race. |
| `bool`      | `is_online`                        | Multiplayer session. |
| `int32`     | `number_of_sessions`               | Sessions in this event/weekend. |
| `char[33]`  | `nation`                           | Track nation (e.g. `"GBR"`, `"ITA"`). |
| `float`     | `longitude`                        | Track location, decimal degrees. |
| `float`     | `latitude`                         | |
| `char[33]`  | `track`                            | Track id (e.g. `"silverstone"`). |
| `char[33]`  | `track_configuration`              | Layout id (`"gp"`, `"international"`, `""` = default). |
| `float`     | `track_length_m`                   | m. |

Where to source car specs now:

* **`max_rpm`** → `physics.currentMaxRpm` (per-tick) or
  `graphics.rpm_percent` (use the ratio directly).
* **`max_power`, `max_torque`** → `graphics.current_bhp`,
  `graphics.current_torque` give the *live* values; an absolute peak
  has to be observed (rolling max) or hard-coded per car.
* **`max_turbo_boost`** → `graphics.max_turbo_boost`.
* **`max_fuel`** → `graphics.max_fuel`.
* **`suspensionMaxTravel`** → calibrate per session by rolling-max on
  `physics.suspensionTravel`. The overlay seeds with `2× first sample`
  and tightens to `1.05× observed max` thereafter.

---

## 7. Embedded substructs

### 7.1 Tyre state (256 B per corner)

`SMEvoTyreState`, embedded four times in the graphics block.

| Type        | Field                                    | Units / Notes |
| ----------- | ---------------------------------------- | -------------- |
| `float`     | `slip`                                   | Combined slip. |
| `bool`      | `lock`                                   | Wheel locked **right now** (game-supplied — replaces the slip/angular-speed heuristic the AC1 plugin used). |
| `float`     | `tyre_pressure`                          | psi. |
| `float`     | `tyre_temperature_c`                     | °C, representative. |
| `float`     | `brake_temperature_c`                    | °C. |
| `float`     | `brake_pressure`                         | bar (per-corner). |
| `float`     | `tyre_temperature_left`                  | °C, left face of contact patch. |
| `float`     | `tyre_temperature_center`                | °C. |
| `float`     | `tyre_temperature_right`                 | °C, right face. |
| `char[33]`  | `tyre_compound_front`                    | Compound name, replicated in all four states. |
| `char[33]`  | `tyre_compound_rear`                     | |
| `float`     | `tyre_normalized_pressure`               | 1.0 = ideal cold pressure for current compound. |
| `float`     | `tyre_normalized_temperature_left`       | 1.0 = ideal. |
| `float`     | `tyre_normalized_temperature_center`     | 1.0 = ideal. |
| `float`     | `tyre_normalized_temperature_right`      | 1.0 = ideal. |
| `float`     | `brake_normalized_temperature`           | 1.0 = ideal. |
| `float`     | `tyre_normalized_temperature_core`       | 1.0 = ideal. |
| —           | _reserved padding to 256 B_              | Reserved for future extension. |

The "left/right" axis is the **contact-patch face viewed from outside
the car**: a left-side wheel has `left = outer face, right = inner
face`; right-side wheels are mirrored. The overlay normalises this via
the `is_left = wid[1] == "L"` check.

### 7.2 Damage state (128 B) — opaque

`SMEvoDamageState` in `graphics.car_damage`. The five-zone summary in
`physics.carDamage[5]` (front / rear / left / right / centre, 0..1) is
the most usable view today. The 128-byte block likely carries
per-component damage (suspension corners, panels, aero, mechanicals);
exact field list is still TBD — see §10.

### 7.3 Pit info (64 B) — opaque

`SMEvoPitInfo` in `graphics.pit_info`. Likely contains: pit-stop state
machine (idle / requested / in-progress / serving / done), tyres /
fuel / repair flags, time remaining. Exact layout TBD.

### 7.4 Electronics & instrumentation (128 B each) — opaque

Three parallel 128-byte blocks per category:

* `electronics` / `instrumentation` — current values
* `*_min_limit` / `*_max_limit` — per-channel min and max
* `electronics_is_modifiable` — per-channel writability

The channel definition is car-dependent (engine map, brake migration,
ABS index, TC index, diff entry/mid/exit, etc.). Decoding will require
a per-car calibration map; see §10 for an investigation strategy.

### 7.5 Session state (256 B) — opaque

`SMEvoSessionState` in `graphics.session_state`. Likely carries the
detail behind the headline `session` enum: time remaining, sectors,
session phase, weather summary. Layout TBD.

### 7.6 Timing state (256 B) — opaque

`SMEvoTimingState` in `graphics.timing_state`. Likely carries lap
sector splits, optimal/theoretical lap, personal best splits, gap to
leader. Layout TBD.

### 7.7 Assists state (64 B) — opaque

`SMEvoAssistsState` in `graphics.assists_state`. Likely carries the
on/off + level for every driver aid (auto-clutch, auto-shift, ideal
line, fuel-rate, tyre-rate, etc.). Layout TBD.

---

## 8. Enumerations

Names below are sourced from the Steam guide #3707421508 and from the
overlay's own field comments. **Numeric values are not all confirmed**
from a public source — fill them in as they're verified against a
running game. Where the overlay relies on a specific value
(`pitLimiterOn != 0`, etc.), it does so by truthiness, not by named
constant — so missing numerics here don't block the overlay, they just
make this doc less useful as a study reference.

### 8.1 `ACEVO_STATUS` (`graphics.status`)

Game-loop state: typically `OFF`, `REPLAY`, `LIVE`, `PAUSE`. Numeric
values TBC.

### 8.2 `ACEVO_CAR_LOCATION` (`graphics.car_location`)

Where the player car is on the track:

* track / pit lane / pit box / on grid / approaching pit / leaving pit

Numeric values TBC.

### 8.3 `ACEVO_FLAG_TYPE` (`graphics.flag`, `graphics.global_flag`)

Marshal flags shown to the driver:

* none / blue / yellow / black / white / checkered / penalty / orange
  (mechanical) / FCY / SC / VSC

Numeric values TBC. `flag` is the flag shown specifically to the
focused car; `global_flag` is the session-wide flag.

### 8.4 `ACEVO_ENGINE_TYPE` (`graphics.engine_type`)

Powertrain category:

* internal-combustion / hybrid / electric / formula-style hybrid

Numeric values TBC. The overlay uses `engine_type` only via
`has_kers` — the bool form is enough for the chip strip.

### 8.5 `ACEVO_SESSION_TYPE` (`static.session`)

* practice / qualifying / race / hotlap / time-attack / drift /
  drag

Numeric values TBC.

### 8.6 `ACEVO_STARTING_GRIP`

* low / optimum / greenline / fast / damp / wet / flooded

Numeric values TBC.

### 8.7 `ERS_HEAT_CHARGING` (`physics.ersHeatCharging`)

* off / from kinetic only / from heat (MGUH)

Numeric values TBC.

### 8.8 `BRAKE_COMPOUND` (`physics.frontBrakeCompound`, `physics.rearBrakeCompound`)

* per-game pad+disc compound id

Numeric values TBC. Names (carbon road / carbon race / iron / etc.)
are car-dependent.

### 8.9 `P2P_STATUS` (`physics.P2PStatus`)

* idle / armed / active / cooling-down

Numeric values TBC.

---

## 9. Semantic gotchas vs Assetto Corsa 1

These are the traps that most reliably cause AC1-era code to misbehave
when re-pointed at AC EVO.

### 9.1 `tyreWear` is inverted

AC1: `tyreWear ∈ [0, 100]`, **0 = worn, 100 = fresh**, percent
remaining.
AC EVO: `tyreWear ∈ [0, 1]`, **0 = new, 1 = fully worn**.

The overlay maps to a "remaining grip" value with `tire_w = 1 -
tyreWear`. Forgetting this flip makes a fresh tyre render as "fully
worn".

### 9.2 The static block is no longer car-focused

AC1's static block carried the car spec sheet (`maxRpm`, `maxPower`,
`maxTorque`, `maxTurboBoost`, `suspensionMaxTravel`). Those are all
gone in AC EVO. The replacements are listed in §6; the most surprising
one is that there's **no static `maxRpm`** — the canonical
replacement is the per-tick `physics.currentMaxRpm`, which can vary
during a session.

### 9.3 Ride-height units are not consistent across cars

`physics.rideHeight` is metres on most cars, but some chassis (notably
older mods) publish millimetres directly. Auto-detect by magnitude:
any `|h| ≥ 1.0` is implausibly tall in metres, so treat it as mm.

### 9.3a Suspension travel sign convention is not consistent across cars

`physics.suspensionTravel` is positive metres from full extension on
most cars, but cars with active or electronically managed suspension
publish a signed displacement from a static reference instead — values
cluster around `-0.03` at rest and swing a couple of cm on kerbs.
Without `abs()`, an unsigned-only consumer never sees a positive
sample, never seeds its rolling-max calibration, and the corresponding
UI element stays pinned to whatever default it picks for "uncalibrated"
(in this overlay, dead-centre 0.5). Take the magnitude before
calibrating; both conventions then render identically.

### 9.4 `accG` ordering is `[lat, vert, long]`

AC1's `accG` was `[long, lat, vert]` in some references and `[lat,
vert, long]` in others — AC EVO's documented ordering is `[0]=lat,
[1]=vert, [2]=long`, which **matches** the graphics block's
`g_forces_x/y/z` ordering. Don't assume forward-axis-first.

### 9.5 `tc` / `abs` (physics) ≠ `tc_active` / `abs_active` / `tcInAction` / `absInAction`

* `physics.tc`, `physics.abs` (float, 0..1) — driver-set aid
  **strength** (i.e. "what level is the TC dial on"). Non-zero even
  when no power cut is happening.
* `physics.tcInAction`, `physics.absInAction` (int, 0/1) — the system
  is **right now actively cutting / modulating**. This is the precise
  signal for a "currently engaging" UI chip.
* `graphics.tc_active`, `graphics.abs_active` (bool) — a HUD-rate
  approximation; lags the physics-rate `*InAction` by up to one
  display frame.

The overlay's chip-blink uses `*InAction` for accuracy.

### 9.6 The wheel name mapping in graphics differs from physics

* Physics arrays: `[FL, FR, RL, RR]` (uniform)
* Graphics fields: `tyre_lf`, `tyre_rf`, `tyre_lr`, `tyre_rr` —
  `lf/rf/lr/rr`, **not** `fl/fr/rl/rr`.

Same wheels, just a different naming convention. Map at the boundary.

### 9.7 Compound name is replicated

`tyre_compound_front` and `tyre_compound_rear` are duplicated across
all four `SMEvoTyreState` instances. Read once (e.g. from
`tyre_lf`) — they don't differ between the four.

### 9.8 `c_bool` is one byte, `int32` flags are four

AC EVO mixes the two. The graphics block uses `c_bool` heavily, while
physics uses `int32` flags. ctypes handles this natively but it
changes the alignment math: a sequence of three `bool`s consumes 3
bytes, then an `int32` after them needs 1 byte of padding for
alignment.

---

## 10. Still-opaque areas worth investigating

Each of these is mapped as a `byte[N]` blob today — no field-level
decoding. Listed by priority (highest-value first):

1. **`graphics.session_state` (256 B)** — almost certainly carries
   session time/laps remaining, sector boundaries, weather phase.
   Comparing dumps across session-type changes (practice → qual →
   race) will quickly spot the type-discriminator field.
2. **`graphics.timing_state` (256 B)** — sector splits, theoretical
   best, gap-to-leader. Triggering a lap completion and capturing
   before/after dumps will surface the split-time fields.
3. **`graphics.car_damage` (128 B)** — likely a per-component
   breakdown deeper than the 5-zone `physics.carDamage[5]` summary.
   Drive into a wall on each axle in turn and diff to find the
   per-component slots.
4. **`graphics.electronics` + min/max/is_modifiable** — driver maps
   for engine, brake migration, diff, etc. Cycle each map up & down
   in-game, capture before/after dumps. The `*_is_modifiable` block
   should narrow the candidates.
5. **`graphics.assists_state` (64 B)** — driver-aid on/off + levels.
   Toggle aids one at a time and diff.
6. **`graphics.pit_info` (64 B)** — pit-stop state machine. Trigger a
   pit stop and dump in each phase.
7. **`graphics.instrumentation` + min/max** — car-dependent gauge
   feed. Will require per-car calibration.
8. **Numeric values for all enums in §8.** Run the dump tool with
   `--watch` while changing the relevant in-game state (e.g. cycle
   sessions) and read the int directly.

The dump tool's `--scan LO HI` and `--track-monotonic DURATION LO HI`
modes are tailored to this kind of investigation — see §11.

---

## 11. Inspecting & extending the layout

The overlay ships a dump tool that attaches to the live shared memory
and exposes three modes for layout investigation:

```bash
# Parsed view of every field the overlay knows about, refreshing every 1 s.
python -m overlay.sources.dump physics  --parsed --watch 1.0
python -m overlay.sources.dump graphics --parsed --watch 1.0
python -m overlay.sources.dump static   --parsed

# Raw hex window — first 256 bytes.
python -m overlay.sources.dump physics --bytes 256

# List every aligned float in [LO, HI].
# Pick a range tight enough to rule out padding (e.g. tyre wear lives
# in [0, 1], brake disc temp in [50, 900]). Run twice with different
# real-world states; offsets that changed are candidates.
python -m overlay.sources.dump physics --scan 0.5 1.0

# Sample over DURATION_S; report aligned floats that only ever
# decreased and stayed in [LO, HI]. True wear / monotonic counters
# pop out cleanly.
python -m overlay.sources.dump physics --track-monotonic 60 0.5 1.0
```

Recommended workflow when extending the layout for a new field:

1. Find the byte range with `--scan` (or `--track-monotonic` for
   wear-like fields), narrowing by drive-state correlation.
2. Confirm with a second `--scan` after you've changed the real-world
   value — only the matching offset should track.
3. Add the field to the corresponding ctypes struct in
   [`src/overlay/sources/ac_evo.py`](../src/overlay/sources/ac_evo.py).
   Keep `_pack_ = 4` and put the field at the documented offset; if
   inserting mid-struct, the size of the surrounding fields must stay
   correct or every offset after it shifts.
4. Add a row to this document with the offset, type, units and
   semantic notes.
5. Add a parsed line to `dump.py`'s `_print_parsed` so future
   investigators can see the value without re-instrumenting.

Tips for safe iteration:

* Map a generously-sized region (the overlay maps 4 kB / 8 kB / 2 kB).
  Mapping bigger than written is harmless; mapping smaller truncates
  trailing fields.
* Always range-clamp newly added fields in the
  `_apply_physics` / `_apply_graphics` paths. If your offset is wrong,
  the value will be visibly out of range rather than silently
  poisoning a downstream consumer.
* When in doubt, log `ctypes.sizeof(_SPageFile…)` at startup — a size
  drift vs the documented `800 / N / 210` is the fastest signal that
  a field is mis-sized.

---

## 12. References

* **Kunos, official:** [Assetto Corsa EVO — Shared Memory documentation
  (Steam guide #3707421508)](https://steamcommunity.com/sharedfiles/filedetails/?id=3707421508)
  — canonical source for field names and embedded substruct sizes.
* **Assetto Corsa 1 plugin SDK** — `SPageFilePhysics` /
  `SPageFileGraphic` / `SPageFileStatic` — the AC1-compatible prefix
  in §4.1 is essentially this struct. See the AC1 SDK package shipped
  with the original game.
* **`acevo-shared-memory` (Rust crate)** — independent
  re-implementation that confirmed several specific field types
  (`speedKmh: f32`, `rpms: i32`, `gear: i32`,
  `fuel_liter_current_quantity: f32`, etc.).
* **This repository:**
    * [`src/overlay/sources/ac_evo.py`](../src/overlay/sources/ac_evo.py)
      — ctypes structs + apply functions; the source of truth for
      this overlay.
    * [`src/overlay/sources/dump.py`](../src/overlay/sources/dump.py)
      — dump / scan / monotonic-track tool used to validate offsets.
    * [`src/overlay/telemetry.py`](../src/overlay/telemetry.py)
      — `TelemetryFrame` / `EngineData` / `WheelData` / `InputsData`,
      the abstracted shape the rest of the overlay consumes.
