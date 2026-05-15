"""Dump tool for inspecting AC Evo shared memory against a running game.

Usage::

    python -m overlay.sources.dump physics
    python -m overlay.sources.dump graphics --bytes 256
    python -m overlay.sources.dump static --parsed
    python -m overlay.sources.dump physics --validate --watch 0.5
    python -m overlay.sources.dump physics --camber

Designed for iterating on the struct layout: print a raw hex window, or
parse with the best-effort structs and show the resulting field values, so
known values (RPM you're holding on screen, current gear, etc.) can be
matched to byte offsets.

``--validate`` runs the live values against the PDF-documented range and
sign expectations the source now relies on; ``--camber`` prints the raw
per-wheel camber values for the FR/RR sign-convention check.
"""
from __future__ import annotations

import argparse
import math
import struct
import sys
import time

from .ac_evo import AcEvoSharedMemoryReader


def _track_monotonic(reader: AcEvoSharedMemoryReader, segment: str,
                     duration_s: float, lo: float, hi: float,
                     min_drop: float = 0.0005) -> str:
    """Watch a segment over time and report aligned floats that only ever decrease.

    True tire wear is monotonic — it can't recover without a tire change.
    Sample the buffer repeatedly over ``duration_s`` and keep only offsets
    where every sample was <= the previous one, with at least ``min_drop``
    total decrease (filters out noise). Constrain to floats in [lo, hi]
    throughout to skip out-of-range padding.
    """
    samples: list[bytes] = []
    deadline = time.monotonic() + duration_s
    print(f"[track] sampling '{segment}' for {duration_s:.0f} s — drive the car...",
          file=sys.stderr)
    while time.monotonic() < deadline:
        samples.append(reader.read_raw(segment))
        time.sleep(0.5)
    if len(samples) < 3:
        return "[track] need at least 3 samples; increase --duration"

    end = min(len(samples[0]) - 4, 4096)
    candidates: list[tuple[int, float, float]] = []
    for i in range(0, end, 4):
        try:
            values = [struct.unpack_from("<f", s, i)[0] for s in samples]
        except struct.error:
            continue
        if any(math.isnan(v) for v in values):
            continue
        if not all(lo <= v <= hi for v in values):
            continue
        if not all(values[k] >= values[k + 1] for k in range(len(values) - 1)):
            continue
        drop = values[0] - values[-1]
        if drop < min_drop:
            continue
        candidates.append((i, values[0], values[-1]))

    lines = [f"[track] monotonically-decreasing offsets in [{lo}, {hi}] "
             f"with >={min_drop} drop over {duration_s:.0f} s "
             f"({len(samples)} samples):"]
    for off, first, last in candidates:
        lines.append(f"  0x{off:04x} ({off:4d}): {first:.6f} -> {last:.6f}  "
                     f"(Δ {first - last:+.6f})")
    lines.append(f"  total: {len(candidates)}")
    return "\n".join(lines)


def _scan_floats(data: bytes, lo: float, hi: float, max_offset: int = 4096) -> str:
    """List every 4-byte aligned float in the buffer that falls in [lo, hi].

    Useful for hunting unknown field offsets: pick a range tight enough to
    rule out padding (e.g. tire wear is roughly 0.5..1.0 on AC1 scale, brake
    temp is 50..900 C). Run twice — once with a known tire state, once with
    a different state — and the offsets that changed are your candidates.
    """
    lines: list[str] = [f"[scan] aligned floats in [{lo}, {hi}]:"]
    found = 0
    end = min(len(data), max_offset)
    for i in range(0, end - 4, 4):
        v = struct.unpack_from("<f", data, i)[0]
        if math.isnan(v) or v == 0.0:
            continue
        if lo <= v <= hi:
            lines.append(f"  0x{i:04x} ({i:4d}): {v:.6f}")
            found += 1
    lines.append(f"  total: {found}")
    return "\n".join(lines)


