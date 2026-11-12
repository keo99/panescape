"""Physical layout: where every monitor's housing and lit area sit, in millimetres.

Coordinates: x to the right, y downwards, origin arbitrary (normalised so the
union of the active areas starts at 0,0).
"""

from __future__ import annotations

from dataclasses import dataclass

from . import perspective
from .config import Configuration, Monitor


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def x1(self) -> float:
        return self.x + self.w

    @property
    def y1(self) -> float:
        return self.y + self.h

    def shifted(self, dx: float, dy: float) -> Rect:
        return Rect(self.x + dx, self.y + dy, self.w, self.h)

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (round(self.x, 3), round(self.y, 3), round(self.w, 3), round(self.h, 3))


@dataclass
class Placed:
    monitor: Monitor
    outer: Rect
    active: Rect
    # angled screens (a layout with a viewer): lit-area mm from its top-left -> picture mm.
    # None when the screen shows its flat rectangle of the picture, as without a viewer.
    warp: tuple | None = None
    quad: list | None = None   # its lit corners on the picture (TL, TR, BR, BL), when warped

    def outline(self) -> list[tuple[float, float]]:
        """The lit area's corners on the picture (TL, TR, BR, BL)."""
        if self.quad:
            return list(self.quad)
        a = self.active
        return [(a.x, a.y), (a.x1, a.y), (a.x1, a.y1), (a.x, a.y1)]

    def outer_outline(self) -> list[tuple[float, float]]:
        """The housing's corners, where they appear on the picture (TL, TR, BR, BL)."""
        o, a = self.outer, self.active
        corners = [(o.x, o.y), (o.x1, o.y), (o.x1, o.y1), (o.x, o.y1)]
        if self.warp is None:
            return corners
        return [perspective.map_point(self.warp, x - a.x, y - a.y) for x, y in corners]


@dataclass
class Layout:
    placed: list[Placed]
    canvas: Rect   # bounding box of what the screens show of the picture (normalised to x=y=0)
    eye: tuple | None = None   # (x, y, z) mm with a viewer: z is the distance in front of the first screen

    def __iter__(self):
        return iter(self.placed)


def _outer_y(m: Monitor, ref: Rect) -> float:
    """Top of m's housing so that its v_align edge is v_pos mm *above* ref's."""
    _, h = m.outer
    if m.v_align == "top":
        return ref.y - m.v_pos
    if m.v_align == "bottom":
        return ref.y1 - m.v_pos - h
    return (ref.y + ref.h / 2) - m.v_pos - h / 2


def _outer_x(m: Monitor, ref: Rect) -> float:
    """Left of m's housing so that its h_align edge is h_pos mm *right* of ref's."""
    w, _ = m.outer
    if m.h_align == "left":
        return ref.x + m.h_pos
    if m.h_align == "right":
        return ref.x1 + m.h_pos - w
    return (ref.x + ref.w / 2) + m.h_pos - w / 2


def _dependencies(cfg: Configuration, i: int) -> tuple[str | None, list[str]]:
    """(placement rule, monitors that must be placed first) for monitor i.

    Rules: 'origin', 'position', 'right_of', 'left_of', 'below', 'above'.
    A monitor with no placement keys sits right of the previous one in the
    list, with v_pos measured from the first monitor (a simple row).
    """
    m = cfg.monitors[i]
    if m.position is not None:
        return "position", []
    if m.attach is None:
        if i == 0:
            return "origin", []
        side, target = "right_of", cfg.monitors[i - 1].name
        vref = m.v_ref or cfg.monitors[0].name
        return side, [target, vref]
    side, target = m.attach
    if side in ("right_of", "left_of"):
        return side, [target, m.v_ref or target]
    return side, [target, m.h_ref or target]


