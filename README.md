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
├── app.py                  # builds engine row + 2x2 wheel grid, wires telemetry
├── window.py               # transparent / always-on-top / click-through window
├── colors.py               # palette ported from lt_colors.py
├── interpolation.py        # Power, TirePsi, TireTemp interpolators
├── telemetry.py            # mock TelemetrySource (EngineData + 4x WheelData)
└── widgets/
    ├── engine_view.py      # boost bar + RPM/power bar + HP/RPM labels
    └── wheel_view.py       # tire, temps, pressure, suspension, wear, camber, height, dirt, lock, load
```

## Next steps

`telemetry.py` currently emits a synthetic `TelemetryFrame` (engine + 4 wheels) at 60 Hz. To plug in real AC Evo data, replace `TelemetrySource._tick` with a shared-memory or UDP reader that fills the same `EngineData` and per-wheel `WheelData` dataclasses — the widgets do not need to change.
