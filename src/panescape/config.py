"""Loading and validating monitor configuration files (``configurations/*.yml``)."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from . import models
from .units import parse_length, parse_resolution

ROTATIONS = {
    0: 0, 90: 90, 180: 180, 270: 270,
    "0": 0, "90": 90, "180": 180, "270": 270,
    "landscape": 0, "normal": 0, "none": 0, "horizontal": 0,
    "portrait-right": 90, "portrait_right": 90, "portrait right": 90, "right": 90, "cw": 90,
    "landscape-flipped": 180, "landscape (flipped)": 180, "flipped": 180, "upside-down": 180,
    "portrait-left": 270, "portrait_left": 270, "portrait left": 270, "left": 270, "ccw": 270,
}
ROTATION_NAMES = {0: "landscape", 90: "portrait-right", 180: "landscape-flipped", 270: "portrait-left"}
SIDES = ("top", "right", "bottom", "left")


class ConfigError(ValueError):
    pass


def rotate_bezel(native: dict, rotation: int) -> dict:
    """Map bezels from the panel's native landscape frame to how it is mounted.

    Rotation follows GNOME Settings: ``portrait-right`` (90) means the panel was
    turned clockwise, so its native top edge ends up on the right.
    """
    t, r, b, lf = (native[s] for s in SIDES)
    if rotation == 0:
        return {"top": t, "right": r, "bottom": b, "left": lf}
    if rotation == 90:
        return {"right": t, "bottom": r, "left": b, "top": lf}
    if rotation == 180:
        return {"bottom": t, "left": r, "top": b, "right": lf}
    if rotation == 270:
        return {"left": t, "top": r, "right": b, "bottom": lf}
    raise ConfigError(f"bad rotation {rotation}")


def parse_bezel(value, base: dict) -> dict:
    """Bezel as a number (all sides), [top, right, bottom, left], or a dict.

    Dict keys: top/right/bottom/left, or x (left+right) / y (top+bottom).
    Unspecified sides fall back to ``base``.
    """
    out = dict(base)
    if value is None:
        return out
    if isinstance(value, (int, float, str)):
        v = parse_length(value)
        return {s: v for s in SIDES}
    if isinstance(value, (list, tuple)):
        if len(value) != 4:
            raise ConfigError("bezel list must be [top, right, bottom, left]")
        return {s: parse_length(v) for s, v in zip(SIDES, value, strict=False)}
    if isinstance(value, dict):
        for k, v in value.items():
            k = str(k).lower()
            if k in SIDES:
                out[k] = parse_length(v)
            elif k in ("x", "horizontal", "sides"):
                out["left"] = out["right"] = parse_length(v)
            elif k in ("y", "vertical"):
                out["top"] = out["bottom"] = parse_length(v)
            else:
                raise ConfigError(f"unknown bezel key {k!r}")
        return out
    raise ConfigError(f"cannot parse bezel {value!r}")


@dataclass
class Monitor:
    name: str
    index: int
    resolution: tuple[int, int]          # native landscape pixels
    active_native: tuple[float, float]   # native landscape mm
    rotation: int = 0
    bezel: dict = field(default_factory=lambda: {s: 0.0 for s in SIDES})  # as mounted, mm
    gap: float = 0.0                     # mm from previous monitor's housing; -ve = overlap
    v_pos: float = 0.0                   # mm, +ve = up, relative to v_ref
    v_align: str = "center"              # which edges v_pos is measured between
    v_ref: str | None = None             # monitor name (default: first monitor)
    position: tuple[float, float] | None = None  # explicit housing top-left (mm, y down)
    model: str | None = None
    match: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)

    @property
    def portrait(self) -> bool:
        return self.rotation in (90, 270)

    @property
    def pixels(self) -> tuple[int, int]:
        """Pixel size as mounted (what the wallpaper for this screen should be)."""
        w, h = self.resolution
        return (h, w) if self.portrait else (w, h)

    @property
    def active(self) -> tuple[float, float]:
        """Active area as mounted, mm."""
        w, h = self.active_native
        return (h, w) if self.portrait else (w, h)

    @property
    def outer(self) -> tuple[float, float]:
        w, h = self.active
        b = self.bezel
        return (w + b["left"] + b["right"], h + b["top"] + b["bottom"])

    @property
    def ppmm(self) -> tuple[float, float]:
        (pw, ph), (aw, ah) = self.pixels, self.active
        return (pw / aw, ph / ah)


@dataclass
class Configuration:
    name: str
    path: Path | None
    monitors: list[Monitor]
    crop: dict
    output: dict
    description: str = ""
    raw: dict = field(default_factory=dict)

    def fingerprint(self) -> str:
        data = {
            "monitors": [asdict(m) | {"notes": None} for m in self.monitors],
            "output": self.output,
        }
        return hashlib.sha1(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:12]

    def monitor(self, name: str) -> Monitor:
        for m in self.monitors:
            if m.name == name:
                return m
        raise ConfigError(f"no monitor named {name!r}")


def _rotation(entry: dict, notes: list) -> int:
    rot = entry.get("rotation")
    orient = str(entry.get("orientation", "")).strip().lower()
    if rot is not None:
        key = rot if isinstance(rot, int) else str(rot).strip().lower()
        if key not in ROTATIONS:
            raise ConfigError(f"unknown rotation {rot!r} (landscape, portrait-right, portrait-left, landscape-flipped)")
        r = ROTATIONS[key]
        if orient in ("vertical", "portrait") and r in (0, 180):
            raise ConfigError(f"orientation {orient!r} conflicts with rotation {rot!r}")
        return r
    if orient in ("vertical", "portrait"):
        notes.append("orientation is portrait but no rotation given; assuming portrait-right "
                     "(only affects which side the model's thicker bezel is placed)")
        return 90
    if orient in ("", "horizontal", "landscape"):
        return 0
    raise ConfigError(f"unknown orientation {orient!r}")


def _monitor_from_entry(i: int, entry: dict, defaults: dict) -> Monitor:
    notes: list[str] = []
    name = str(entry.get("name") or f"monitor{i + 1}")
    model_name = entry.get("model")
    model = models.lookup(model_name) if model_name else None
    if model_name and not model:
        notes.append(f"model {model_name!r} not in built-in database; using size/resolution")

    # resolution
    if entry.get("resolution") is not None:
        res = parse_resolution(entry["resolution"])
    elif model:
        res = tuple(model["resolution"])
    else:
        raise ConfigError(f"monitor {name!r}: needs 'resolution' (or a known 'model')")

    # physical active area (native landscape)
    if entry.get("active_area") is not None:
        aa = entry["active_area"]
        if not isinstance(aa, (list, tuple)) or len(aa) != 2:
            raise ConfigError(f"monitor {name!r}: active_area must be [width, height] in mm (landscape)")
        w, h = sorted((parse_length(aa[0]), parse_length(aa[1])), reverse=True)
        active = (w, h)
    elif model and entry.get("size") is None:
        active = tuple(model["active_area"])
    elif entry.get("size") is not None:
        diag = parse_length(entry["size"], default_unit="in")
        rw, rh = res
        k = diag / math.hypot(rw, rh)  # assumes square pixels
        active = (rw * k, rh * k)
        notes.append("physical size derived from diagonal (nominal diagonals are often rounded; "
                     "'model' or 'active_area' is more precise)")
    else:
        raise ConfigError(f"monitor {name!r}: needs 'size', 'active_area' or a known 'model'")

    rotation = _rotation(entry, notes)

    # bezels
    base = {s: 0.0 for s in SIDES}
    if model and model.get("bezel"):
        base = rotate_bezel(model["bezel"], rotation)
        if entry.get("bezel") is None and model.get("bezel_estimated"):
            notes.append("bezels are estimated from the spec sheet; measure and set 'bezel' for best alignment")
    bezel_val = entry.get("bezel", defaults.get("bezel"))
    bezel = parse_bezel(bezel_val, base)

    # spacing (gap, or legacy spacing/overlap)
    if "gap" in entry:
        gap = parse_length(entry["gap"])
    elif "spacing" in entry or "overlap" in entry:
        gap = parse_length(entry.get("spacing", 0) or 0) - parse_length(entry.get("overlap", 0) or 0)
    else:
        gap = parse_length(defaults.get("gap", 0))

    position = entry.get("position")
    if position is not None:
        if not isinstance(position, (list, tuple)) or len(position) != 2:
            raise ConfigError(f"monitor {name!r}: position must be [x, y] in mm")
        position = (parse_length(position[0]), parse_length(position[1]))

    v_align = str(entry.get("v_align", defaults.get("v_align", "center"))).lower().replace("centre", "center")
    if v_align not in ("center", "top", "bottom"):
        raise ConfigError(f"monitor {name!r}: v_align must be center, top or bottom")

    match = {}
    for key in ("connector", "serial", "product", "vendor"):
        if entry.get(key) is not None:
            match[key] = str(entry[key])
    if isinstance(entry.get("match"), dict):
        match.update({k: str(v) for k, v in entry["match"].items()})

    return Monitor(
        name=name,
        index=i,
        resolution=res,
        active_native=active,
        rotation=rotation,
        bezel=bezel,
        gap=gap,
        v_pos=parse_length(entry.get("v_pos", 0) or 0),
        v_align=v_align,
        v_ref=entry.get("v_ref"),
        position=position,
        model=model["model"] if model else model_name,
        match=match,
        notes=notes,
    )


def load_configuration(path: str | Path) -> Configuration:
    path = Path(path)
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    return configuration_from_dict(raw, name=raw.get("name") or path.stem, path=path)


def configuration_from_dict(raw: dict, name: str = "config", path: Path | None = None) -> Configuration:
    defaults = raw.get("defaults") or {}
    entries = raw.get("monitors")
    if isinstance(entries, dict):
        # legacy form: {layout: LR, 1: {...}, 2: {...}}
        numbered = {k: v for k, v in entries.items() if isinstance(v, dict)}
        for k in ("edges", "gap"):
            if k in entries and not isinstance(entries[k], dict):
                defaults.setdefault("gap", 0 if entries[k] in ("joined", None) else entries[k])
        entries = [numbered[k] for k in sorted(numbered, key=lambda x: int(x))]
    if not entries:
        raise ConfigError("configuration has no monitors")
    mons = [_monitor_from_entry(i, e or {}, defaults) for i, e in enumerate(entries)]
    names = [m.name for m in mons]
    if len(set(names)) != len(names):
        raise ConfigError("monitor names must be unique")
    for m in mons:
        if m.v_ref is not None and m.v_ref not in names:
            raise ConfigError(f"monitor {m.name!r}: v_ref {m.v_ref!r} is not a monitor name")
    return Configuration(
        name=str(name),
        path=path,
        monitors=mons,
        crop=dict(raw.get("crop") or {}),
        output=dict(raw.get("output") or {}),
        description=str(raw.get("description", "")),
        raw=raw,
    )
