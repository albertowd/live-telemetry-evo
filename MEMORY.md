# Shared-Memory Reference

Cross-game cheat sheet for the four games this overlay supports. The
ctypes structs in `src/overlay/sources/<game>.py` are the source of
truth for the actual binary layout; this document is the
human-readable index — what each game publishes, what it doesn't, and
the non-obvious gotchas the source classes have to handle.

For the deep AC Evo field-by-field reference (offsets, value ranges,
investigation history) see
[`docs/SHARED_MEMORY.md`](docs/SHARED_MEMORY.md).

---

## 1. Overview

| Aspect | AC Evo | AC1 | ACC | AC Rally |
|---|---|---|---|---|
| **Tag namespace** | `Local\acevo_pmf_*` | `Local\acpmf_*` | `Local\acpmf_*` | `Local\acpmf_*` |
| **Physics size** | 800 B | 580 B | 800 B | 800 B |
| **Graphics size** | ~7–8 kB | 296 B | 1588 B | 1588 B |
| **Static size** | ~210 B | 684 B | 820 B | 820 B |
| **String type** | `char` (ASCII) | `wchar_t` (UTF-16) | `wchar_t` | `wchar_t` |
| **Temperature unit** | °C | °C | °C | **Kelvin** |
| **Wheel order** | FL, FR, RL, RR | FL, FR, RL, RR | FL, FR, RL, RR | FL, FR, RL, RR |
| **Gear convention** | 0=R, 1=N, 2+=fwd | 0=R, 1=N, 2+=fwd | 0=R, 1=N, 2+=fwd | 0=R, 1=N, 2+=fwd |
| **Reader source** | [`ac_evo.py`](src/overlay/sources/ac_evo.py) | [`ac1.py`](src/overlay/sources/ac1.py) | [`acc.py`](src/overlay/sources/acc.py) | [`acrally.py`](src/overlay/sources/acrally.py) |

**Tag collision.** Three of the four games publish under the *same*
shared-memory tag names (`Local\acpmf_*`). Only one of those games can
be running at a time, so collision in practice doesn't happen — but
the user has to pick the parsing layout via `--source` because the
struct layouts differ. Attaching with the wrong layout reads garbage.

---

## 2. AC Evo

* **Source:** [`src/overlay/sources/ac_evo.py`](src/overlay/sources/ac_evo.py)
* **Deep reference:** [`docs/SHARED_MEMORY.md`](docs/SHARED_MEMORY.md)
* **Tags:** `Local\acevo_pmf_physics` / `_graphics` / `_static`

### Layout

* **Physics** — 800 bytes. First 416 bytes are an AC1-compatible
  prefix; the next 384 bytes are AC Evo additions
  (`tyreContactPoint/Normal/Heading`, `brakeBias`, `localVelocity`,
  per-wheel `mz/fx/fy/slipRatio/slipAngle`, `tcInAction`/`absInAction`,
  `suspensionDamage`, `tyreTemp`, `waterTemp`, `brakeTorque`,
  `padLife`/`discLife`, `ignitionOn`/`starterEngineOn`/`isEngineRunning`,
  FFB-effect floats).
* **Graphics** — large block (multi-kB) with HUD-rate fields plus
  embedded substructs: four 256-byte `SMEvoTyreState` (per-corner
  pressure/temp + normalised values + compound name), 60-car coordinate
  table, opaque damage/pit/electronics/instrumentation/session/timing
  blocks.
* **Static** — slim, only session/track metadata. The AC1 car spec
  sheet (`maxRpm`/`maxPower`/`maxTorque`/`maxTurboBoost`/`suspensionMaxTravel`)
  is **gone** in AC Evo.

### Quirks worth remembering

* `currentMaxRpm` (int32 at physics offset 588) replaces the dropped
  static `maxRpm`. Per-tick; can vary.
* `tyreWear[4]` at offset 120 is **dead** — always reads 0.0. The
  in-game HUD wear figure is game-internal, not exposed via SHM.
* `camberRAD` has a **per-wheel local sign**: setup `−2.5°` on a
  right-side wheel reads as `+0.044 rad`, opposite of the setup tool.
  The overlay's wheel widget handles this with `is_left` sign flips.
* `physics.carDamage[5]` is **absolute** damage units (hundreds of
  joules?), not 0..1 like AC1 documented. UI must clamp.
* Per-face I/M/O temps come from the **graphics block's** TyreState
  (`tyre_temperature_left/center/right`), not the legacy
  `physics.tyreTempI/M/O` slots (those read 0 on AC Evo).
* Ride-height units vary by chassis (metres on most, mm on some
  older / modded cars). Auto-detect: `|h| >= 1.0` ⇒ mm.

---

## 3. AC1 (original Assetto Corsa)

* **Source:** [`src/overlay/sources/ac1.py`](src/overlay/sources/ac1.py)
* **Tags:** `Local\acpmf_physics` / `_graphics` / `_static`

### Layout

* **Physics** — 580 bytes. The AC1-compatible prefix
  (offsets 0..415) plus a small AC1 extension block
  (`isAIControlled`, `tyreContactPoint/Normal/Heading`, `brakeBias`,
  `localVelocity`). Ends at offset 580.
