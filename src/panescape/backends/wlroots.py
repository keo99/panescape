"""sway / Hyprland (wlroots-style compositors).

These desktops set a wallpaper *per output*, so each monitor simply gets its
native-resolution slice; no combined image is needed.

Layout:  ``swaymsg -t get_outputs -r``  or  ``hyprctl monitors -j``.
Setter (first that works, or ``PANESCAPE_SETTER`` / ``setter:`` in setup.yml):

* ``sway``      ``swaymsg output NAME bg FILE fill`` (sway runs swaybg itself)
* ``hyprpaper`` ``hyprctl hyprpaper wallpaper 'NAME, FILE, cover'``
* ``swww``      ``swww img --outputs NAME FILE`` (animated transitions)
* ``swaybg``    a ``swaybg`` process started and replaced by panescape

There is no native slideshow, so ``panescape slideshow`` switches all
outputs together on schedule (``apply`` starts it when needed).
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
from pathlib import Path

from .base import (  # noqa: F401 - match_monitors is part of the backend interface
    TRANSFORM_ROTATION,
    BackendError,
    OsLayout,
    OsMonitor,
    match_monitors,
    native_size,
    prune_links,
    unique_link,
)
from .base import running as _running

KIND = "per_output"  # one image per output
SETTERS = ("sway", "hyprpaper", "swww", "swaybg")
SWAY_TRANSFORMS = {"normal": 0, "90": 90, "180": 180, "270": 270,
                   "flipped": 0, "flipped-90": 90, "flipped-180": 180, "flipped-270": 270}


def compositor() -> str | None:
    if os.environ.get("SWAYSOCK") and shutil.which("swaymsg"):
        return "sway"
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        return "hyprland"
    return None


def available() -> bool:
    return compositor() is not None


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    out = subprocess.run(cmd, capture_output=True, text=True)
    if check and out.returncode != 0:
        raise BackendError(f"{shlex.join(cmd)} failed: {(out.stderr or out.stdout).strip()}")
    return out


# --------------------------------------------------------------------------- layout

def parse_sway_outputs(data: list) -> OsLayout:
    mons = []
    for o in data:
        if not o.get("active", True) or not o.get("rect"):
            continue
        r = o["rect"]
        mode = o.get("current_mode") or {}
        mons.append(OsMonitor(
            connector=o["name"], vendor=o.get("make", ""), product=o.get("model", ""),
            serial=o.get("serial", ""), display_name=f"{o.get('make', '')} {o.get('model', '')}".strip(),
            x=int(r["x"]), y=int(r["y"]), width=int(r["width"]), height=int(r["height"]),
            scale=float(o.get("scale") or 1.0), rotation=SWAY_TRANSFORMS.get(str(o.get("transform", "normal")), 0),
            mode=(int(mode.get("width", r["width"])), int(mode.get("height", r["height"]))),
            primary=bool(o.get("focused")), builtin=o["name"].startswith(("eDP", "LVDS", "DSI"))))
    if not mons:
        raise BackendError("sway reports no active outputs")
    return OsLayout(mons, logical=True)


def parse_hyprland_monitors(data: list) -> OsLayout:
    mons = []
    for o in data:
        if o.get("disabled"):
            continue
        scale = float(o.get("scale") or 1.0)
        rot = TRANSFORM_ROTATION.get(int(o.get("transform", 0)), 0)
        mw, mh = int(o["width"]), int(o["height"])          # mode, before rotation
        lw, lh = (mh, mw) if rot in (90, 270) else (mw, mh)
        mons.append(OsMonitor(
            connector=o["name"], vendor=o.get("make", ""), product=o.get("model", ""),
            serial=o.get("serial", ""), display_name=o.get("description") or o["name"],
            x=int(o["x"]), y=int(o["y"]), width=round(lw / scale), height=round(lh / scale),
            scale=scale, rotation=rot, mode=(mw, mh), primary=bool(o.get("focused")),
            builtin=o["name"].startswith(("eDP", "LVDS", "DSI"))))
    if not mons:
        raise BackendError("Hyprland reports no active monitors")
    return OsLayout(mons, logical=True)


def get_layout() -> OsLayout:
    comp = compositor()
    if comp == "sway":
        return parse_sway_outputs(json.loads(_run(["swaymsg", "-t", "get_outputs", "-r"]).stdout))
    if comp == "hyprland":
        return parse_hyprland_monitors(json.loads(_run(["hyprctl", "monitors", "-j"]).stdout))
    raise BackendError("not running under sway or Hyprland")


# --------------------------------------------------------------------------- setters

def choose_setter(preferred: str | None = None) -> str:
    preferred = preferred or os.environ.get("PANESCAPE_SETTER")
    if preferred:
        if preferred not in SETTERS:
            raise BackendError(f"unknown setter {preferred!r}; use one of {', '.join(SETTERS)}")
        return preferred
    comp = compositor()
    if comp == "sway":
        return "sway"
    # hyprctl's request list differs between versions, so check for the daemons themselves
    if comp == "hyprland" and _running("hyprpaper"):
        return "hyprpaper"
    if (_running("swww-daemon") or _running("awww-daemon")) and shutil.which("swww"):
        return "swww"
    if shutil.which("swaybg"):
        return "swaybg"
    raise BackendError("no wallpaper tool found: install hyprpaper, swww or swaybg "
                       "(or start hyprpaper / swww-daemon)")


def _pidfile() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return Path(runtime) / "panescape-swaybg.pid"


def _set_swaybg(outputs: dict[str, Path], color: str) -> None:
    pf = _pidfile()
    try:
        old = int(pf.read_text())
        os.kill(old, signal.SIGTERM)
    except (OSError, ValueError):
        pass
    cmd = ["swaybg"]
    for name, path in outputs.items():
        cmd += ["-o", name, "-i", str(path), "-m", "fill", "-c", color]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
    try:  # swaybg exits straight away if it can't draw (wrong WAYLAND_DISPLAY, no layer-shell)
        _, err = proc.communicate(timeout=0.5)
        raise BackendError(f"swaybg exited: {err.decode(errors='replace').strip() or proc.returncode} "
                           f"(is WAYLAND_DISPLAY this session's? try 'setter:' hyprpaper or swww)")
    except subprocess.TimeoutExpired:
        proc.stderr.close()
    pf.write_text(str(proc.pid))


def set_outputs(outputs: dict[str, Path], transition: float = 0.0, setter: str | None = None,
                color: str = "#000000") -> None:
    """Show ``outputs[name]`` on each named output."""
    outputs = {n: Path(p).resolve() for n, p in outputs.items()}
    tool = choose_setter(setter)
    if tool == "sway":
        cmd = "; ".join(f"output {shlex.quote(n)} bg {shlex.quote(str(p))} fill {color}" for n, p in outputs.items())
        _run(["swaymsg", cmd])
    elif tool == "hyprpaper":
        # hyprpaper keeps loaded images by path: a re-rendered slice needs a new name
        outputs = {n: unique_link(p, "hyprpaper").absolute() for n, p in outputs.items()}
        prune_links("hyprpaper", set(outputs.values()))
        for n, p in outputs.items():
            out = _run(["hyprctl", "hyprpaper", "wallpaper", f"{n}, {p}, cover"], check=False)
            if out.returncode != 0 or "not preloaded" in out.stdout.lower():  # hyprpaper < 0.8
                _run(["hyprctl", "hyprpaper", "preload", str(p)])
                _run(["hyprctl", "hyprpaper", "wallpaper", f"{n},{p}"])
            elif "error" in out.stdout.lower() or "invalid" in out.stdout.lower():
                raise BackendError(f"hyprpaper: {out.stdout.strip()}")
    elif tool == "swww":
        for n, p in outputs.items():
            _run(["swww", "img", "--outputs", n, str(p), "--resize", "crop",
                  "--transition-type", "fade" if transition > 0 else "none",
                  "--transition-duration", f"{max(transition, 0.0):g}"])
    else:
        _set_swaybg(outputs, color)
