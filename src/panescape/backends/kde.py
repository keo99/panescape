"""KDE Plasma 6 (Wayland or X11).

Plasma sets a wallpaper per screen, so each monitor gets its native slice.

Layout:   ``kscreen-doctor -j`` (connector names, positions, modes, scale,
          rotation).
Setting:  Plasma's scripting interface over D-Bus
          (``org.kde.PlasmaShell.evaluateScript``), which can configure the
          wallpaper of each screen's desktop containment.

Plasma screens are numbered, not named, so the script first reports every
screen's geometry and outputs are matched to screens by position. Plasma
caches images by URL, so each image is handed over through a uniquely named
link in ``~/.cache/panescape/kde`` (otherwise a re-rendered slice with the
same file name might not show up). Slideshows are run by ``panescape
slideshow``, as Plasma's own slideshow can't keep screens in step.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .base import (  # noqa: F401 - part of the backend interface
    BackendError,
    OsLayout,
    OsMonitor,
    link_dir,
    match_monitors,
    native_size,
    prune_links,
    unique_link,
)

KIND = "per_output"
# KScreen::Output::Rotation -> degrees (GNOME Settings sense, 90 = "Portrait Right")
KSCREEN_ROTATION = {1: 0, 2: 90, 4: 180, 8: 270}
FILL_PRESERVE_ASPECT_CROP = 2


def available() -> bool:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    return "kde" in desktop and bool(shutil.which("busctl") or shutil.which("gdbus"))


def _run(cmd: list[str]) -> str:
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise BackendError(f"{cmd[0]} failed: {(out.stderr or out.stdout).strip()}")
    return out.stdout


# --------------------------------------------------------------------------- layout

def parse_kscreen(data: dict) -> OsLayout:
    mons = []
    for o in data.get("outputs", []):
        if not (o.get("enabled") and o.get("connected", True)):
            continue
        mode = next((m for m in o.get("modes", []) if str(m.get("id")) == str(o.get("currentModeId"))), None)
        size = (mode or {}).get("size") or o.get("size") or {}
        mw, mh = int(size.get("width", 0)), int(size.get("height", 0))
        rot = KSCREEN_ROTATION.get(int(o.get("rotation", 1)), 0)
        scale = float(o.get("scale") or 1.0)
        lw, lh = (mh, mw) if rot in (90, 270) else (mw, mh)
        name = o.get("name", "")
        mm = o.get("sizeMM") or o.get("sizeMm") or {}
        try:
            physical = (float(mm["width"]), float(mm["height"])) if mm else None
        except (KeyError, TypeError, ValueError):
            physical = None
        mons.append(OsMonitor(
            connector=name, vendor=o.get("vendor", "") or "", product=o.get("model", "") or "",
            serial=o.get("serial", "") or "", display_name=o.get("model") or name,
            x=int(o.get("pos", {}).get("x", 0)), y=int(o.get("pos", {}).get("y", 0)),
            width=round(lw / scale), height=round(lh / scale), scale=scale, rotation=rot,
            mode=(mw, mh), primary=o.get("priority") == 1,
            builtin=str(o.get("type", "")).lower() in ("panel", "embedded") or name.startswith(("eDP", "LVDS")),
            physical_mm=physical))
    if not mons:
        raise BackendError("kscreen-doctor reports no enabled outputs")
    return OsLayout(mons, logical=True)


def get_layout() -> OsLayout:
    tool = shutil.which("kscreen-doctor")
    if not tool:
        raise BackendError("kscreen-doctor not found (install libkscreen / libkscreen-bin)")
    layout = parse_kscreen(json.loads(_run([tool, "-j"])))
    try:  # Plasma's own geometry is the authority on logical sizes
        refine_with_plasma(layout, plasma_screens())
    except BackendError:
        pass
    return layout


def refine_with_plasma(layout: OsLayout, screens: list[dict]) -> None:
    """Use Plasma's logical screen sizes (kscreen's mode / scale can disagree,
    e.g. in nested sessions)."""
    for om in layout.monitors:
        idx = screen_for(om, screens)
        s = next((s for s in screens if s["screen"] == idx), None)
        if s is not None:
            om.width, om.height = int(s["width"]), int(s["height"])


# --------------------------------------------------------------------------- plasma scripting

def evaluate(script: str) -> str:
    """Run a Plasma desktop script and return what it print()ed."""
    if shutil.which("busctl"):
        out = _run(["busctl", "--user", "--json=short", "call", "org.kde.plasmashell", "/PlasmaShell",
                    "org.kde.PlasmaShell", "evaluateScript", "s", script])
        data = json.loads(out).get("data") if out.strip() else None
        return data[0] if data else ""
    out = _run(["gdbus", "call", "--session", "--dest", "org.kde.plasmashell", "--object-path", "/PlasmaShell",
                "--method", "org.kde.PlasmaShell.evaluateScript", script])
    return out.strip().removeprefix("('").removesuffix("',)")


SCREENS_JS = """
var out = [];
var seen = {};
desktops().forEach(function (d) {
    if (d.screen < 0 || seen[d.screen]) return;
    seen[d.screen] = true;
    var g = screenGeometry(d.screen);
    out.push({screen: d.screen, x: g.x, y: g.y, width: g.width, height: g.height});
});
print(JSON.stringify(out));
"""


def plasma_screens() -> list[dict]:
    text = evaluate(SCREENS_JS).strip()
    try:
        return json.loads(text.splitlines()[-1]) if text else []
    except (json.JSONDecodeError, IndexError) as e:
        raise BackendError(f"unexpected answer from Plasma: {text[:200]!r}") from e


def screen_for(om: OsMonitor, screens: list[dict]) -> int | None:
    """Plasma screen number showing this output, matched by position (outputs
    can't share a top-left corner; sizes can disagree between kscreen and Plasma)."""
    def dist(s):
        return abs(s["x"] - om.x) + abs(s["y"] - om.y)
    best = min(screens, key=lambda s: (dist(s), abs(s["width"] - om.width)), default=None)
    return None if best is None or dist(best) > 8 else best["screen"]


def _cache_dir() -> Path:
    return link_dir("kde")


def _unique_link(path: Path) -> Path:
    return unique_link(path, "kde")


def set_script(by_screen: dict[int, Path], fill_mode: int = FILL_PRESERVE_ASPECT_CROP) -> str:
    # absolute(), not resolve(): the unique link name is what makes Plasma reload
    urls = {str(k): Path(v).absolute().as_uri() for k, v in by_screen.items()}
    return f"""
var urls = {json.dumps(urls)};
desktops().forEach(function (d) {{
    var url = urls[String(d.screen)];
    if (url === undefined) return;
    d.wallpaperPlugin = "org.kde.image";
    d.currentConfigGroup = ["Wallpaper", "org.kde.image", "General"];
    d.writeConfig("Image", url);
    d.writeConfig("FillMode", {int(fill_mode)});
}});
"""


def set_outputs(outputs: dict[str, Path], transition: float = 0.0, setter: str | None = None,
                color: str = "#000000") -> None:
    """Show ``outputs[connector]`` on each output (all activities)."""
    layout = get_layout()
    screens = plasma_screens()
    by_screen: dict[int, Path] = {}
    for om in layout.monitors:
        if om.connector not in outputs:
            continue
        idx = screen_for(om, screens)
        if idx is None:
            raise BackendError(f"could not find Plasma's screen for {om.connector} at ({om.x},{om.y})")
        by_screen[idx] = _unique_link(Path(outputs[om.connector]).resolve())
    if by_screen:
        evaluate(set_script(by_screen))
        _prune_links(set(by_screen.values()))


def _prune_links(keep: set[Path], max_links: int = 64) -> None:
    prune_links("kde", keep, max_links)