def compute_layout(cfg: Configuration) -> Layout:
    outer: dict[str, Rect] = {}
    hinges: dict[str, tuple] = {}   # name -> (parent name or None, pivot (x, y))
    pending = list(range(len(cfg.monitors)))
    order: list[Monitor] = []       # parents before children
    while pending:
        progressed = False
        for i in list(pending):
            m = cfg.monitors[i]
            rule, deps = _dependencies(cfg, i)
            if any(d not in outer for d in deps):
                continue
            ow, oh = m.outer
            if rule == "position":
                r = Rect(m.position[0], m.position[1], ow, oh)
                hinges[m.name] = (None, (r.x + r.w / 2, r.y + r.h / 2))
            elif rule == "origin":
                r = Rect(0.0, -oh / 2, ow, oh)
                hinges[m.name] = (None, (r.x + r.w / 2, r.y + r.h / 2))
            else:
                target = outer[deps[0]]
                ref = outer[deps[1]]
                if rule == "right_of":
                    r = Rect(target.x1 + m.gap, _outer_y(m, ref), ow, oh)
                elif rule == "left_of":
                    r = Rect(target.x - m.gap - ow, _outer_y(m, ref), ow, oh)
                elif rule == "below":
                    r = Rect(_outer_x(m, ref), target.y1 + m.gap, ow, oh)
                else:  # above
                    r = Rect(_outer_x(m, ref), target.y - m.gap - oh, ow, oh)
                hinges[m.name] = (deps[0], _hinge(rule, target, r))
            outer[m.name] = r
            order.append(m)
            pending.remove(i)
            progressed = True
        if not progressed:
            names = ", ".join(cfg.monitors[i].name for i in pending)
            raise ValueError(f"monitor placement refers in a circle: {names}")

    placed = []
    for m in cfg.monitors:
        o = outer[m.name]
        aw, ah = m.active
        placed.append(Placed(m, o, Rect(o.x + m.bezel["left"], o.y + m.bezel["top"], aw, ah)))

    eye = _fold_and_project(cfg, placed, order, hinges) if cfg.viewer else None

    corners = [c for p in placed for c in p.outline()]
    x0 = min(c[0] for c in corners)
    y0 = min(c[1] for c in corners)
    x1 = max(c[0] for c in corners)
    y1 = max(c[1] for c in corners)
    for p in placed:
        p.outer = p.outer.shifted(-x0, -y0)
        p.active = p.active.shifted(-x0, -y0)
        if p.quad:
            p.quad = [(x - x0, y - y0) for x, y in p.quad]
            p.warp = perspective.solve_homography(_local_corners(p), p.quad)
    if eye:
        eye = (eye[0] - x0, eye[1] - y0, eye[2])
    return Layout(placed, Rect(0.0, 0.0, x1 - x0, y1 - y0), eye)


def _hinge(rule: str, target: Rect, r: Rect) -> tuple[float, float]:
    """A point on the edge where monitor r meets the one it's placed against."""
    if rule in ("right_of", "left_of"):
        x = target.x1 if rule == "right_of" else target.x
        lo, hi = max(r.y, target.y), min(r.y1, target.y1)
        return (x, (lo + hi) / 2 if hi > lo else r.y + r.h / 2)
    y = target.y1 if rule == "below" else target.y
    lo, hi = max(r.x, target.x), min(r.x1, target.x1)
    return ((lo + hi) / 2 if hi > lo else r.x + r.w / 2, y)


def _local_corners(p: Placed) -> list[tuple[float, float]]:
    w, h = p.active.w, p.active.h
    return [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)]


def _fold_and_project(cfg: Configuration, placed: list[Placed], order: list[Monitor], hinges: dict) -> tuple:
    """Fold the flat layout into 3D and project every lit area from the eye onto
    the first monitor's plane. Sets ``quad`` on screens that come out of that plane."""
    viewer = cfg.viewer
    first = next(p for p in placed if p.monitor.index == 0)
    a = first.active
    eye = (a.x + a.w / 2 + viewer.offset, a.y + a.h / 2 - viewer.height, viewer.distance)
    base_tilt = first.monitor.tilt
    poses: dict[str, perspective.Pose] = {}
    by_name = {p.monitor.name: p for p in placed}
    for m in order:
        parent, pivot = hinges[m.name]
        r = by_name[m.name].active
        toward = 1.0 if r.x + r.w / 2 >= eye[0] else -1.0  # "turned towards you" from either side
        angle = 0.0 if m.index == 0 else m.angle * toward
        rot = perspective.rotation(angle, m.tilt - base_tilt)
        origin = poses[parent].point(*pivot) if parent else (pivot[0], pivot[1], 0.0)
        poses[m.name] = perspective.Pose(pivot, origin, rot)
    for p in placed:
        pose = poses[p.monitor.name]
        flat = p.outline()
        pts = [pose.point(x, y) for x, y in flat]
        if all(abs(z) < 1e-9 for _, _, z in pts):
            continue  # in the picture's plane: its flat rectangle, exactly as without a viewer
        try:
            p.quad = [perspective.project(eye, q) for q in pts]
        except ValueError:
            raise ValueError(f"monitor {p.monitor.name!r} reaches past your eyes: "
                             "check its angle and tilt, or move the viewer further back") from None
    return eye
