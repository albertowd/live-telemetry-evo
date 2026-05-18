# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.6.5] - 2026-05-18

### Added
- Polling and rendering decoupled across threads — telemetry source runs on a dedicated `QThread`; UI repaints at `QScreen.refreshRate()` Hz independently of poll cadence.
- New `FrameBus` transports the latest snapshot (deep-copied, under lock) to the UI and a bounded queue to the CSV writer thread.
- Tray submenu **Polling Hz** — 30 / 60 / 100 / 120 / 144 / 250; choice persisted via `settings.json`.
- Tray actions **Start logging** / **Stop logging** + **Open logs folder**; CSV writes happen on a dedicated thread to keep the polling worker off disk.
- CSV schema auto-built from `dataclasses.fields()` on `TelemetryFrame`; new dataclass fields appear in the next session's file with no maintenance step.
- New `overlay.paths` module — config (`positions.json`) and logs (`logs/`) now always sit **next to the executable** in frozen builds (or in CWD during dev). `LIVE_TELEMETRY_DATA_DIR` env var overrides both.
- Countdown screen now shows the **detected game name** above the digit, both centred at the detection-screen font size (the giant digit was visually jarring next to the detection text).
- AC EVO: full set of doc-supplied KERS fields exposed — `has_kers`, `battery_temp_c`, `kers_lap_deploy_capped` / `_charge_capped`, `ers_overtake_mode`, `ers_heat_charging`, `ers_deployment_map`, `ers_recharge_map`. Chips for KMAX / CMAX / OT / HEAT; battery temp added to the readouts strip.
- AC1: per-car ACD data plumbed through to widgets — torque curve drives the rev-bar colour band per car, per-compound thermal performance curve drives the per-wheel temperature widget, ideal pressure surfaced on `WheelData`.
- `WheelData.susp_v` flag — true when `susp_m_t` came from rolling-max calibration (vs. a static engineered limit); suspension widget mid-band paints **blue** in that mode.

### Changed
- AC1 boost bar: dropped the `static.maxTurboBoost` seed (unreliable on many mods, per the sibling LiveTelemetry plugin). Pure rolling max from observed `turbo_boost` — bar right-edge tracks the highest boost actually achieved this session.
- Engine widget rev-bar colour: uses the per-car torque curve from the ACD when available (AC1 with `data.acd`); otherwise self-calibrates from observed live `current_bhp` against the RPM the peak was seen at. The hardcoded 5500 RPM default curve no longer paints the F1 2004 (peak ~17000 RPM) bar red across most of its useful range.
- Boost bar and KERS battery bar slots now render **fully transparent** when not applicable (NA car, ICE-only car) — no more black stripes at the top of the engine widget on cars that don't have a turbo or hybrid.
- AC EVO ABS-active blink merges all three documented signals (`physics.absInAction` int, `physics.abs` intensity float, `graphics.abs_active` bool) — the per-wheel disk blink now fires on every car/build combo. Same for TC.
- Per-wheel ABS active heuristic on AC EVO uses the documented `slipRatio` (not legacy `wheelSlip`); threshold dropped from 0.10 to 0.03 — ABS holds slip *below* ~10 % by design, so the previous threshold only fired after the wheel was already lost.
- Brake disk now always rendered (was hidden on AC1 since `has_brake_temp=False`) — neutral white base for AC1 with blue ABS blink, temperature-curve tint for other games with white ABS blink so the cue contrasts against any temp colour.
- AC1 + AC EVO: per-wheel **body-roll height correction** — the per-axle `rideHeight` is now split into per-wheel values using the relative suspension travel across the axle (more-compressed side reads lower). Matches the sibling LiveTelemetry plugin's correction.
- Engine `max_turbo_boost` default flipped 1.2 → 0.0 so naturally aspirated cars on every source correctly hide the boost slot via the existing `> 0.05` visibility gate.
- Default polling rate moved from a CLI-only `--hz` to a persisted setting changeable at runtime; `--hz 0` (new default) uses the persisted value.

### Fixed
- AC1 suspension calibration: removed the 5 % headroom inflation and 2× initial seed. With static `suspensionMaxTravel` trusted as-is, the "bottoming-out" red signal now fires correctly on stiff F1 suspension that legitimately brushes the limit under aero load. Same fix applied to ACC, AC Rally; AC EVO is always dynamic since the static block dropped the field.
- AC1 tire-wear bar: the raw `tyreWear` signal is a grip/health value (climbs through warm-up, then drifts down across only ~0.06 normalised units of useful range), not a linear 0-100 % counter. Remap the 0.06 window below the fresh peak (pinned at 1.0 so a mid-session start doesn't misread the current value as fresh) onto 0..1 — the bar now actually moves across a stint instead of pegging at ~99 %.
- KERS deploy-power derivation on AC EVO now gates on the explicit `physics.ersIsCharging` flag instead of inferring direction from a SoC drop — fires correctly even when SoC is pinned at 0 or 1, and no longer fragile under single-precision quantisation jitter.
- `WheelData.has_brake_temp=False` on AC1 no longer hides the entire brake column; the icon stays so the ABS / lock blink remains visible, and the `°C` label is the only thing suppressed (matches the actual missing-signal — AC1's `brakeTemp` slot is never written by the game).
- Dropped wasted writes to `brake_t` / `brake_t_norm` on AC1 (consumed only when `has_brake_temp=True`).
- Sibling project fix: identified a source-mixing bug in `lt_wheel_info.py` where the body-roll height correction read this wheel's travel from the Python API but the opposite wheel's travel from shared memory. The Python-API guard the surrounding code applied got bypassed for the diff — most visible on F1-style cars with tiny absolute travel. Now consistent on both sides.

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

[Unreleased]: https://github.com/albertowd/live-telemetry-ac-evo/compare/v0.6.5...HEAD
[0.6.5]: https://github.com/albertowd/live-telemetry-ac-evo/compare/v0.6.0...v0.6.5
[0.6.0]: https://github.com/albertowd/live-telemetry-ac-evo/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/albertowd/live-telemetry-ac-evo/releases/tag/v0.5.0
