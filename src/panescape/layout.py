"""Physical layout: where every monitor's housing and lit area sit, in millimetres.

Coordinates: x to the right, y downwards, origin arbitrary (normalised so the
union of the active areas starts at 0,0).
"""

from __future__ import annotations

from dataclasses import dataclass

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


@dataclass
class Layout:
    placed: list[Placed]
    canvas: Rect   # bounding box of active areas (after normalisation x=y=0)

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
    pending = list(range(len(cfg.monitors)))
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
            elif rule == "origin":
                r = Rect(0.0, -oh / 2, ow, oh)
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
            outer[m.name] = r
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

    x0 = min(p.active.x for p in placed)
    y0 = min(p.active.y for p in placed)
    x1 = max(p.active.x1 for p in placed)
    y1 = max(p.active.y1 for p in placed)
    for p in placed:
        p.outer = p.outer.shifted(-x0, -y0)
        p.active = p.active.shifted(-x0, -y0)
    return Layout(placed, Rect(0.0, 0.0, x1 - x0, y1 - y0))