* **Graphics** — small (~296 B): lap times as `wchar_t` strings, a
  single-car coordinate, simple flag enum.
* **Static** — full car spec sheet: `maxRpm`, `maxPower`, `maxTorque`,
  `maxFuel`, `maxTurboBoost`, `suspensionMaxTravel[4]`, `tyreRadius[4]`.
  Used at session start; AC Evo dropped these.

### Quirks

* `tyreWear[4]` is `0..100` (percent **remaining**: 100 = fresh, 0 =
  bald). Opposite of AC Evo's documented (but unwritten) "fraction
  worn" scale. Converted at the apply step.
* No `padLife` / `discLife` — wear bars stay full.
* No `tcInAction` / `absInAction` — slip heuristic used.
* No `currentMaxRpm` — `static.maxRpm` is authoritative.
* No per-compound normalised pressure / temps — overlay falls back to
  interpolating the default tyre-temp curve, and uses 26 psi as a
  fixed cold ideal.

---

## 4. ACC (Assetto Corsa Competizione)

* **Source:** [`src/overlay/sources/acc.py`](src/overlay/sources/acc.py)
* **Tags:** `Local\acpmf_physics` / `_graphics` / `_static` (shares
  with AC1 — `--source acc` selects the parsing layout)
* **Spec PDF:** `ACCSharedMemoryDocumentationV1.8.12.pdf` (bundled at
  repo root)

### Layout

* **Physics** — 800 bytes, **byte-for-byte the same binary layout as
  AC Evo**. Only field-name differences: ACC's `brakePressure` at
  offset 716 vs Evo's `brakeTorque`; ACC's `gVibrations` at 792 vs
  Evo's `roadVibrations`.
* **Graphics** — 1588 bytes. Closer to AC1 (`wchar_t` lap-time
  strings) but adds rain forecasting, MFD pressures, electronics
  levels (TC/ABS/EngineMap as int selectors), 60-car coord + ID
  table, delta/estimated times, global flag state.
* **Static** — 820 bytes. AC1-shape with three additions: `isOnline`,
  `dryTyresName`, `wetTyresName`. Carries the full car spec sheet.

### Unpublished fields (in struct, but ACC never writes them)

The PDF colour-codes these as unused; the colour is lost when
extracting PDF text, so they only show up when you actually attach to
a running game and see flat zero.

| Field | Offset | Source flag set |
|---|---|---|
| `camberRAD[4]` | 168 | `has_camber = False` |
| `rideHeight[2]` | 268 | `has_ride_height = False` |
| `wheelLoad[4]` | 72 | `has_wheel_load = False` |
| `tyreTempI/M/O[4]` | 368 / 384 / 400 | core-temp fallback |

### Other quirks

* **`currentMaxRpm` is `int32`**, not `float` as the v1.8.12 PDF
  claims. Reading as float interprets the int bits as a denormal
  (~1e-41) and pegs the RPM bar at 100 % every tick.
* `brakeBias` has a per-car dash offset (PDF Appendix 4) that the
  in-car HUD adds before display — the SHM value is the raw signal,
  off by a few percent from what the dashboard shows.
* Per-wheel `lock` not published — slip-magnitude heuristic.

---

## 5. AC Rally

* **Source:** [`src/overlay/sources/acrally.py`](src/overlay/sources/acrally.py)
* **Tags:** `Local\acpmf_physics` / `_graphics` / `_static` (shares
  with AC1 and ACC — `--source acrally` selects)
* **Verification tools:**
  [`tools/probe_shm.py`](tools/probe_shm.py),
  [`tools/probe_rally_layout.py`](tools/probe_rally_layout.py),
  [`tools/inspect_acrally.py`](tools/inspect_acrally.py)

### Layout

Same 800-byte physics layout and 820-byte static / 1588-byte graphics
as ACC — confirmed via byte-by-byte probe against a running game. We
reuse ACC's ctypes structs verbatim (`from .acc import _SPageFile…`)
and only the apply step differs.

### Quirks

