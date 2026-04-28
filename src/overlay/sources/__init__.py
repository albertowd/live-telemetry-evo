from __future__ import annotations

from .base import TelemetrySource
from .synthetic import SyntheticTelemetrySource


def make_source(name: str, hz: int = 60, parent=None) -> TelemetrySource:
    """Factory that maps a CLI/source-name string to the matching reader.

    Imports the AC Evo reader lazily so the synthetic path keeps working on
    non-Windows hosts (mmap by tagname is a Windows feature).
    """
    if name == "synthetic":
        return SyntheticTelemetrySource(hz=hz, parent=parent)
    if name == "ac-evo":
        from .ac_evo import AcEvoTelemetrySource  # pylint: disable=import-outside-toplevel
        return AcEvoTelemetrySource(hz=hz, parent=parent)
    raise ValueError(f"unknown telemetry source: {name!r}")


__all__ = ["TelemetrySource", "SyntheticTelemetrySource", "make_source"]
