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


def compute_layout(cfg: Configuration) -> Layout:
    placed: list[Placed] = []
    by_name: dict[str, Rect] = {}
    cursor_x = 0.0
    for i, m in enumerate(cfg.monitors):
        ow, oh = m.outer
        if m.position is not None:
            outer = Rect(m.position[0], m.position[1], ow, oh)
        elif i == 0:
            outer = Rect(0.0, -oh / 2, ow, oh)
        else:
            x = cursor_x + m.gap
            ref_name = m.v_ref or cfg.monitors[0].name
            ref = by_name.get(ref_name)
            if ref is None:
                raise ValueError(f"monitor {m.name!r}: v_ref {ref_name!r} must be listed before it")
            outer = Rect(x, 0.0, ow, oh)
            outer.y = _outer_y(m, ref)
        aw, ah = m.active
        active = Rect(outer.x + m.bezel["left"], outer.y + m.bezel["top"], aw, ah)
        placed.append(Placed(m, outer, active))
        by_name[m.name] = outer
        cursor_x = outer.x1

    x0 = min(p.active.x for p in placed)
    y0 = min(p.active.y for p in placed)
    x1 = max(p.active.x1 for p in placed)
    y1 = max(p.active.y1 for p in placed)
    for p in placed:
        p.outer = p.outer.shifted(-x0, -y0)
        p.active = p.active.shifted(-x0, -y0)
    return Layout(placed, Rect(0.0, 0.0, x1 - x0, y1 - y0))
