# AC Evo Telemetry Overlay

Cross-platform transparent, always-on-top desktop overlay for live telemetry charts. Built with **PySide6** + **PyQtGraph**.

## Features

- Frameless, translucent window that draws over other apps (including fullscreen-windowed games)
- Always-on-top
- Click-through toggle (`Ctrl+Alt+L`) so the overlay does not steal mouse input from the game
- Drag-to-move when click-through is off
- 60 Hz live chart with a numpy ring buffer (no per-frame allocations)

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
├── __main__.py     # python -m overlay entry
├── app.py          # wires window + chart + telemetry source
├── window.py       # transparent / always-on-top / click-through window
├── chart.py        # PyQtGraph ring-buffer plot
└── telemetry.py    # synthetic data source (replace with AC Evo SHM reader)
```

## Next steps

`telemetry.py` currently emits a synthetic signal. To plug in real AC Evo data, replace `TelemetrySource._tick` with a reader for the game's shared-memory layout (or UDP feed) and emit the same `sample` dict shape (`throttle`, `brake`, `speed`, ...).
