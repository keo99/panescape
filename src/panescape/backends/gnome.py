"""GNOME / Mutter backend (Wayland and X11).

GNOME has one wallpaper for the whole desktop. With ``picture-options =
spanned`` Mutter stretches that image over the bounding box of the *logical*
monitor layout (Settings > Displays) and each monitor shows its own part, at
``scale`` texels per logical pixel. So we build one composite image in that
logical space, at the largest monitor scale so HiDPI screens get native
resolution, and put each monitor's physically correct slice into its rectangle.

Slideshows use GNOME's native background XML format, so no daemon is needed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

SCHEMA = "org.gnome.desktop.background"
TRANSFORM_ROTATION = {0: 0, 1: 90, 2: 180, 3: 270, 4: 0, 5: 90, 6: 180, 7: 270}  # 4-7 flipped


class BackendError(RuntimeError):
    pass


@dataclass
class OsMonitor:
    connector: str
    vendor: str
    product: str
    serial: str
    display_name: str
    x: int                 # logical position
    y: int
    width: int             # logical size (as rotated)
    height: int
    scale: float
    rotation: int          # 0/90/180/270, GNOME Settings sense
    mode: tuple[int, int]  # native mode pixels (landscape as reported)
    primary: bool
    builtin: bool

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2


@dataclass
class OsLayout:
    monitors: list[OsMonitor]
    logical: bool          # True: logical layout mode (Wayland), False: physical px

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        x0 = min(m.x for m in self.monitors)
        y0 = min(m.y for m in self.monitors)
        x1 = max(m.x + m.width for m in self.monitors)
        y1 = max(m.y + m.height for m in self.monitors)
        return x0, y0, x1, y1

    @property
    def render_scale(self) -> float:
        return max(m.scale for m in self.monitors) if self.logical else 1.0

    def fingerprint(self) -> str:
        import hashlib
        key = [(m.connector, m.serial, m.x, m.y, m.width, m.height, m.scale) for m in self.monitors]
        return hashlib.sha1(json.dumps(key).encode()).hexdigest()[:10]


def available() -> bool:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if not ("gnome" in desktop or "unity" in desktop):
        return False
    try:
        _system_gsettings()
    except BackendError:
        return False
    return True


def _display_state_json() -> list:
    if shutil.which("busctl"):
        out = subprocess.run(
            ["busctl", "--user", "--json=short", "call", "org.gnome.Mutter.DisplayConfig",
             "/org/gnome/Mutter/DisplayConfig", "org.gnome.Mutter.DisplayConfig", "GetCurrentState"],
            capture_output=True, text=True)
        if out.returncode == 0:
            return json.loads(out.stdout)["data"]
        err = out.stderr.strip()
    else:
        err = "busctl not found"
    raise BackendError(f"could not query GNOME display layout ({err})")


def _variant(v):
    return v.get("data") if isinstance(v, dict) and "data" in v else v


def get_layout() -> OsLayout:
    _serial, monitors, logical_monitors, props = _display_state_json()
    info = {}
    for (spec, modes, mprops) in monitors:
        cur = next((md for md in modes if _variant(md[6].get("is-current", {})) is True), None)
        info[tuple(spec)] = {
            "mode": (cur[1], cur[2]) if cur else None,
            "display_name": _variant(mprops.get("display-name", {})) or spec[0],
            "builtin": bool(_variant(mprops.get("is-builtin", {}))),
        }
    layout_mode = _variant(props.get("layout-mode", {"data": 1}))
    logical = layout_mode != 2
    result = []
    for (x, y, scale, transform, primary, specs, _p) in logical_monitors:
        spec = tuple(specs[0])
        meta = info.get(spec, {})
        mw, mh = meta.get("mode") or (0, 0)
        rot = TRANSFORM_ROTATION.get(int(transform), 0)
        w, h = (mh, mw) if rot in (90, 270) else (mw, mh)
        if logical:
            w, h = int(round(w / scale)), int(round(h / scale))
        result.append(OsMonitor(
            connector=spec[0], vendor=spec[1], product=spec[2], serial=spec[3],
            display_name=meta.get("display_name", spec[0]), x=int(x), y=int(y), width=w, height=h,
            scale=float(scale), rotation=rot, mode=(mw, mh), primary=bool(primary),
            builtin=meta.get("builtin", False)))
    if not result:
        raise BackendError("GNOME reports no active monitors")
    return OsLayout(result, logical)


def match_monitors(config_monitors, os_layout: OsLayout) -> dict:
    """Return {config monitor name: OsMonitor}.

    Explicit ``connector`` / ``serial`` / ``product`` keys win; the rest are
    paired left-to-right by position in GNOME's layout.
    """
    remaining = sorted(os_layout.monitors, key=lambda m: (m.center_x, m.y))
    result = {}
    pending = []
    for cm in config_monitors:
        if cm.match:
            hit = [om for om in remaining if all(
                str(getattr(om, k, "")).lower() == str(v).lower() for k, v in cm.match.items())]
            if not hit:
                raise BackendError(f"monitor {cm.name!r}: nothing in GNOME matches {cm.match}")
            result[cm.name] = hit[0]
            remaining.remove(hit[0])
        else:
            pending.append(cm)
    # positional pairing keeps the configuration's left-to-right order
    if pending:
        if len(remaining) < len(pending):
            raise BackendError(f"configuration has {len(config_monitors)} monitors but GNOME has "
                               f"{len(os_layout.monitors)} active; add connector/serial keys or fix the layout")
        builtin_free = [om for om in remaining if not om.builtin]
        pool = builtin_free if len(builtin_free) >= len(pending) else remaining
        for cm, om in zip(pending, pool, strict=False):
            result[cm.name] = om
    return result


def compose(os_layout: OsLayout, render_for, background="#000000") -> Image.Image:
    """Build the spanned image. ``render_for(os_monitor, (w, h))`` returns an image or None."""
    k = os_layout.render_scale
    x0, y0, x1, y1 = os_layout.bbox
    W, H = int(round((x1 - x0) * k)), int(round((y1 - y0) * k))
    canvas = Image.new("RGB", (W, H), background)
    for om in os_layout.monitors:
        rx0, ry0 = int(round((om.x - x0) * k)), int(round((om.y - y0) * k))
        rx1, ry1 = int(round((om.x + om.width - x0) * k)), int(round((om.y + om.height - y0) * k))
        tile = render_for(om, (rx1 - rx0, ry1 - ry0))
        if tile is not None:
            canvas.paste(tile, (rx0, ry0))
    return canvas


# --------------------------------------------------------------------------- gsettings

def _system_gsettings() -> str:
    """The desktop's own gsettings binary.

    Conda/Anaconda (and some venvs/Flatpak SDKs) ship their own GLib whose
    gsettings has no dconf backend: it "succeeds" but writes to a throwaway
    memory store. Prefer the system binary and ignore ones inside an env.
    """
    for cand in ("/usr/bin/gsettings", "/bin/gsettings", "/usr/local/bin/gsettings"):
        if os.access(cand, os.X_OK):
            return cand
    for d in os.environ.get("PATH", "").split(os.pathsep):
        cand = os.path.join(d, "gsettings")
        low = cand.lower()
        if os.access(cand, os.X_OK) and not any(s in low for s in ("conda", "mamba", "venv", "/envs/")):
            return cand
    found = shutil.which("gsettings")
    if found:
        return found
    raise BackendError("gsettings not found")


def _env() -> dict:
    env = dict(os.environ)
    for var in ("GSETTINGS_BACKEND", "GIO_MODULE_DIR", "GSETTINGS_SCHEMA_DIR", "GIO_EXTRA_MODULES"):
        env.pop(var, None)
    return env


def _gset(key: str, value: str) -> None:
    subprocess.run([_system_gsettings(), "set", SCHEMA, key, value], check=True, env=_env())


def _gget(key: str) -> str:
    out = subprocess.run([_system_gsettings(), "get", SCHEMA, key], capture_output=True, text=True,
                         check=True, env=_env())
    return out.stdout.strip().strip("'")


def _dconf_read(key: str) -> str | None:
    """Read straight from dconf (what GNOME Shell actually uses), if dconf is installed."""
    dconf = "/usr/bin/dconf" if os.access("/usr/bin/dconf", os.X_OK) else shutil.which("dconf")
    if not dconf:
        return None
    out = subprocess.run([dconf, "read", f"/org/gnome/desktop/background/{key}"],
                         capture_output=True, text=True, env=_env())
    return out.stdout.strip().strip("'") if out.returncode == 0 else None


def set_wallpaper(path: Path, color: str = "#000000") -> None:
    uri = Path(path).resolve().as_uri()
    _gset("picture-options", "spanned")
    _gset("primary-color", color)
    _gset("color-shading-type", "solid")
    _gset("picture-uri", uri)
    _gset("picture-uri-dark", uri)
    stored = _dconf_read("picture-uri")
    if stored is None:
        stored = _gget("picture-uri")
    if stored != uri:
        raise BackendError(f"GNOME did not accept the wallpaper (picture-uri is {stored!r}). "
                           f"Is {_system_gsettings()} the desktop's gsettings?")


def current_uri() -> str:
    return _gget("picture-uri")


# --------------------------------------------------------------------------- slideshows

def write_slideshow(xml_path: Path, files: list[Path], interval: float, transition: float,
                    start: float | None = None) -> Path:
    """GNOME background XML. ``interval`` is the total time per image (incl. transition).

    GNOME shows image ``((now - start) mod cycle) // interval``, so ``start``
    controls which image is on screen; it defaults to now (first image).
    """
    transition = max(0.0, min(transition, interval / 2))
    root = ET.Element("background")
    st = ET.SubElement(root, "starttime")
    t = time.localtime(time.time() if start is None else start)
    for tag, val in (("year", t.tm_year), ("month", t.tm_mon), ("day", t.tm_mday),
                     ("hour", t.tm_hour), ("minute", t.tm_min), ("second", t.tm_sec)):
        ET.SubElement(st, tag).text = str(val)
    files = [Path(f).resolve() for f in files]
    if len(files) == 1:
        files = files * 2  # GNOME needs at least one transition loop to be happy
    for i, f in enumerate(files):
        static = ET.SubElement(root, "static")
        ET.SubElement(static, "duration").text = f"{interval - transition:.1f}"
        ET.SubElement(static, "file").text = str(f)
        if transition > 0:
            tr = ET.SubElement(root, "transition", type="overlay")
            ET.SubElement(tr, "duration").text = f"{transition:.1f}"
            ET.SubElement(tr, "from").text = str(f)
            ET.SubElement(tr, "to").text = str(files[(i + 1) % len(files)])
    ET.indent(root)
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(xml_path, encoding="utf-8", xml_declaration=True)
    return xml_path


def read_slideshow(xml_path: Path) -> tuple[list[Path], float, float]:
    root = ET.parse(xml_path).getroot()
    files, interval, transition = [], None, 0.0
    for el in root:
        if el.tag == "static":
            files.append(Path(el.findtext("file")))
            interval = interval or float(el.findtext("duration", "0"))
        elif el.tag == "transition":
            transition = float(el.findtext("duration", "0"))
    # undo the single-image duplication
    if len(files) == 2 and files[0] == files[1]:
        files = files[:1]
    return files, (interval or 0) + transition, transition
