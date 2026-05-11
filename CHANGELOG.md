# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Tire rotates by camber** on each wheel widget — silhouette,
  IMO grid and dirt overlay tilt around the tire centre (×2 visual
  amplification), consistent across left and right wheels.
- **Contact-patch bars** below each tire: three white bars whose
  heights encode a `camber × pressure × load` heuristic for which
  lateral face (inner / middle / outer) is in contact. The bars
  themselves are the ground reference.

### Changed
- Removed the old trapezoidal camber strip — the tire rotation plus
  the contact bars replace it.
- IMO temp grid now always shows the **inner** face on the
  screen-centre-facing side of the widget (left wheels were inverted).
- Wheel widget logical height bumped 271 → 316 to fit the new bars
  and give the rotated tire top breathing room.
- Synthetic source: right-side camber sign flipped to mirror the real
  game; corner-load → camber coupling rescaled to realistic values.

## [0.5.1] - 2026-05-09

### Added
- **Brake-pad / disc wear bars** on each wheel widget. Two horizontal bars
  (titled `Disk Wear` / `Pads Wear`) sit between the brake-temperature
  label and the pressure icon, fill left → right with green / yellow / red
  colour bands, and self-calibrate against the per-wheel max observed
  since session start (AC EVO's `padLife` / `discLife` raw scale isn't
  pinned down, so "max seen" stands in for "fresh").
- New `pad_w` / `disc_w` fields on `WheelData`, populated from
  `ph.padLife[idx]` / `ph.discLife[idx]`. The synthetic source decays
  both proportional to brake input (pad faster than disc, fronts faster
  than rears) so dev mode animates the bars.
- **Per-zone temperature readouts** on the tyre silhouette: inner /
  middle / outer values in the top bumps and the core value in the
  centre. Text colour flips black or white based on the patch's Rec. 601
  luminance, so the reading stays legible across the cold-blue → ideal-
  green → hot-red sweep.

### Changed
- Brake icon no longer clip-shrinks with disc temperature. Temperature
  drives only the icon's tint — the icon is drawn at full size and wear
  is now conveyed by the new dedicated bars instead of being conflated
  with a transient temperature signal.

### Fixed
- Per-face tyre temperatures (`tire_t_i` / `tire_t_m` / `tire_t_o`) read
  as 0 °C on AC EVO. The legacy AC1 `ph.tyreTempI/M/O` slots are dead on
  EVO; the values now come from the graphics-block TyreState
  (`tyre_temperature_left/center/right`) with the same left/right side
  mirroring already used for the normalised temps.
- Brake icon stayed green at any disc temperature on AC EVO — the source
  only updated `brake_t_norm` when `brake_normalized_temperature > 0.0`,
  and EVO leaves that at 0.0 for many cars / states. The field stuck at
  its default 1.0 ("ideal") and the curve-driven blue / green / red
  banding never kicked in. The source now falls back to interpolating
  the brake curve from the raw `brake_t` whenever the live norm is
  missing, matching the synthetic-source behaviour.

## [0.5.0] - 2026-05-01

### Added
- System-tray icon with a context menu: **Reset positions**, **Click-through**
  toggle, **Size** submenu (XS/S/M/L/XL), **Quit**. Left-click and right-click
  both surface the menu; tray icon shares the same source PNG as the EXE icon.
- Two new global hotkeys (Win32 `RegisterHotKey`, fire even with the game
  focused): `Ctrl+Alt+R` resets every widget to its default position;
  `Ctrl+Alt+S` cycles widget size. Each tray entry shows its shortcut.
- **Phase 1 — driver-aid chips** on the engine widget: `ESC`, `LC` (launch
  control), `DRS` (bright when deployed, dim when only available), `ERS`
  (charging), `WW` (wrong way), `INV` (lap invalidated), `LAST` (final lap).
  Strip auto-compresses width when many fire at once.
- **Phase 2 — analog readouts row** on the engine widget: water / oil temp,
  oil / fuel pressure, exhaust temp, battery V, fuel L, brake bias %F. Cells
  hide automatically when the source publishes nothing for the current car.
- **Phase 3 — new inputs widget** (top-centred): pedal bars (THR/BRK/CLU/HBR),
  signed steering bar, FFB clipping indicator, G-meter circle with reference
  rings, five-zone damage chips, tyres-off-track count, performance-mode
  label. Hidden by default in 0.5.0 — `Ctrl+Alt+R` brings it on screen.
- 18 MDI icons rendered into `resources/img/` via a new `fetch_icons.py`
  helper (downloads SVGs from Iconify, rasterises to 256×256 PNGs through
  `QSvgRenderer` — no extra dependencies).
- GitHub Action (`.github/workflows/release.yml`) that builds the Windows
  executable and publishes a release on every `v*` tag push.

### Changed
- Removed the floating reset and size buttons from the overlay itself —
  their job is now done by the tray menu plus the new hotkeys.
- App icon replaced with the higher-resolution **LT** branding (256×256);
  tray and EXE share the same source file so they always match.
- Engine widget logical height grew from 120 to 148 px to fit the new
  Phase 2 readouts row; the bar still anchors to the bottom of the screen.

### Fixed
- Tire temperature grid showed thin transparent seams between colours at
  fractional logical coordinates (anti-aliased edges of adjacent rects).
  AA is now disabled for that grid; aliased fills snap flush.
- Tray context menu was being covered by the overlay every second when the
  topmost-reassertion timer fired. The reassertion now skips while any
  popup widget is active.
- `build.py` now bundles `resources/icon.png` into the EXE so the tray
  icon resolves at runtime (previously only the embedded `.ico` was set,
  so the tray was iconless in the frozen build).

[Unreleased]: https://github.com/albertowd/live-telemetry-ac-evo/compare/v0.5.1...HEAD
[0.5.1]: https://github.com/albertowd/live-telemetry-ac-evo/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/albertowd/live-telemetry-ac-evo/releases/tag/v0.5.0
