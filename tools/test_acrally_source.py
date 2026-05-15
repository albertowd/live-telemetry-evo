"""Connect to a running AC Rally session, read N frames, print them.

Verifies the source maps fields correctly before launching the full
overlay. Usage::

    python tools/test_acrally_source.py

Stops after a few frames; doesn't open any UI.
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, "src")

from overlay.sources.acrally import AcRallySharedMemoryReader  # noqa: E402
from overlay.sources.acrally import AcRallyTelemetrySource, _k_to_c  # noqa: E402


def _print_frame_summary(reader: AcRallySharedMemoryReader, source: AcRallyTelemetrySource) -> None:
    """Read one frame and print the converted values."""
    phys = reader.read_physics()
    source._apply_physics(phys)  # pylint: disable=protected-access
    f = source._frame  # pylint: disable=protected-access

    e = f.engine
    print(f"  engine: rpm={e.rpm:.0f}/{e.max_rpm:.0f}  gear={e.gear}  "
          f"speed={e.speed_kmh:.1f} km/h  water={e.water_temp_c:.1f} C  "
          f"brake_bias={e.brake_bias:.2f}  fuel={e.fuel_liters:.1f} L")
    for wid in ("FL", "FR", "RL", "RR"):
        w = f.wheels[wid]
        print(f"  {wid}: camber={w.camber:+.4f}  load={w.tire_l:6.1f} N  "
              f"p={w.tire_p:.1f} psi (norm {w.tire_p_norm:.2f})  "
              f"core_t={w.tire_t_c:5.1f} C  brake_t={w.brake_t:5.1f} C  "
              f"has_load={w.has_wheel_load}  has_h={w.has_ride_height}  "
              f"has_camber={w.has_camber}")


def main() -> int:
    reader = AcRallySharedMemoryReader()
    try:
        reader.open()
    except OSError as exc:
        print(f"could not attach to AC Rally shared memory: {exc}",
              file=sys.stderr)
        return 1

    source = AcRallyTelemetrySource(hz=60)
    source._reader = reader  # pylint: disable=protected-access
    source._apply_static(reader.read_static())  # pylint: disable=protected-access

    for tick in range(5):
        print(f"\n--- tick {tick + 1} ---")
        _print_frame_summary(reader, source)
        time.sleep(0.5)

    reader.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