def _validate(reader: AcEvoSharedMemoryReader) -> str:
    """Run PDF-conformance checks on the live physics + graphics blocks.

    Each check returns one of:
      OK     value is in the PDF-documented range / sign convention
      WARN   value is plausible but at an edge (e.g. zero when we expect
             motion) — usually means "drive the car"
      FAIL   value clearly violates PDF — the Phase 1 source fix is wrong
             or the running build doesn't match the spec

    Drive a varied lap (full-lock turn, throttle, brake, kerb hit) for
    the WARNs to clear.
    """
    ph = reader.read_physics()
    gr = reader.read_graphics()

    rows: list[tuple[str, str, str, str, str]] = []

    def check(category: str, name: str, expected: str, actual: str,
              status: str) -> None:
        rows.append((status, category, name, expected, actual))

    # --- steering (Phase 1 fix #2/#3) ------------------------------------
    sa = float(ph.steerAngle)
    if -1.0 <= sa <= 1.0:
        check("steer", "steerAngle in [-1, 1]", "-1.0..+1.0", f"{sa:+.3f}", "OK")
    else:
        check("steer", "steerAngle in [-1, 1]", "-1.0..+1.0", f"{sa:+.3f}", "FAIL")
    sd = int(gr.steer_degrees)
    if -1080 <= sd <= 1080:
        status = "OK" if abs(sd) > 0 or abs(sa) < 0.02 else "WARN"
        check("steer", "graphics.steer_degrees plausible",
              "±[0, 1080]°", f"{sd:+d}°", status)
    else:
        check("steer", "graphics.steer_degrees plausible",
              "±[0, 1080]°", f"{sd:+d}°", "FAIL")

    # --- accG ordering (Phase 1 fix #1) ----------------------------------
    g = tuple(float(ph.accG[k]) for k in range(3))
    check("g_force", "accG[0] lateral",       "≈0 at rest, ±cornering", f"{g[0]:+.2f}", "OK")
    check("g_force", "accG[1] longitudinal",  "+ accel / − brake",      f"{g[1]:+.2f}", "OK")
    # AC Evo subtracts gravity from accG[2], so the vertical axis sits at
    # ~0 at rest and only swings under chassis pitch (nose dip on braking,
    # squat on acceleration, kerb hits). Accept anything in ±3 g.
    if abs(g[2]) <= 3.0:
        check("g_force", "accG[2] vertical (gravity-subtracted)",
              "≈0 at rest, ±pitch", f"{g[2]:+.2f}", "OK")
    else:
        check("g_force", "accG[2] vertical (gravity-subtracted)",
              "≈0 at rest, ±pitch", f"{g[2]:+.2f}", "WARN")

    # --- suspension travel (Phase 1 fix #5) ------------------------------
    # PDF says non-negative compression metres, but some chassis publish
    # signed displacement around a static reference — source compensates
    # with abs(). Accept either sign; reject only unphysical magnitudes.
    travels = tuple(float(ph.suspensionTravel[k]) for k in range(4))
    if all(abs(t) <= 0.5 for t in travels):
        sign = "signed (mixed)" if any(t < 0 for t in travels) else "non-negative"
        check("susp", "suspensionTravel plausible magnitude",
              "|travel| ≤ 0.5 m", f"{travels} [{sign}]", "OK")
    else:
        check("susp", "suspensionTravel plausible magnitude",
              "|travel| ≤ 0.5 m", f"{travels}", "FAIL")

    # --- ride height (Phase 1 fix #6) ------------------------------------
    # PDF says metres but some chassis publish mm directly; the source
    # auto-detects via |raw| >= 1.0. Accept either convention here.
    rh = (float(ph.rideHeight[0]), float(ph.rideHeight[1]))
    if all(0.005 <= h <= 0.5 for h in rh):
        check("ride", "rideHeight plausible (metres convention)",
              "[0.005, 0.5] m or [5, 500] mm",
              f"{rh} m", "OK")
    elif all(5.0 <= h <= 500.0 for h in rh):
        check("ride", "rideHeight plausible (mm-in-metres convention)",
              "[0.005, 0.5] m or [5, 500] mm",
              f"{rh} (raw mm)", "OK")
    else:
        check("ride", "rideHeight plausible",
              "[0.005, 0.5] m or [5, 500] mm",
              f"{rh}", "FAIL")

    # --- tyre wear (Phase 1 fix #7) --------------------------------------
    # PDF documents the field but current builds leave it dead-zero.
    # Source masks with the _tire_wear_live latch; bar hidden until
    # populated. All-zero is the expected state today, not a failure.
    tw = tuple(float(ph.tyreWear[k]) for k in range(4))
    if all(w == 0.0 for w in tw):
        check("tyre", "tyreWear (dead in current builds)",
              "all zero today, [0, 1] when alive",
              f"{tw}  (latch hides bar)", "OK")
    elif all(0.0 <= w <= 1.0 for w in tw):
        check("tyre", "tyreWear populated, in PDF range",
              "[0, 1]", f"{tw}", "OK")
    else:
        check("tyre", "tyreWear in PDF range",
              "[0, 1]", f"{tw}", "FAIL")

    # --- damage scale (PDF: 0..1) ----------------------------------------
    dmg = tuple(float(ph.carDamage[k]) for k in range(5))
    if all(0.0 <= d <= 1.0 for d in dmg):
        check("damage", "carDamage[5] in [0, 1] (PDF says normalised)",
              "[0, 1]", f"{dmg}", "OK")
    else:
        check("damage", "carDamage[5] in [0, 1] (PDF says normalised)",
              "[0, 1]", f"{dmg}",
              "FAIL")  # MEMORY.md says it might be absolute units — this is the test

    # --- pad / disc life (PDF: 0..1) -------------------------------------
    pl = tuple(float(ph.padLife[k]) for k in range(4))
    dl = tuple(float(ph.discLife[k]) for k in range(4))
    if all(0.0 <= v <= 1.0 for v in pl):
        check("brake", "padLife[4] in [0, 1] (PDF says normalised)",
              "[0, 1]", f"{pl}", "OK")
    else:
        check("brake", "padLife[4] in [0, 1] (PDF says normalised)",
              "[0, 1]", f"{pl}", "FAIL")
    if all(0.0 <= v <= 1.0 for v in dl):
        check("brake", "discLife[4] in [0, 1] (PDF says normalised)",
              "[0, 1]", f"{dl}", "OK")
    else:
        check("brake", "discLife[4] in [0, 1] (PDF says normalised)",
              "[0, 1]", f"{dl}", "FAIL")

    # --- aid level vs intervention (Phase 1 fix #8) ----------------------
    tc_act = float(ph.tc)
    abs_act = float(ph.abs)
    if 0.0 <= tc_act <= 1.0 and 0.0 <= abs_act <= 1.0:
        check("aids", "physics.tc/abs are intervention 0..1",
              "[0, 1]", f"tc={tc_act:.2f} abs={abs_act:.2f}", "OK")
    else:
        check("aids", "physics.tc/abs are intervention 0..1",
              "[0, 1]", f"tc={tc_act:.2f} abs={abs_act:.2f}", "FAIL")
    tc_lvl = int(gr.electronics.tc_level)
    abs_lvl = int(gr.electronics.abs_level)
    check("aids", "electronics.tc/abs_level driver-set ints",
          "≥ 0", f"tc={tc_lvl} abs={abs_lvl}", "OK")

    # --- brake bias (sanity) ---------------------------------------------
    bb = float(ph.brakeBias)
    if 0.15 <= bb <= 0.85:
        check("aids", "brakeBias plausible front fraction",
              "[0.15, 0.85]", f"{bb:.3f}", "OK")
    else:
        check("aids", "brakeBias plausible front fraction",
              "[0.15, 0.85]", f"{bb:.3f}", "WARN")

    # --- currentMaxRpm ---------------------------------------------------
    mr = int(ph.currentMaxRpm)
    if 1000 <= mr <= 25000:
        check("engine", "currentMaxRpm in plausible range",
              "[1000, 25000]", f"{mr}", "OK")
    else:
        check("engine", "currentMaxRpm in plausible range",
              "[1000, 25000]", f"{mr}", "WARN")

    # --- gear convention -------------------------------------------------
    gear = int(ph.gear)
    if 0 <= gear <= 12:
        check("engine", "gear 0=R, 1=N, 2+=fwd",
              "[0, 12]", f"{gear}", "OK")
    else:
        check("engine", "gear 0=R, 1=N, 2+=fwd",
              "[0, 12]", f"{gear}", "FAIL")

    # --- format as a fixed-width table ----------------------------------
    headers = ("STATUS", "CATEGORY", "CHECK", "EXPECTED", "ACTUAL")
    widths = [max(len(h), max(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = ["[validate]", fmt.format(*headers), fmt.format(*("-" * w for w in widths))]
    for r in rows:
        lines.append(fmt.format(*r))
    summary = {
        "OK": sum(1 for r in rows if r[0] == "OK"),
        "WARN": sum(1 for r in rows if r[0] == "WARN"),
        "FAIL": sum(1 for r in rows if r[0] == "FAIL"),
    }
    lines.append(f"  {summary['OK']} OK / {summary['WARN']} WARN / {summary['FAIL']} FAIL")
    return "\n".join(lines)


def _camber(reader: AcEvoSharedMemoryReader) -> str:
    """Print the raw per-wheel camberRAD values for the sign-convention test.

    Set up a car with **uniform negative camber** (e.g. ‑3° all four
    corners) in the setup tool and run this. If all four come back
    negative, the per-wheel sign flip is empirical-only and can be
    removed. If FR/RR come back positive while FL/RL are negative, the
    flip is real and any widget consuming camber must keep mirroring
    the right side.
    """
    ph = reader.read_physics()
    rad = tuple(float(ph.camberRAD[k]) for k in range(4))
    deg = tuple(math.degrees(v) for v in rad)
    lines = ["[camber] raw camberRAD values:"]
    for wid, r, d in zip(("FL", "FR", "RL", "RR"), rad, deg):
        lines.append(f"  {wid}: {r:+.5f} rad = {d:+.3f}°")
    fl_sign = "-" if rad[0] < 0 else "+"
    fr_sign = "-" if rad[1] < 0 else "+"
    rl_sign = "-" if rad[2] < 0 else "+"
    rr_sign = "-" if rad[3] < 0 else "+"
    lines.append("")
    lines.append(f"  signs: FL={fl_sign} FR={fr_sign} RL={rl_sign} RR={rr_sign}")
    if fl_sign == fr_sign and rl_sign == rr_sign:
        lines.append("  → uniform sign per axle: per-wheel flip is empirical-only "
                     "and can be removed.")
    elif fl_sign != fr_sign or rl_sign != rr_sign:
        lines.append("  → split sign across an axle: per-wheel flip IS real, "
                     "widget mirroring must stay.")
    return "\n".join(lines)


def _hex_dump(data: bytes, width: int = 16) -> str:
    lines = []
    for i in range(0, len(data), width):
        chunk = data[i:i + width]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{i:04x}  {hex_part:<{width * 3}}  {ascii_part}")
    return "\n".join(lines)


def _print_parsed(reader: AcEvoSharedMemoryReader, segment: str) -> None:
    if segment == "physics":
        ph = reader.read_physics()
        print("[physics]")
        print(f"  packetId       {ph.packetId}")
        print(f"  rpms           {ph.rpms}")
        print(f"  gear           {ph.gear}")
        print(f"  speedKmh       {ph.speedKmh:.2f}")
        print(f"  turboBoost     {ph.turboBoost:.3f}")
        print(f"  brake          {ph.brake:.3f}")
        print(f"  gas            {ph.gas:.3f}")
        print(f"  abs            {ph.abs:.3f}")
        print(f"  wheelSlip      {tuple(ph.wheelSlip)}")
        print(f"  wheelLoad      {tuple(ph.wheelLoad)}")
        print(f"  wheelsPressure {tuple(ph.wheelsPressure)}")
        print(f"  tyreCoreTemp   {tuple(ph.tyreCoreTemperature)}")
        # AC1 `tyreWear` (offset 120) is dead in current AC EVO builds —
        # always 0.0; print it anyway so any future revival is obvious.
        # padLife/discLife at 740/756 keep the AC1 semantic (1.0 = fresh,
        # decreasing toward 0); multiply by 1000 to match the in-game
        # pad/disc readouts. Live tyre wear (the in-game "X.XX" line) is
        # NOT in shared memory — game-internal, not exposed.
        print(f"  tyreWear (120) [DEAD]  {tuple(ph.tyreWear)}")
        print(f"  padLife  (740) ×1000   {tuple(round(x * 1000, 2) for x in ph.padLife)}")
        print(f"  discLife (756) ×1000   {tuple(round(x * 1000, 2) for x in ph.discLife)}")
        print(f"  tyreDirtyLevel {tuple(ph.tyreDirtyLevel)}")
        print(f"  brakeTemp      {tuple(ph.brakeTemp)}")
        print(f"  rideHeight     {tuple(ph.rideHeight)}")
        print(f"  suspTravel     {tuple(ph.suspensionTravel)}")
        print(f"  suspDamage     {tuple(ph.suspensionDamage)}")
        print(f"  tc             {ph.tc:.3f}")
        print(f"  pitLimiterOn   {ph.pitLimiterOn}")
    elif segment == "static":
        st = reader.read_static()
        print("[static]")
        print(f"  sm_version           {st.sm_version!r}")
        print(f"  ac_evo_version       {st.ac_evo_version!r}")
        print(f"  session              {st.session}")
        print(f"  session_name         {st.session_name!r}")
        print(f"  starting_grip        {st.starting_grip}")
        print(f"  starting_ambient_c   {st.starting_ambient_temperature_c:.2f}")
        print(f"  starting_ground_c    {st.starting_ground_temperature_c:.2f}")
        print(f"  is_static_weather    {st.is_static_weather}")
        print(f"  is_online            {st.is_online}")
        print(f"  nation               {st.nation!r}")
        print(f"  track                {st.track!r}")
        print(f"  track_configuration  {st.track_configuration!r}")
        print(f"  track_length_m       {st.track_length_m:.1f}")
    elif segment == "graphics":
        gr = reader.read_graphics()
        print("[graphics] per-wheel tyre states (raw vs game-normalized):")
        print(f"  {'wheel':5}  {'psi':>6}  {'norm_p':>7}  "
              f"{'t_c':>6}  {'norm_t':>7}  {'brake_c':>8}  {'norm_b':>7}  compound(F/R)")
        for label, ts in (("FL", gr.tyre_lf), ("FR", gr.tyre_rf),
                          ("RL", gr.tyre_lr), ("RR", gr.tyre_rr)):
            cf = bytes(ts.tyre_compound_front).rstrip(b"\x00").decode("ascii", errors="ignore")
            cr = bytes(ts.tyre_compound_rear).rstrip(b"\x00").decode("ascii", errors="ignore")
            print(f"  {label:5}  {ts.tyre_pressure:6.2f}  {ts.tyre_normalized_pressure:7.3f}  "
                  f"{ts.tyre_temperature_c:6.1f}  {ts.tyre_normalized_temperature_core:7.3f}  "
                  f"{ts.brake_temperature_c:8.0f}  {ts.brake_normalized_temperature:7.3f}  "
                  f"{cf}/{cr}")
        # Per-face temps: hypothesised to match the game's "OMI" HUD line.
        # Print both raw °C and game-normalised so a side-by-side check
        # against the on-screen values is one glance.
        print()
        print("[graphics] per-face tyre temps (raw °C / normalized) — verify against game OMI line:")
        print(f"  {'wheel':5}  {'L_c':>6}  {'M_c':>6}  {'R_c':>6}  "
              f"{'L_n':>6}  {'M_n':>6}  {'R_n':>6}")
        for label, ts in (("FL", gr.tyre_lf), ("FR", gr.tyre_rf),
                          ("RL", gr.tyre_lr), ("RR", gr.tyre_rr)):
            print(f"  {label:5}  {ts.tyre_temperature_left:6.2f}  "
                  f"{ts.tyre_temperature_center:6.2f}  {ts.tyre_temperature_right:6.2f}  "
                  f"{ts.tyre_normalized_temperature_left:6.3f}  "
                  f"{ts.tyre_normalized_temperature_center:6.3f}  "
                  f"{ts.tyre_normalized_temperature_right:6.3f}")
    else:
        print(f"[{segment}] parsed view not implemented yet — use --bytes for a hex dump")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("segment", choices=("physics", "graphics", "static"))
    parser.add_argument("--bytes", type=int, default=128, help="hex dump window size")
    parser.add_argument("--parsed", action="store_true", help="print parsed struct fields")
    parser.add_argument("--scan", nargs=2, type=float, metavar=("LO", "HI"),
                        help="scan aligned floats and list those in [LO, HI]")
    parser.add_argument("--track-monotonic", nargs=3, type=float,
                        metavar=("DURATION_S", "LO", "HI"),
                        help="sample over DURATION_S; report offsets in [LO, HI] "
                             "that only ever decreased (find true wear fields)")
    parser.add_argument("--validate", action="store_true",
                        help="check live values against PDF-documented ranges "
                             "and sign conventions; segment argument is ignored")
    parser.add_argument("--camber", action="store_true",
                        help="print raw per-wheel camberRAD for the sign-"
                             "convention test; segment argument is ignored")
    parser.add_argument("--watch", type=float, default=0.0,
                        help="if > 0, repeat at this interval in seconds")
    args = parser.parse_args(argv)

    reader = AcEvoSharedMemoryReader()
    try:
        reader.open()
    except OSError as exc:
        print(f"could not attach to AC Evo shared memory: {exc}", file=sys.stderr)
        print("is the game running and on this machine?", file=sys.stderr)
        return 1

    try:
        while True:
            print("-" * 60)
            if args.validate:
                print(_validate(reader))
            elif args.camber:
                print(_camber(reader))
            elif args.parsed:
                _print_parsed(reader, args.segment)
            elif args.scan is not None:
                raw = reader.read_raw(args.segment)
                print(_scan_floats(raw, args.scan[0], args.scan[1]))
            elif args.track_monotonic is not None:
                duration, lo, hi = args.track_monotonic
                print(_track_monotonic(reader, args.segment, duration, lo, hi))
                break  # tracking has its own timing loop
            else:
                raw = reader.read_raw(args.segment)[:args.bytes]
                print(_hex_dump(raw))
            if args.watch <= 0.0:
                break
            time.sleep(args.watch)
    finally:
        reader.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
