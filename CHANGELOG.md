# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.6.6] - 2026-05-19

### Added
- Auto-update on launch: hits GitHub releases API, downloads the new `LiveTelemetryEvo-<version>.exe` next to the app; skips already-downloaded files.
- Streams via a `.partial` sibling + atomic rename so a crash mid-download never leaves a half-file under the final name.
- Tray entry **Check for Updates** — 4 states: idle → `Checking...` → `Downloading...` → **Restart to Update** (launches new .exe, quits).
- Tray balloon notification when a fresh download completes; silent on already-on-disk / up-to-date / offline paths.
- New `overlay.updater` module — `UpdateChecker` worker + `UpdateController` (state machine + Qt signals the tray menu subscribes to).

### Changed
- PyInstaller spec keeps Python's stdlib SSL DLLs (`libcrypto-*` / `libssl-*`) for `urllib` HTTPS; `Qt6Network.dll` + TLS plugins stay excluded.

## [0.6.5] - 2026-05-18

### Added
- Global hotkey `Ctrl+Alt+C` toggles CSV logging on/off, mirroring the tray **Start logging** / **Stop logging** action.
- Polling and rendering decoupled across threads — source runs on its own `QThread`; UI repaints at `QScreen.refreshRate()` Hz.
- `FrameBus` transports the latest snapshot (deep-copied, under lock) to the UI and a bounded queue to the CSV writer thread.
- Tray submenu **Polling Hz** — 30 / 60 / 100 / 120 / 144 / 250; choice persisted via `settings.json`.
- Tray actions **Start logging** / **Stop logging** + **Open logs folder**; CSV writes on a dedicated thread off the polling worker.
- CSV schema auto-built from `dataclasses.fields()` on `TelemetryFrame`; new fields appear in the next session's file automatically.
- New `overlay.paths` module — `positions.json` and `logs/` sit next to the executable (CWD in dev); `LIVE_TELEMETRY_DATA_DIR` overrides.
- Countdown screen shows the **detected game name** above the digit, both centred at the detection-screen font size.
- AC EVO: KERS fields exposed (`has_kers`, `battery_temp_c`, lap-cap flags, overtake/heat/deploy/recharge maps); chips KMAX/CMAX/OT/HEAT.
- AC1: per-car ACD data plumbed to widgets — torque curve drives rev-bar tint, per-compound curves drive tyre temps, ideal psi surfaced.
- `WheelData.susp_v` flag — true when `susp_m_t` is rolling-max calibrated; suspension mid-band paints **blue** in that mode.

### Changed
- AC1 boost bar: dropped `static.maxTurboBoost` seed (unreliable on mods); right-edge now tracks rolling max of observed `turbo_boost`.
- Engine rev-bar colour: uses per-car torque curve from ACD on AC1; otherwise self-calibrates from observed `current_bhp` peak RPM.
- Boost and KERS battery bar slots fully transparent on NA / ICE-only cars — no more black stripes at the top of the engine widget.
- AC EVO ABS blink merges all three signals (`absInAction` int, `abs` intensity float, `graphics.abs_active` bool); same logic for TC.
- Per-wheel ABS heuristic on AC EVO uses documented `slipRatio` (not `wheelSlip`); threshold 0.10 → 0.03 — fires before the wheel is lost.
- Brake disk now always rendered on AC1 (neutral white base + blue ABS blink); other games keep temp-curve tint + white blink.
- AC1 + AC EVO: per-wheel body-roll height correction — per-axle `rideHeight` split by relative suspension travel across the axle.
- Engine `max_turbo_boost` default 1.2 → 0.0 so NA cars on every source hide the boost slot via the existing `> 0.05` gate.
- Default polling rate moved from CLI-only `--hz` to a persisted setting changeable at runtime; `--hz 0` (new default) uses persisted value.

### Fixed
- AC1 suspension calibration: removed 5 % headroom and 2× initial seed; static `suspensionMaxTravel` now trusted as-is. ACC/Rally too.
- AC1 tyre-wear bar: raw `tyreWear` is a grip/health value (~0.06 useful range), not a linear 0-100 % counter — remapped onto 0..1.
- KERS deploy-power on AC EVO now gates on `physics.ersIsCharging` instead of inferring direction from SoC drop; robust at SoC 0 / 1.
- `has_brake_temp=False` on AC1 no longer hides the whole brake column — icon stays for ABS/lock blink, only the `°C` label is suppressed.
- Dropped wasted writes to `brake_t` / `brake_t_norm` on AC1 (consumed only when `has_brake_temp=True`).
- Sibling project fix: `lt_wheel_info.py` body-roll correction mixed Python-API and SHM reads across wheels — now consistent on both sides.

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
- AC1 reads per-car spec data from `data.acd`: real per-axle ideal pressure, raw torque LUT for live BHP/torque, per-compound tyre thermal curves.
- AC install auto-detected via Steam registry + `libraryfolders.vdf`; honours `LT_AC_PATH` env override and falls back to the default Steam path.

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

[Unreleased]: https://github.com/albertowd/live-telemetry-ac-evo/compare/v0.6.6...HEAD
[0.6.6]: https://github.com/albertowd/live-telemetry-ac-evo/compare/v0.6.5...v0.6.6
[0.6.5]: https://github.com/albertowd/live-telemetry-ac-evo/compare/v0.6.0...v0.6.5
[0.6.0]: https://github.com/albertowd/live-telemetry-ac-evo/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/albertowd/live-telemetry-ac-evo/releases/tag/v0.5.0
