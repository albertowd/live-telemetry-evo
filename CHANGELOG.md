# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/albertowd/live-telemetry-ac-evo/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/albertowd/live-telemetry-ac-evo/releases/tag/v0.5.0
