"""Decrypt and parse Assetto Corsa 1's per-car ``data.acd`` files.

AC1's shared-memory feed omits a number of fields the overlay needs to
render properly (per-compound ideal pressure, the raw torque curve,
per-compound tyre-temperature performance curves). Those values live on
disk in ``<AC install>/content/cars/<car_name>/data.acd`` — an encrypted
container of plain-text ``.ini``/``.lut`` files.

The decryption algorithm is the one Luigi Auriemma reverse-engineered
years ago and the sibling LiveTelemetry plugin
(``D:/projects/live-telemetry``) has been using in production. We port
just the slice the overlay needs: open the container, expose individual
files by name, and provide typed helpers for the three pieces we read
(ideal pressure, power curve, tyre thermal-performance curve).

Modern AC cars ship the ``data/`` folder unpacked alongside ``data.acd``
for mod cars (no encryption); we handle both paths.
"""
from __future__ import annotations

import os
from collections import OrderedDict
from configparser import ConfigParser, Error as ConfigError
from struct import unpack


# Section prefixes in tyres.ini correspond to axle position, indexed by
# compound number suffix (FRONT, FRONT_1, ..., FRONT_9 / REAR, REAR_1,
# ...). The overlay only needs front-vs-rear, never per-corner.
_AXLE_PREFIX = {"FL": "FRONT", "FR": "FRONT", "RL": "REAR", "RR": "REAR"}
_MAX_COMPOUNDS = 10
# AC1 publishes the SHORT_NAME in graphics.tyreCompound; tyres.ini maps
# SHORT_NAME -> the full section name we read PRESSURE_IDEAL etc. from.
_DEFAULT_COMPOUND_INDEX = 0


class ACD:
    """A decrypted ``data.acd`` container exposing its files as strings.

    Falls back to reading the unpacked ``data/`` folder when ``data.acd``
    is missing (common with mod cars and Kunos's newer cars that ship the
    folder unencrypted).
    """

    def __init__(self, car_dir: str) -> None:
        self._car_name = os.path.basename(car_dir.rstrip("/\\"))
        self._files: "OrderedDict[str, str]" = OrderedDict()
        self._key = _generate_key(self._car_name)

        acd_path = os.path.join(car_dir, "data.acd")
        if os.path.isfile(acd_path):
            self._load_from_acd(acd_path)
        else:
            self._load_from_folder(os.path.join(car_dir, "data"))

    @property
    def car_name(self) -> str:
        return self._car_name

    @property
    def file_names(self) -> list[str]:
        return list(self._files.keys())

    def get_file(self, name: str) -> str:
        return self._files.get(name, "")

    def has_file(self, name: str) -> bool:
        return name in self._files

    # --- public typed accessors -------------------------------------------

    def get_ideal_pressure(self, compound: str, wid: str) -> float | None:
        """Return ``PRESSURE_IDEAL`` (psi) for the current compound + wheel.

        ``compound`` is the SHORT_NAME the game publishes via
        ``graphics.tyreCompound``. Returns ``None`` if tyres.ini is missing
        or the compound can't be matched (mod cars sometimes drop fields).
        """
        section = self._find_compound_section(compound, wid)
        if section is None:
            return None
        config = self._read_ini("tyres.ini")
        if config is None:
            return None
        try:
            return float(config.get(section, "PRESSURE_IDEAL"))
        except (ConfigError, ValueError):
            return None

    def get_power_curve(self) -> list[tuple[float, float]] | None:
        """Return the (rpm, torque-Nm) curve from engine.ini's referenced
        .lut. ``None`` when engine.ini or the .lut can't be parsed.
        """
        config = self._read_ini("engine.ini")
        if config is None:
            return None
        try:
            lut_name = config.get("HEADER", "POWER_CURVE")
        except ConfigError:
            return None
        return _parse_lut(self.get_file(lut_name))

    def get_temp_curve(self, compound: str, wid: str) -> list[tuple[float, float]] | None:
        """Return the (temperature-C, grip-fraction) curve for the given
        compound + wheel from the THERMAL_<section>.PERFORMANCE_CURVE .lut.
        ``None`` when the section / .lut can't be parsed.
        """
        section = self._find_compound_section(compound, wid)
        if section is None:
            return None
        config = self._read_ini("tyres.ini")
        if config is None:
            return None
        thermal = f"THERMAL_{section}"
        try:
            lut_name = config.get(thermal, "PERFORMANCE_CURVE")
        except ConfigError:
            return None
        return _parse_lut(self.get_file(lut_name))

    # --- internals --------------------------------------------------------

    def _read_ini(self, name: str) -> ConfigParser | None:
        content = self.get_file(name)
        if not content:
            return None
        config = ConfigParser(empty_lines_in_values=False,
                              inline_comment_prefixes=(";",))
        try:
            config.read_string(content)
        except ConfigError:
            return None
        return config

    def _find_compound_section(self, compound: str, wid: str) -> str | None:
        config = self._read_ini("tyres.ini")
        if config is None:
            return None
        prefix = _AXLE_PREFIX[wid]
        # Match SHORT_NAME first — the most reliable signal across cars.
        for i in range(_MAX_COMPOUNDS):
            section = prefix if i == 0 else f"{prefix}_{i}"
            if (config.has_option(section, "SHORT_NAME")
                    and config.get(section, "SHORT_NAME") == compound):
                return section
        # Fall back to the default-compound index if matching failed —
        # better than returning nothing for cars whose SHORT_NAME doesn't
        # match what the game publishes (some mod cars).
        try:
            idx = int(config.get("COMPOUND_DEFAULT", "INDEX"))
        except (ConfigError, ValueError):
            idx = _DEFAULT_COMPOUND_INDEX
        section = prefix if idx == 0 else f"{prefix}_{idx}"
        return section if config.has_section(section) else None

    def _load_from_acd(self, path: str) -> None:
        with open(path, "rb") as raw:
            blob = bytearray(raw.read())
        size = len(blob)
        if size <= 8:
            return

        # File starts with an optional 8-byte version header for newer
        # cars. Old Kunos cars omit it; we detect by a negative first
        # int32 (sentinel) and skip both fields when present.
        offset = 0
        sentinel = unpack("<l", bytes(blob[offset:offset + 4]))[0]
        offset += 4
        if sentinel < 0:
            offset += 4
        else:
            offset = 0

        key_size = len(self._key)
        while offset < size:
            name_size = unpack("<L", bytes(blob[offset:offset + 4]))[0]
            offset += 4
            file_name = bytes(blob[offset:offset + name_size]).decode("utf-8",
                                                                     errors="replace")
            offset += name_size
            file_size = unpack("<L", bytes(blob[offset:offset + 4]))[0]
            offset += 4

            # Each character is stored as a 4-byte int; only the low byte
            # carries the encrypted code. Take every fourth byte.
            packed = bytes(blob[offset:offset + file_size * 4])[::4]
            offset += file_size * 4

            chars = []
            for i in range(file_size):
                code = packed[i] - ord(self._key[i % key_size])
                # The encryption is order-of-magnitude-stable; a negative
                # code means a single corrupt byte. The original plugin
                # substituted '!' to keep parsing, so we do the same.
                chars.append(chr(33 if code < 0 else code))
            self._files[file_name] = "".join(chars)

    def _load_from_folder(self, folder: str) -> None:
        if not os.path.isdir(folder):
            return
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as raw:
                    self._files[name] = raw.read()
            except OSError:
                continue


