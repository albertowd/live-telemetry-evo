"""Persist per-widget positions and runtime preferences across sessions.

Stored as a single JSON file resolved via :mod:`overlay.paths` — next to
the bundled ``.exe`` in release builds, in the current working directory
during dev runs (see ``overlay.paths`` for the full resolution policy).
Schema is intentionally trivial — one entry per widget id with absolute
screen-pixel coords, plus a handful of top-level scalars (``size_index``,
``polling_hz``):

.. code-block:: json

    {
      "engine": {"x": 700, "y": 16},
      "FL":     {"x": 16,  "y": 200},
      "size_index": 2,
      "polling_hz": 60
    }

The validators below survive a corrupt or partial file by dropping the
bad entries rather than failing the whole load.
"""
from __future__ import annotations

import json

from .paths import settings_path


def _positions_path():
    return settings_path()


def _read() -> dict:
    path = _positions_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    try:
        _positions_path().write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def load_positions() -> dict[str, tuple[int, int]]:
    """Return ``{widget_id: (x, y)}`` for any saved positions, ``{}`` if none."""
    out: dict[str, tuple[int, int]] = {}
    for key, val in _read().items():
        if not isinstance(val, dict):
            continue
        try:
            out[str(key)] = (int(val["x"]), int(val["y"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def load_visibility() -> dict[str, bool]:
    """Return ``{widget_id: visible}`` only for entries that explicitly
    persisted a visibility flag. Anything missing is treated as visible."""
    out: dict[str, bool] = {}
    for key, val in _read().items():
        if isinstance(val, dict) and "visible" in val:
            out[str(key)] = bool(val["visible"])
    return out


def save_position(widget_id: str, x: int, y: int) -> None:
    """Update one widget's saved position. Other entries are preserved."""
    data = _read()
    entry = data.get(widget_id)
    if not isinstance(entry, dict):
        entry = {}
    entry["x"] = int(x)
    entry["y"] = int(y)
    data[widget_id] = entry
    _write(data)


def save_visibility(widget_id: str, visible: bool) -> None:
    """Persist a widget's visibility (close button / reset toggles this)."""
    data = _read()
    entry = data.get(widget_id)
    if not isinstance(entry, dict):
        entry = {}
    entry["visible"] = bool(visible)
    data[widget_id] = entry
    _write(data)


def delete_entries(widget_ids: list[str]) -> None:
    """Drop saved state for the listed widgets — used by the reset button
    to wipe positions+visibility while preserving entries we want to
    keep (the reset button's own placement)."""
    data = _read()
    changed = False
    for wid in widget_ids:
        if wid in data:
            del data[wid]
            changed = True
    if changed:
        _write(data)


def load_size_index(default: int, count: int) -> int:
    """Return the persisted size-cycle index, clamped to ``[0, count)``."""
    val = _read().get("size_index")
    if isinstance(val, int) and 0 <= val < count:
        return val
    return default


def save_size_index(idx: int) -> None:
    data = _read()
    data["size_index"] = int(idx)
    _write(data)


def load_polling_hz(default: int, allowed: tuple[int, ...]) -> int:
    """Return the persisted polling rate, snapping to one of ``allowed``.
    Anything else (corrupt entry, removed value) falls back to ``default``."""
    val = _read().get("polling_hz")
    if isinstance(val, int) and val in allowed:
        return val
    return default


def save_polling_hz(hz: int) -> None:
    data = _read()
    data["polling_hz"] = int(hz)
    _write(data)
