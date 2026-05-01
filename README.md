# AC Evo Telemetry Overlay

![Overlay running on top of AC Evo](https://raw.githubusercontent.com/albertowd/live-telemetry-ac-evo/main/resources/preview.webp)

Transparent, always-on-top desktop overlay that displays live engine and per-wheel
telemetry on top of **Assetto Corsa Evo** (or any other window). Built with **PySide6**
and ported from the original AC1 *LiveTelemetry* plugin.

The overlay reads AC Evo's three named shared-memory blocks (`Local\acevo_pmf_physics`,
`…_graphics`, `…_static`) when the game is running, or falls back to a synthetic data
generator for development and screenshots.

---

## Quick start

### Option A — run the prebuilt executable

1. Build it once with `python build.py` (see [Building a redistributable
   executable](#building-a-redistributable-executable)).
2. Double-click `dist\ACEvoOverlay-<version>.exe`.
3. Start AC Evo. The overlay attaches automatically as soon as the game publishes
   shared memory; until then it shows the synthetic mock so you can see the layout.
4. Use `Ctrl+Alt+L` to unlock for repositioning, `Ctrl+Alt+Q` to quit.

### Option B — run from source

The project uses a local virtual environment so it does not touch the system Python.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m overlay
```

Or use `run.bat`, which uses the venv's Python directly (no activation needed).

### Command-line flags

```bash
python -m overlay --source synthetic   # default — animated mock data, no game required
python -m overlay --source ac-evo      # live AC Evo shared memory (game must be running)
python -m overlay --hz 120             # sample rate in Hz (default: 60)
```

The `ac-evo` source attaches via Win32 `OpenFileMappingW`. If the game isn't running it
polls quietly once a second and connects automatically when AC Evo starts publishing.

---

## Controls

### Hotkeys (registered globally)

| Shortcut     | Action                                                              |
| ------------ | ------------------------------------------------------------------- |
| `Ctrl+Alt+L` | Toggle click-through (lock / unlock the overlay for repositioning). |
| `Ctrl+Alt+R` | Reset every widget to its default position and visibility.          |
| `Ctrl+Alt+S` | Cycle widget size: `XS → S → M → L → XL → XS …`. Persists.          |
| `Ctrl+Alt+Q` | Quit the overlay.                                                   |

These use Win32 `RegisterHotKey`, so they fire even while the game has focus.

### System-tray icon

The overlay registers an icon in the Windows notification area. Left- or right-click
it to open a menu mirroring the hotkeys above:

- **Reset positions** — restores the default layout.
- **Click-through** — checkable; reflects current state.
- **Size** — submenu with `XS / S / M / L / XL` as a radio group.
- **Quit** — exits the overlay.

Each menu entry shows its hotkey alongside the label.

### Mouse — when the overlay is unlocked (click-through OFF)

| Action                    | Result                                              |
| ------------------------- | --------------------------------------------------- |
| Drag a widget             | Move it; position is persisted across sessions.     |
| Click the `×` on a widget | Hide it; the hidden state persists across sessions. |

If you hide everything, `Ctrl+Alt+R` (or *Reset positions* in the tray) brings the
whole layout back.

### Startup behaviour

- **Click-through is ON by default** so the overlay never steals mouse input from the
  game. Toggle it off (`Ctrl+Alt+L`) only when you want to drag widgets.
- A 5-second countdown is drawn full-screen before the telemetry widgets reveal. The
  source still feeds frames during the countdown so values are live the moment the
  widgets appear.
- Widgets you hid in a previous session stay hidden; `Ctrl+Alt+R` brings them back.

---

## What the HUD shows

All widgets are painted in a logical coordinate system and scaled per screen, so the
absolute pixel sizes here are reference values for the multiplier-1.0 baseline (1440p
nominal). The overlay picks a multiplier from the screen's vertical resolution and
multiplies it by your size factor (XS = 0.5 … XL = 1.5).

### Engine bar (top-centred at the bottom of the screen)

```
┌─────────────────────────── boost bar ───────────────────────────┐
│                                                                 │
├─────────────────────────── RPM bar ─────────────────────────────┤
│                                                                 │
│   1234 HP                  3   148 km/h                7820 RPM │
│         PIT  TC  ABS  ESC  LC  DRS  ERS  WW  INV  LAST          │
│  WAT 92°  OIL 110°  OPR 4.5 bar  EXH 720°  FUEL 42L  BIAS 55%F  │
└─────────────────────────────────────────────────────────────────┘
```

| Element     | What it shows                                                                         |
| ----------- | ------------------------------------------------------------------------------------- |
| Boost bar   | Current turbo boost as a fraction of `max_turbo_boost` (white below 90 %, green above). Hidden on naturally-aspirated cars (`max_turbo_boost ≤ 0.05`). The numeric label is `bar`, two-pass painted so it's legible over both filled and empty regions. |
| RPM bar     | Engine speed as a fraction of redline. Uses `rpm_percent` from the graphics block when available, otherwise `rpm / max_rpm`. Colour normally tracks the power curve (white → blue → green at peak power → red past peak), but a **shift-up hint forces red** (treat as a shift light) and a **shift-down hint forces blue**. |
| HP label    | `current_bhp` from the graphics block when available; otherwise the synthesised power curve interpolated at the current RPM, scaled by `(1 + boost)` as a rough boosted-output approximation. |
| Gear label  | `R` (reverse), `N` (neutral) or the forward gear number — matches AC's `0=R, 1=N, 2+=forward` convention. Speed in km/h sits next to it. |
| RPM label   | Live engine speed in RPM.                                                             |
| Aid chips   | Up to 10 driver-aid / status chips, **only rendered while their condition is true**. The strip auto-compresses when many fire at once so nothing clips: `PIT` (yellow, pit limiter), `TC` (green, traction control — bright when cutting, dim when armed-but-idle), `ABS` (blue, same scheme), `ESC` (red, stability control), `LC` (green, launch control), `DRS` (blue, **bright when deployed**, dim when only available), `ERS` (yellow, kers/battery charging), `WW` (red, wrong way), `INV` (red, lap invalidated by a cut), `LAST` (white, final lap). |
| Readouts    | Bottom row: `WAT` water temp °C, `OIL` oil temp °C, `OPR` oil pressure bar, `FPR` fuel pressure bar, `EXH` exhaust temp °C, `BAT` battery V, `FUEL` litres, `BIAS` brake bias (%F front). Cells whose source publishes nothing for the current car are hidden so the strip shows only live values. |

#### Icon vs. text fallback (engine bar)

Each chip and readout cell is keyed to an [MDI](https://pictogrammers.com/library/mdi/)
icon name. Drop a white-on-transparent PNG named `<icon-name>.png` (e.g.
`car-brake-abs.png`, `water-thermometer.png`) into `resources/img/` and the cell
renders as a tinted icon; until the file exists it falls back to the text label.
Icon-name mapping:

| Cell | MDI icon | Cell | MDI icon |
| --- | --- | --- | --- |
| PIT | `car-speed-limiter` | WAT | `water-thermometer` |
| TC  | `car-traction-control` | OIL | `oil-temperature` |
| ABS | `car-brake-abs` | OPR | `oil-level` |
| ESC | `car-esp` | FPR | `gas-station` |
| LC  | `rocket-launch` | EXH | `smoke` |
| DRS | `car-cruise-control` | BAT | `car-battery` |
| ERS | `battery-charging` | FUEL | `fuel` |
| WW  | `alert` | BIAS | `car-brake-parking` |
| INV | `flag-remove` | | |
| LAST | `flag-checkered` | | |

### Inputs widget (top-centred, between the front wheels)

Phase 3 widget: driver inputs + dynamics + car state. Sits at the top of
the screen, mirroring the engine bar's bottom-centre slot.

```
┌────────────────────────────────────────────────────────────────────┐
│ THR ▓▓▓▓▓▓▓▓▓▓░  85%                       ┌─────────┐             │
│ BRK ░░░░░░░░░░░   0%                       │   ⊕ ●   │  G-meter    │
│ CLU ░░░░░░░░░░░   0%                       │  rings  │             │
│ HBR ░░░░░░░░░░░   0%                       └─────────┘             │
│ STR ───•─────── -162°                          1.61 g              │
│ FFB ▓▓▓▓▓▓▓▓▓▓▓  97%      DMG  F R L I C   OUT 2  MODE QUAL        │
└────────────────────────────────────────────────────────────────────┘
```

| Element        | What it shows                                                                |
| -------------- | ---------------------------------------------------------------------------- |
| THR / BRK / CLU / HBR | Pedal % as horizontal bars (green / red / yellow / white).            |
| STR            | Steering input as a signed bar from the centre (left = blue fill leftward, right = blue fill rightward); numeric readout in degrees. |
| FFB            | Force-feedback strength 0..1; turns red at ≥ 95 % to flag clipping.          |
| G-meter        | Circle with 0.5 g + 1.0 g reference rings. Dot at `(g_lat, -g_long)` scaled to a 1.7 g rim. **Green** below 1 g combined, **yellow** 1–1.5 g, **red** above. The combined-g magnitude is shown numerically below the meter. |
| DMG chips      | Five zones (F = front, R = rear, L = left, I = right, C = centre). Dim outline below 5 %, **yellow** to ~25 %, **red** above. The whole row is hidden when the car is undamaged. |
| OUT chip       | Number of tyres currently off-track (0 hides the chip).                      |
| MODE label     | Performance-mode preset (e.g. `WET`, `QUAL`); hidden when empty.             |

Right-side wheels are mirrored so the *outer* edge always faces the screen edge. The
icons are tinted PNGs cached as alpha masks and re-coloured every frame.

```
┌──────────────────────────────────────────┐ FL
│ wear │  brake │   tire silhouette  │ susp│
│ bar  │   60×60│   + temp grid      │  bar│
│      │        │   + dirt overlay   │     │
│      │        │   + load circle    │     │
│      │  pres- │                    │ ride│
│      │  sure  │                    │ hght│
│      │  60×60 │   ── camber strip ─│     │
└──────────────────────────────────────────┘
       80 °C       (FL / SOF)         32 mm
       26.4 psi
```

| Element               | What it shows                                                                                                                                                                                                                                |
| --------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Tire silhouette       | Tinted by **composite tyre temperature**: 75 % core + 25 % average of inner/middle/outer. The colour tracks the per-compound normalised temperature when the source publishes it (1.0 = on target), so the band stays correct across compounds. |
| Inner / middle / outer temp grid | Three columns, each with a top and a bottom rectangle, coloured by that face's temperature. Lets you spot a single-edge overheat (camber too aggressive) or one-corner cooking. |
| Core temp band        | Central horizontal band over the silhouette, alpha 85 %, coloured by core temperature. |
| Dirt overlay          | Brown rectangle that grows from the bottom of the silhouette. `tire_d` is clamped to 0…4; full overlay = wheel is filthy. |
| Tyre-load circle      | White ring centred on the silhouette. Diameter scales linearly with vertical wheel load: **0.049 px / N**, clamped to 40…256 px. A typical static corner load (~3 000 N) fills the inner half; the ring saturates around ~5 200 N. |
| Camber strip          | A trapezoidal strip below the silhouette. The horizontal edge stays flat; the top edge tilts proportional to `tan(camber_rad) × strip_width`, so heavy negative camber leans inward and positive camber leans outward. Visual only — there is no numeric readout. |
| Suspension bar        | Tinted suspension graphic on the **outer** side of the wheel. The colour band reflects how close to the bump-stops you are: white = mid-travel, **yellow** outside ±10 % of the calibrated max, **red** outside ±5 %. The inner fill is the original AC plugin convention — the bar **fills at full extension and shrinks as the suspension compresses** (height ∝ `1 − travel_ratio`). Counter-intuitive vs a typical "load grows" gauge but kept for parity. The max travel auto-calibrates from observation, since AC Evo no longer publishes a per-car `suspensionMaxTravel`. |
| Wear bar              | Thin vertical bar on the **inner** side. AC Evo's wear scale is small (1.0 = fresh, ~0.85 = significantly worn), so the bar maps the **0.85 → 1.00 range** to its full height. Green > 0.95, yellow > 0.90, red below. |
| Brake icon (top-inner)| Tinted by **brake disc temperature** (curve peak ≈ 400 °C; cold below ~150 °C and hot above ~600 °C reduce stopping power). Per-wheel **lock** triggers a 0.5 s yellow blink; **ABS modulating on this wheel** triggers a continuous blue blink. The °C label below the icon stays in the temperature-tint colour for legibility. |
| Pressure icon (mid-inner) | Tinted by **normalised pressure** (1.0 = ideal cold pressure for the current compound). Bands sit tight: green within ±0.02 of 1.0, lerp through 0.01, then solid blue (under) or red (over). The label is the **raw psi value**. |
| Ride-height icon (outer-bottom) | White most of the time. Drops to red for 0.5 s when the height falls below 20 mm (bottoming-out warning). Label in mm. AC Evo cars publish per-axle ride height in metres for some cars and millimetres for others — the source auto-detects: any value with `|height| ≥ 1.0` is treated as mm, otherwise it's metres × 1000. |
| Wheel ID + compound   | `FL` / `FR` / `RL` / `RR` and, below it, the first three uppercase chars of the active compound (`SOF`, `MED`, `HAR`, `INT`, `WET`). |

### Colour reference (when in doubt, glance here)

| Colour | RPM bar          | Tyre temp / pressure         | Brake temp / wear / suspension |
| ------ | ---------------- | ---------------------------- | ------------------------------ |
| white  | safely below peak | (transitional only)          | mid-range / OK                 |
| blue   | shift-down hint, or near-peak just before the redline | below ideal (cold tyre / underpressure / cold brake) | (only as the ABS blink)        |
| green  | at peak power    | on target (within ideal band) | excellent / pad in window       |
| red    | past peak (or shift-up hint) | above ideal (hot / overpressure / hot brake) | bad — bottoming, locked, fully worn, on the bump-stops |
| yellow | —                | —                            | warning band (close to limit, lock blink, pit limiter) |
| brown  | —                | —                            | dirt overlay                    |

---

## Per-frame data fields

Each `TelemetryFrame` carries:

**Engine** — `rpm`, `max_rpm`, `gear`, `speed_kmh`, `turbo_boost`, `max_turbo_boost`,
`abs_level`, `tc_level`, `pit_limiter`, plus AC Evo graphics-block fields when
available: `current_bhp`, `current_torque`, `rpm_percent`, `tc_in_action`,
`abs_in_action`, `shift_up_hint`, `shift_down_hint`.
Phase 1 chips (booleans): `esc_active`, `launch_active`, `drs_available`,
`drs_enabled`, `ers_charging`, `wrong_way`, `valid_lap`, `last_lap`.
Phase 2 readouts (zero = not published, hidden): `water_temp_c`, `oil_temp_c`,
`oil_pressure_bar`, `fuel_pressure_bar`, `exhaust_temp_c`, `battery_voltage`,
`fuel_liters`, `brake_bias` (0..1, fraction toward the front axle).

**Inputs** (Phase 3) — `throttle`, `brake`, `clutch`, `handbrake` (0..1);
`steering` (-1..1), `steering_deg` (signed degrees), `ffb` (0..1, 1.0 = clipping);
`g_lat`, `g_long`, `g_vert` (g); `damage` (5-tuple, 0..1 per body zone); `tyres_out`
(0..4); `performance_mode` (string).

**Wheel (×4)** — `tire_t_c`/`tire_t_i`/`tire_t_m`/`tire_t_o` (core / inner / middle /
outer °C) plus matching per-compound `tire_t_norm_*` (1.0 = ideal), `tire_p` (psi) +
`tire_p_norm`, `tire_l` (vertical load, N), `tire_w` (wear, 1.0 = fresh), `tire_d`
(dirt 0…4), `camber` (rad), `susp_t` / `susp_m_t` (current / calibrated-max travel,
m), `height` (mm), `brake_t` (°C) + `brake_t_norm`, `lock`, `abs_active`, `compound`.

The fields are populated either by the synthetic generator (animated coherent
"lap"), or read straight from AC Evo's three shared-memory blocks. Wheel order
in every per-wheel array is `[FL, FR, RL, RR]` — both AC1 and AC Evo agree on this.

A semantic gotcha: AC Evo's `tyreWear` is **0.0 = new, 1.0 = fully worn** (opposite of
AC1's "% remaining"). The source flips it so the overlay can keep treating `tire_w`
as "remaining grip".

---

## Persistence

State is stored as a single JSON file at
`%APPDATA%\LiveTelemetryAcEvo\Overlay\positions.json`
(`QStandardPaths.AppConfigLocation`).

Schema:

```json
{
  "engine": { "x": 700, "y": 16, "visible": true },
  "FL":     { "x": 16,  "y": 200 },
  "size_index": 2
}
```

A position is honoured on next launch only if the widget would land fully on screen
at the current resolution; otherwise it falls back to the layout default. *Reset*
(hotkey `Ctrl+Alt+R` or the tray entry) wipes the telemetry-widget entries; the
persisted `size_index` is preserved and only changes via `Ctrl+Alt+S` or the tray.

---

## Building a redistributable executable

```powershell
.venv\Scripts\python build.py
```

Reads the version from `pyproject.toml`, converts `resources/img/icon.png` to
`resources/icon.ico` (Pillow), then invokes PyInstaller in one-file windowed mode:

- Output: `dist/ACEvoOverlay-<version>.exe`
- Bundles the `resources/img` directory so the icon PNGs ship with the binary.
- Removes the one-folder fallback PyInstaller drops alongside the onefile binary.

Requires the dev extras:

```bash
pip install -r requirements-dev.txt
```

---

## Inspecting live shared memory

When the game is running, dump real bytes / parsed fields to verify the struct
layout or hunt unknown offsets:

```bash
python -m overlay.sources.dump physics --parsed
python -m overlay.sources.dump graphics --parsed
python -m overlay.sources.dump static  --parsed --watch 1.0
python -m overlay.sources.dump physics --bytes 256                # raw hex window
python -m overlay.sources.dump physics --scan 0.5 1.0             # aligned floats in [LO,HI]
python -m overlay.sources.dump physics --track-monotonic 60 0.5 1.0   # 60 s, find wear-like fields
```

The `--scan` and `--track-monotonic` modes are useful when AC Evo extends the layout
and the overlay needs to be re-pointed at a moved field.

---

## Project layout

```
src/overlay/
├── __main__.py                # `python -m overlay` entry point
├── app.py                     # CLI parsing, layout + size cycling, ties widgets to the source
├── window.py                  # frameless / translucent / always-on-top window + Win32 hotkeys
├── tray.py                    # system-tray icon + context menu (reset / click-through / size / quit)
├── layout.py                  # screen-size → multiplier and corner placements
├── settings.py                # JSON-backed positions / visibility / size persistence
├── colors.py                  # palette ported from lt_colors.py
├── fonts.py                   # explicit font family chain
├── interpolation.py           # Power, TirePsi, TireTemp interpolators
├── resources.py               # PNG load + scaled-mask cache + tint helper
├── telemetry.py               # data shapes (TelemetryFrame / EngineData / WheelData)
├── sources/
│   ├── base.py                # TelemetrySource (Qt object emitting `frame`)
│   ├── synthetic.py           # mock data generator (default)
│   ├── ac_evo.py              # AC Evo shared-memory reader (Win32 OpenFileMappingW)
│   └── dump.py                # `python -m overlay.sources.dump` for SHM debugging
└── widgets/
    ├── countdown.py           # full-screen 5 s countdown shown at startup
    ├── draggable.py           # base widget — drag, click, close button
    ├── engine_view.py         # boost bar + RPM/power bar + HP/RPM labels + aid chips + analog readouts
    ├── inputs_view.py         # pedals + steering + FFB + G-meter + damage / tyres-out / mode
    └── wheel_view.py          # tire, temps, pressure, suspension, wear, camber, height, dirt, lock, load
```

Widgets only know about `TelemetryFrame` shapes, so swapping the synthetic source for
the live AC Evo reader (or any future source) does not touch any UI code.

---

## Caveats

- **AC Evo's struct layout is still partially undocumented.** The structs in
  `sources/ac_evo.py` are seeded from the AC1 SDK plus the publicly confirmed AC Evo
  field names, with each value range-clamped to keep a wrong offset visible-but-bounded
  rather than crashy. If a field looks suspicious for a particular car, run the dump
  tool against a live session and confirm offsets before adjusting.
- **Click-through default is ON.** This is deliberate — a full-screen overlay must
  not steal mouse input from the game. Toggle it off with `Ctrl+Alt+L` before trying
  to drag a widget.
