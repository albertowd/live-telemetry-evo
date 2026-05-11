# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.6.0] - 2026-05-11

### Added
- `--source auto` (now default) detects the running game via SHM tag namespace + process snapshot; shows "Detecting AC Environment..." until found.
- Assetto Corsa Rally support via `--source acrally` flag.
- Assetto Corsa Competizione support via `--source acc` flag.
- Original Assetto Corsa support via `--source ac1` flag.
- Tire silhouette rotates by per-wheel camber on each wheel widget.
- Contact-patch bars below each tire encoding inner/middle/outer load distribution.
- Brake-pad and disc wear bars on each wheel widget, self-calibrated against per-wheel max observed since session start.
- New `pad_w` / `disc_w` fields on `WheelData`, populated from `ph.padLife` / `ph.discLife`.
- Per-zone temperature readouts (inner / middle / outer / core) on the tyre silhouette, with luminance-based text colour.

### Changed
- `WheelData` gains `has_wheel_load`, `has_ride_height`, `has_camber` capability flags; widgets hide indicators when a source lacks the signal.
- Removed the trapezoidal camber strip; tire rotation plus contact bars replace it.
- IMO temp grid always shows the inner face on the screen-centre-facing side of the widget.
- Wheel widget logical height bumped 271 → 316 to fit the new bars and rotated tire.
- Synthetic source: right-side camber sign flipped; corner-load → camber coupling rescaled.
- Brake icon no longer shrinks with disc temperature; temperature drives only tint, wear moves to the dedicated bars.

### Fixed
- Per-face tyre temperatures now read from graphics-block TyreState on AC EVO instead of the dead legacy AC1 slots.
- Brake icon now falls back to interpolating the brake curve from raw temp when `brake_normalized_temperature` is missing.
- Tyre-wear restyled as a horizontal "Tire Wear" bar stacked under the existing Disk/Pads Wear rows; gated on `has_tire_wear` (hidden on AC EVO).
- AC1 hides the Disk/Pads Wear bars — AC1's SDK predates `padLife` / `discLife`, so the bars previously rendered a stuck-fresh value.
- AC1 hides the brake icon entirely (icon + label) since `brakeTemp` is never written by the game; lock/ABS blink disappears with it on AC1.
- Wear bars now space themselves evenly between the brake column and pressure icon — a lone visible bar pins to the top of the available height.
- AC1 contact-patch middle bar no longer collapses: new `has_pressure_norm` flag neutralises the pressure axis when the norm is a rough synthesis.

## [0.5.0] - 2026-05-01

### Added
- System-tray icon with context menu: Reset positions, Click-through toggle, Size submenu (XS/S/M/L/XL), Quit.
- Global hotkeys: `Ctrl+Alt+R` resets widget positions; `Ctrl+Alt+S` cycles widget size.
- Phase 1 — driver-aid chips on the engine widget: ESC, LC, DRS, ERS, WW, INV, LAST.
- Phase 2 — analog readouts row on the engine widget: water/oil temp, oil/fuel pressure, exhaust temp, battery V, fuel L, brake bias.
- Phase 3 — new inputs widget: pedal bars, steering bar, FFB clipping, G-meter, damage chips, tyres-off count, performance-mode label.
- 18 MDI icons rasterised into `resources/img/` via a new `fetch_icons.py` helper.
- GitHub Action that builds the Windows executable and publishes a release on every `v*` tag push.

### Changed
- Removed the floating reset and size buttons from the overlay; tray menu and hotkeys replace them.
- App icon replaced with higher-resolution **LT** branding (256×256); tray and EXE share the source file.
- Engine widget logical height grew 120 → 148 px to fit the Phase 2 readouts row.

### Fixed
- Tire temperature grid no longer shows transparent seams between colours; AA disabled for that grid.
- Tray context menu no longer flickers under the overlay; topmost-reassertion skips while a popup is active.
- `build.py` now bundles `resources/icon.png` so the tray icon resolves in the frozen build.

[Unreleased]: https://github.com/albertowd/live-telemetry-ac-evo/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/albertowd/live-telemetry-ac-evo/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/albertowd/live-telemetry-ac-evo/releases/tag/v0.5.0
