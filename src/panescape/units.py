"""Parsing helpers for the human-friendly values used in the YAML files."""

from __future__ import annotations

import re

MM_PER_UNIT = {
    "mm": 1.0,
    "cm": 10.0,
    "m": 1000.0,
    "in": 25.4,
    "inch": 25.4,
    "inches": 25.4,
    '"': 25.4,
}

RESOLUTION_ALIASES = {
    "HD": (1280, 720),
    "720P": (1280, 720),
    "WXGA": (1366, 768),
    "HD+": (1600, 900),
    "FHD": (1920, 1080),
    "1080P": (1920, 1080),
    "FULLHD": (1920, 1080),
    "WUXGA": (1920, 1200),
    "QHD": (2560, 1440),
    "WQHD": (2560, 1440),
    "1440P": (2560, 1440),
    "2K": (2560, 1440),
    "WQXGA": (2560, 1600),
    "UWFHD": (2560, 1080),
    "UWQHD": (3440, 1440),
    "4K": (3840, 2160),
    "UHD": (3840, 2160),
    "4KUHD": (3840, 2160),
    "2160P": (3840, 2160),
    "5K": (5120, 2880),
    "6K": (6016, 3384),
    "8K": (7680, 4320),
}

_LEN_RE = re.compile(r"^\s*([-+]?\d*\.?\d+)\s*([a-zA-Z\"]*)\s*$")


def parse_length(value, default_unit: str = "mm") -> float:
    """Return a length in millimetres.

    Accepts numbers (interpreted in ``default_unit``) or strings such as
    ``"28in"``, ``'28"'``, ``"711mm"``, ``"2.5 cm"``.
    """
    if value is None:
        raise ValueError("missing length")
    if isinstance(value, (int, float)):
        return float(value) * MM_PER_UNIT[default_unit]
    m = _LEN_RE.match(str(value))
    if not m:
        raise ValueError(f"cannot parse length {value!r}")
    number, unit = float(m.group(1)), (m.group(2) or default_unit).lower()
    if unit not in MM_PER_UNIT:
        raise ValueError(f"unknown unit {unit!r} in {value!r} (use mm, cm, in)")
    return number * MM_PER_UNIT[unit]


def parse_resolution(value) -> tuple[int, int]:
    """Return ``(width, height)`` in the panel's native landscape orientation."""
    if value is None:
        raise ValueError("missing resolution")
    if isinstance(value, (list, tuple)) and len(value) == 2:
        w, h = int(value[0]), int(value[1])
    else:
        key = str(value).strip().upper().replace(" ", "").replace("_", "").replace("-", "")
        if key in RESOLUTION_ALIASES:
            w, h = RESOLUTION_ALIASES[key]
        else:
            m = re.match(r"^(\d+)\s*[xX×*]\s*(\d+)$", str(value).strip())
            if not m:
                raise ValueError(f"cannot parse resolution {value!r} (e.g. 3840x2160, 4K, FHD)")
            w, h = int(m.group(1)), int(m.group(2))
    return (max(w, h), min(w, h))


def parse_duration(value) -> float:
    """Return seconds. Accepts numbers (seconds) or e.g. ``"90s"``, ``"10m"``, ``"1h30m"``."""
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().lower()
    parts = re.findall(r"(\d*\.?\d+)\s*(h|hr|hrs|hours?|m|min|mins|minutes?|s|sec|secs|seconds?)?", s)
    if not parts or "".join(n + u for n, u in parts).replace(" ", "") != s.replace(" ", ""):
        raise ValueError(f"cannot parse duration {value!r} (e.g. 30s, 10m, 1h)")
    total = 0.0
    for number, unit in parts:
        mult = 1.0
        if unit.startswith("h"):
            mult = 3600.0
        elif unit.startswith("m"):
            mult = 60.0
        total += float(number) * mult
    return total