* **Temperatures in Kelvin.** `tyreCoreTemp`, `brakeTemp`, `tyreTemp`
  (duplicate at 696), `waterTemp`, `exhaustTemperature` all need a
  −273.15 offset to land Celsius. Helper: `_k_to_c()` in `acrally.py`,
  with a safety guard that passes values < 200 °C through unchanged
  (so a future units fix won't break the overlay).
* **`wheelLoad` IS populated** (unlike ACC). Confirmed plausible
  static loads (~3.7 kN front, ~3.1 kN rear at rest) — load circle
  works.
* **`camberRAD`, `rideHeight`, `tyreTempI/M/O` NOT populated.**
  Verified by parked vs driving diff (`tools/inspect_acrally.py diff`)
  — no plausible alternative offsets carry these signals either.
* **`padLife` / `discLife` scale unknown** — probe showed ~1.6e-5 /
  3.2e-5 at session start, very different from ACC's 0..1 fresh scale.
  The brake-wear bars' rolling-max calibration absorbs this — they
  start full and shrink correctly without us needing to know the
  absolute unit.
* **Static block populated lazily** — reads all zeros in menus,
  comes alive when entering a stage. `_apply_static` guards every
  assignment so a 0 doesn't clobber sensible defaults.
* **`currentMaxRpm` is int32** like AC Evo (same denormal trap if
  mistyped — inherited correctly from the ACC struct).
* `tyreContactNormal` *does* track road surface tilt under each
  wheel — could surface as a road-banking indicator if ever wanted;
  it's not the wheel's camber but the road's tilt at the contact.

### Source-capability flags set by `acrally.py`

```python
w.has_camber = False       # camberRAD not published
w.has_ride_height = False  # rideHeight not published
# w.has_wheel_load = True  # default — wheelLoad IS published
```

The wheel widget's `_draw_contact_patch` early-returns when either
`has_camber` or `has_wheel_load` is False, hiding the bars cleanly
without needing to fake values.

---

## 6. Cross-game conventions

### Wheel array order

All four games use `[FL, FR, RL, RR]`. Stable across the AC family.

### Gear convention

All four: `0 = Reverse, 1 = Neutral, 2..N = forward gears`. Display
gear = `gear − 1` for forward gears.

### Pedal scale

`gas`, `brake`, `clutch` are all `0.0..1.0` floats on every game.

### Steering scale

`physics.steerAngle` is **radians** (signed, negative = left) on every
game. AC Evo additionally exposes a `steer_degrees` int in the
graphics block.

### Temperature unit

| Game | Unit | Conversion |
|---|---|---|
| AC1 | °C | — |
| AC Evo | °C | — |
| ACC | °C | — |
| AC Rally | **Kelvin** | subtract 273.15 |

This is the single biggest cross-game gotcha — AC Rally is the
outlier.

### Normalised pressure / temperature

AC Evo's graphics block publishes `tyre_normalized_pressure` and
`tyre_normalized_temperature_*` (1.0 = on-target for the current
compound). The other three games don't publish these, so their
sources synthesise them by interpolating a default tyre-temp curve
and assuming a 26 psi cold ideal. The pressure widget therefore reads
as "over-pressure" on rally tyres that genuinely run higher — the raw
psi value is still correct, only the colour band is calibrated for
road / GT cars.

### Tyre wear semantic

| Game | Field | Scale |
|---|---|---|
| AC1 | `tyreWear[4]` | 0..100 = % remaining |
| AC Evo | `tyreWear[4]` | **dead — always 0** |
| ACC | `tyreWear[4]` | undocumented, assumed AC1-style % remaining |
| AC Rally | `tyreWear[4]` | undocumented, assumed AC1-style % remaining |

### `padLife` / `discLife`

| Game | Status |
|---|---|
| AC1 | not in struct |
| AC Evo | 1.0 = fresh, decreasing toward 0.0. Scale unclear; ×1000 matches the in-game HUD readout |
| ACC | same as AC Evo |
| AC Rally | published, but at a different scale (~1e-5 at session start) — meaning unclear, but the rolling-max calibration in the brake-wear widget makes it usable as a relative indicator |

### `currentMaxRpm` (int32 vs float)

| Game | Offset | Type |
|---|---|---|
| AC1 | not in struct | — |
| AC Evo | 588 | int32 |
| ACC | 588 | int32 (despite the v1.8.12 PDF claiming float) |
| AC Rally | 588 | int32 |

### Brake-disc temperature units

| Game | Unit |
|---|---|
| AC1 | °C |
| AC Evo | °C |
| ACC | °C |
| AC Rally | Kelvin |

### Contact frame fields (`tyreContactPoint/Normal/Heading`)

Present in AC1 and the 800-byte family (AC Evo / ACC / AC Rally) at
the same offsets (420 / 468 / 516). The fields encode a **road-aligned
frame** at the contact patch, not a wheel-aligned one — so they don't
substitute for the missing `camberRAD` data on ACC / AC Rally. The
road normal `Y/Z` components do react to track banking and surface
tilt under each wheel.

---

## 7. How to keep this document fresh

When a new game build changes a field:

1. Run the matching probe tool against a live session:
   * AC Rally: `python tools/inspect_acrally.py parked` then `driving`
     and `diff`.
   * Anything else: write a tweak of `tools/probe_shm.py` or use the
     existing `python -m overlay.sources.dump physics --parsed`
     (AC Evo only today; easy to extend).
2. Update the matching ctypes struct in
   `src/overlay/sources/<game>.py`. Keep `_pack_ = 4` and preserve
   the documented offsets — if you insert a field mid-struct, every
   offset after it shifts and you'll silently corrupt later reads.
3. If the change affects the "what each game publishes" matrix in
   [`README.md`](README.md), update that too.
4. Update the relevant per-game section above with the new field /
   quirk + a one-line citation of how it was verified (probe diff
   output, official doc, etc.).
5. For AC Evo deep-dive content, prefer
   [`docs/SHARED_MEMORY.md`](docs/SHARED_MEMORY.md) — it's the long
   reference and this file should stay summary-shaped.
