"""Things every desktop backend shares: the desktop's monitor list and matching
it to the monitors in a configuration."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from itertools import permutations
from pathlib import Path

# wl_output / Mutter transform -> rotation in degrees (GNOME Settings sense:
# 90 = "Portrait Right"). 4-7 are the flipped variants.
TRANSFORM_ROTATION = {0: 0, 1: 90, 2: 180, 3: 270, 4: 0, 5: 90, 6: 180, 7: 270}


class BackendError(RuntimeError):
    pass


def link_dir(kind: str) -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "panescape" / kind


def unique_link(path: Path, kind: str) -> Path:
    """A link whose name changes whenever the file changes, for wallpaper tools that
    cache images by path (Plasma, hyprpaper)."""
    import shutil
    st = path.stat()
    d = link_dir(kind)
    d.mkdir(parents=True, exist_ok=True)
    link = d / f"{path.parent.name}-{path.stem}-{st.st_mtime_ns:x}-{st.st_size:x}{path.suffix}"
    if not link.exists():
        try:
            link.symlink_to(path)
        except OSError:
            shutil.copy2(path, link)
    return link


def prune_links(kind: str, keep: set[Path], max_links: int = 64) -> None:
    """Drop old links, keeping the newest few (a slideshow cycles through them)."""
    d = link_dir(kind)
    if not d.is_dir():
        return
    links = sorted(d.iterdir(), key=lambda p: p.lstat().st_mtime, reverse=True)
    for old in links[max_links:]:
        if old not in keep:
            old.unlink(missing_ok=True)


def running(name: str) -> bool:
    """Is a process with this name running for this user? (/proc, no psutil needed)"""
    proc = Path("/proc")
    if not proc.is_dir():
        return False
    uid = os.getuid()
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            if d.stat().st_uid == uid and (d / "comm").read_text().strip() == name[:15]:
                return True
        except OSError:
            continue
    return False


def _hydrapaper_daemon_enabled() -> bool:
    """hydrapaperd keeps running after it is switched off; its config says whether it acts."""
    home = Path.home()
    for cfg in (Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "org.gabmus.hydrapaper.json",
                home / ".var/app/org.gabmus.hydrapaper/config/org.gabmus.hydrapaper.json"):
        try:
            return bool(json.loads(cfg.read_text()).get("enable_daemon", False))
        except (OSError, ValueError):
            continue
    return True  # running but no readable config: assume it acts


# Programs that set the wallpaper on their own and so replace panescape's.
# name -> (process name, extra check, advice)
WALLPAPER_TOOLS = {
    "HydraPaper": ("hydrapaperd", _hydrapaper_daemon_enabled,
                   "its background service re-applies HydraPaper's wallpaper whenever the displays change "
                   "(e.g. after the monitors sleep). Switch the daemon off in HydraPaper's preferences."),
    "Variety": ("variety", None, "it changes the wallpaper on its own schedule. Quit Variety, or turn off "
                "its automatic changes."),
}


def competing_tools() -> list[str]:
    """Warnings about other running programs that will replace panescape's wallpaper."""
    out = []
    for name, (proc, check, advice) in WALLPAPER_TOOLS.items():
        try:
            if running(proc) and (check is None or check()):
                out.append(f"{name} is running and will replace panescape's wallpaper: {advice}")
        except Exception:  # noqa: BLE001 - a warning must never break applying
            continue
    return out


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
        key = [(m.connector, m.serial, m.x, m.y, m.width, m.height, m.scale) for m in self.monitors]
        return hashlib.sha1(json.dumps(key).encode()).hexdigest()[:10]


def match_monitors(config_monitors, os_layout: OsLayout, centres: dict | None = None) -> dict:
    """Return {config monitor name: OsMonitor}.

    Explicit ``connector`` / ``serial`` / ``product`` keys win. The rest are
    paired by arrangement: ``centres`` ({name: (x, y)} in mm, from the physical
    layout) is compared with GNOME's layout after normalising both to their
    bounding boxes, so rows, columns and grids pair up. Without ``centres``
    they are paired left to right.
    """
    remaining = sorted(os_layout.monitors, key=lambda m: (m.center_x, m.y))
    result = {}
    pending = []
    for cm in config_monitors:
        if cm.match:
            hit = [om for om in remaining if all(
                str(getattr(om, k, "")).lower() == str(v).lower() for k, v in cm.match.items())]
            if not hit:
                raise BackendError(f"monitor {cm.name!r}: no monitor on the desktop matches {cm.match}")
            result[cm.name] = hit[0]
            remaining.remove(hit[0])
        else:
            pending.append(cm)
    if not pending:
        return result
    if len(remaining) < len(pending):
        raise BackendError(f"configuration has {len(config_monitors)} monitors but the desktop has "
                           f"{len(os_layout.monitors)} active; add connector/serial keys or fix the layout")
    builtin_free = [om for om in remaining if not om.builtin]
    pool = builtin_free if len(builtin_free) >= len(pending) else remaining
    if centres and len(pending) > 1:
        pairs = _match_by_arrangement(pending, pool, centres)
    else:
        pairs = list(zip(pending, pool, strict=False))  # pool may be larger
    for cm, om in pairs:
        result[cm.name] = om
    return result


def _normalise(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Scale to the bounding box, keeping proportions (one factor for both axes),
    so a small vertical offset in a row stays small."""
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
    return [((x - min(xs)) / span, (y - min(ys)) / span) for x, y in points]


def _match_by_arrangement(pending, pool, centres) -> list:
    """Assign config monitors to OS monitors minimising the normalised distance."""

    cfg_pts = _normalise([centres[cm.name] for cm in pending])
    os_pts = _normalise([(om.center_x, om.y + om.height / 2) for om in pool])

    def cost(i: int, j: int) -> float:
        return (cfg_pts[i][0] - os_pts[j][0]) ** 2 + (cfg_pts[i][1] - os_pts[j][1]) ** 2

    n = len(pending)
    if len(pool) <= 8:
        best = min(permutations(range(len(pool)), n),
                   key=lambda perm: sum(cost(i, j) for i, j in enumerate(perm)))
        return [(pending[i], pool[j]) for i, j in enumerate(best)]
    used, pairs = set(), []  # large setups: greedy
    for i in range(n):
        j = min((j for j in range(len(pool)) if j not in used), key=lambda j: cost(i, j))
        used.add(j)
        pairs.append((pending[i], pool[j]))
    return pairs


def native_size(om: OsMonitor) -> tuple[int, int]:
    """Pixel size of the output as the wallpaper sees it (rotated)."""
    w, h = om.mode
    return (h, w) if om.rotation in (90, 270) else (w, h)