def _parse_lut(content: str) -> list[tuple[float, float]] | None:
    """Parse an AC ``.lut`` lookup table. Lines are ``x|y`` plus optional
    ``;``/``#`` comment lines. Returns ``None`` if no parseable points.
    """
    if not content:
        return None
    points: list[tuple[float, float]] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith((";", "#")):
            continue
        parts = line.split("|")
        if len(parts) != 2:
            continue
        try:
            points.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    return points if points else None


def _generate_key(car_name: str) -> str:
    """Re-implement Auriemma's ACD key derivation byte-for-byte.

    Each ``keyN`` accumulates a different masked combination of the car
    name's character codes; the 8 bytes joined with ``-`` form the
    rotating XOR-style key consumed by the decrypter. The arithmetic
    looks arbitrary because it *is* arbitrary — the goal is bug-for-bug
    parity with what the game's loader does, not algorithmic elegance.
    """
    name = car_name
    n = len(name)

    key1 = 0
    for i in range(n):
        key1 += ord(name[i])
    key1 &= 0xff

    key2 = 0
    i = 0
    while i < n - 1:
        key2 *= ord(name[i])
        i += 1
        key2 -= ord(name[i])
        i += 1
    key2 &= 0xff

    key3 = 0
    i = 1
    while i < n - 3:
        key3 *= ord(name[i])
        i += 1
        key3 = int(key3 / (ord(name[i]) + 0x1b))
        i -= 2
        key3 += -0x1b - ord(name[i])
        i += 4
    key3 &= 0xff

    key4 = 0x1683
    i = 1
    while i < n:
        key4 -= ord(name[i])
        i += 1
    key4 &= 0xff

    key5 = 0x42
    i = 1
    while i < n - 4:
        tmp = (ord(name[i]) + 0xf) * key5
        i -= 1
        key5 = (ord(name[i]) + 0xf) * tmp + 0x16
        i += 5
    key5 &= 0xff

    key6 = 0x65
    i = 0
    while i < n - 2:
        key6 -= ord(name[i])
        i += 2
    key6 &= 0xff

    key7 = 0xab
    i = 0
    while i < n - 2:
        key7 %= ord(name[i])
        i += 2
    key7 &= 0xff

    key8 = 0xab
    i = 0
    while i < n - 1:
        key8 = int(key8 / ord(name[i])) + ord(name[i + 1])
        i += 1
    key8 &= 0xff

    return f"{key1}-{key2}-{key3}-{key4}-{key5}-{key6}-{key7}-{key8}"


__all__ = ["ACD"]
