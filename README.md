# AC Evo Telemetry Overlay

Cross-platform transparent, always-on-top desktop overlay for live telemetry charts. Built with **PySide6** + **PyQtGraph**.

## Features

- Frameless, translucent window that draws over other apps (including fullscreen-windowed games)
- Always-on-top
- Click-through toggle (`Ctrl+Alt+L`) so the overlay does not steal mouse input from the game
- Drag-to-move when click-through is off
- Engine bar (RPM/power + boost) + 2x2 wheel grid (FL/FR/RL/RR), ported from the original AC `LiveTelemetry` plugin
- Per-wheel widgets: tire silhouette tinted by core temp, inner/middle/outer temp grid, pressure, suspension travel, wear, camber strip, ride-height, dirt overlay, lock/ABS indicator, dynamic load circle

## Setup

The project uses a local virtual environment so it does not touch the system Python.

### Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

From an activated venv:

```bash
python -m overlay
```

Or use the launchers (which use the venv's Python directly, no activation needed):

- Windows: `run.bat`
- macOS/Linux: `./run.sh`

### Telemetry source

```bash
python -m overlay --source synthetic   # default: mock data, no game required
python -m overlay --source ac-evo      # live AC Evo shared memory (Windows + game running)
```

The AC Evo source attaches to the three named shared-memory blocks the game publishes (`Local\acevo_pmf_physics`, `…_graphics`, `…_static`) via Win32 `OpenFileMappingW`. If the game isn't running, the source polls quietly and connects automatically when AC Evo starts publishing.

### Inspecting live shared memory

When the game is running, dump real bytes/parsed fields to iterate on the struct layout:

```bash
python -m overlay.sources.dump physics --parsed
python -m overlay.sources.dump physics --bytes 256
python -m overlay.sources.dump static --parsed --watch 1.0
```

## Hotkeys

| Shortcut       | Action                              |
| -------------- | ----------------------------------- |
| `Ctrl+Alt+L`   | Toggle click-through (lock overlay) |
| `Ctrl+Alt+Q`   | Quit                                |

When click-through is OFF, drag the overlay with the left mouse button.

## Layout

```
src/overlay/
├── __main__.py             # python -m overlay entry
├── app.py                  # CLI parsing, screen-relative layout, ties widgets+source
├── window.py               # transparent / always-on-top / click-through window
├── layout.py               # screen-size -> multiplier + corner placements
├── colors.py               # palette ported from lt_colors.py
├── fonts.py                # explicit font family chain
├── interpolation.py        # Power, TirePsi, TireTemp interpolators
├── resources.py            # PNG load + scaled-mask cache + tint helper
├── telemetry.py            # data shapes (TelemetryFrame / EngineData / WheelData)
├── sources/
│   ├── base.py             # TelemetrySource (Qt object emitting `frame`)
│   ├── synthetic.py        # mock data generator (default)
│   ├── ac_evo.py           # AC Evo shared-memory reader (Win32 OpenFileMappingW)
│   └── dump.py             # python -m overlay.sources.dump for SHM debugging
└── widgets/
    ├── engine_view.py      # boost bar + RPM/power bar + HP/RPM labels
    └── wheel_view.py       # tire, temps, pressure, suspension, wear, camber, height, dirt, lock, load
```

## Next steps

The AC Evo source (`sources/ac_evo.py`) is wired up but its struct layout is **best-effort** — seeded from the AC1 SDK plus the publicly confirmed AC Evo field names (`speedKmh`, `rpms`, `gear`, etc.). Once the game is running, use `python -m overlay.sources.dump physics --parsed` to verify which fields land at which offsets, then adjust `_SPageFilePhysics` / `_SPageFileStatic` in `sources/ac_evo.py`. The widgets and synthetic source do not need to change.
