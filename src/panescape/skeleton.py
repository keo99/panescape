"""Turn the desktop's monitor arrangement into a starting configuration.

The desktop only knows a pixel arrangement, not millimetres, so the result
gets the *structure* right (who is next to / below whom, which edges line
up) and leaves the measured numbers (gap, v_pos, h_pos, bezel) at 0 for
the user to fill in, ideally with ``panescape calibrate --watch``.
"""

from __future__ import annotations

from . import models
from .config import ROTATION_NAMES

TOL = 8  # logical px: edges closer than this count as aligned / touching


def _overlap(a0, a1, b0, b1) -> float:
    return min(a1, b1) - max(a0, b0)


def _rows(mons) -> list[list]:
    """Group monitors into rows by vertical overlap, top to bottom."""
    rows: list[list] = []
    for m in sorted(mons, key=lambda m: m.y + m.height / 2):
        for row in rows:
            if all(_overlap(m.y, m.y + m.height, o.y, o.y + o.height) > min(m.height, o.height) / 3 for o in row):
                row.append(m)
                break
        else:
            rows.append([m])
    return [sorted(r, key=lambda m: m.x) for r in rows]


def _names(mons, rows) -> dict:
    col_names = {1: [""], 2: ["left", "right"], 3: ["left", "centre", "right"]}
    row_names = {1: [""], 2: ["top", "bottom"], 3: ["top", "middle", "bottom"]}
    if len(rows) in row_names and all(len(r) in col_names for r in rows):
        names = {}
        for r_i, row in enumerate(rows):
            for c_i, m in enumerate(row):
                parts = [row_names[len(rows)][r_i], col_names[len(row)][c_i]]
                names[id(m)] = "-".join(p for p in parts if p) or "main"
        if len(set(names.values())) == len(mons):
            return names
    return {id(m): f"monitor{i + 1}" for i, m in enumerate(mons)}


def _relation(u, p) -> tuple[str, float] | None:
    """How unplaced monitor u sits relative to placed monitor p (side, edge distance)."""
    h = _overlap(u.x, u.x + u.width, p.x, p.x + p.width) > TOL
    v = _overlap(u.y, u.y + u.height, p.y, p.y + p.height) > TOL
    if v and u.x >= p.x + p.width - TOL:
        return "right_of", u.x - (p.x + p.width)
    if v and u.x + u.width <= p.x + TOL:
        return "left_of", p.x - (u.x + u.width)
    if h and u.y >= p.y + p.height - TOL:
        return "below", u.y - (p.y + p.height)
    if h and u.y + u.height <= p.y + TOL:
        return "above", p.y - (u.y + u.height)
    return None


def _align(a0, a1, b0, b1, low: str, high: str) -> str:
    if abs(a0 - b0) <= TOL:
        return low
    if abs(a1 - b1) <= TOL:
        return high
    return "center"


def usable_serial(m, mons) -> bool:
    """Only pin by serial when it identifies the screen (compositors report
    missing serials as '', 'Unknown' or 0x00000000)."""
    serial = str(m.serial or "").strip()
    if serial.lower() in ("", "unknown", "0", "0x00000000", "none"):
        return False
    return sum(1 for o in mons if str(o.serial or "").strip() == serial) == 1


def config_skeleton(mons, name: str) -> dict:
    mons = list(mons)
    rows = _rows(mons)
    names = _names(mons, rows)
    single_row = len(rows) == 1
    order = [m for row in rows for m in row]
    entries, placed = [], []
    for m in order:
        e = {"name": names[id(m)]}
        known = models.lookup(m.product)
        if known:
            e["model"] = known["model"].title()
        else:
            e["resolution"] = f"{m.mode[0]}x{m.mode[1]}"
            e["size"] = "TODO"  # diagonal, e.g. 27in, or use active_area: [w, h] mm
        if usable_serial(m, mons):
            e["serial"] = m.serial
        e["rotation"] = ROTATION_NAMES[m.rotation]
        if placed and single_row:
            e["gap"] = 0
            e["v_pos"] = 0
        elif placed:
            rels = [(r, p) for p in placed if (r := _relation(m, p))]
            if not rels:  # diagonal neighbour only: attach to the nearest on the right/below
                p = min(placed, key=lambda p: abs(p.x - m.x) + abs(p.y - m.y))
                rels = [(("right_of" if m.x >= p.x else "left_of", 0.0), p)]
            # nearest neighbour, prefer side-by-side over stacked on ties
            (side, _dist), p = min(rels, key=lambda rp: (rp[0][1], rp[0][0] not in ("right_of", "left_of")))
            e[side] = names[id(p)]
            e["gap"] = 0
            if side in ("right_of", "left_of"):
                e["v_align"] = _align(m.y, m.y + m.height, p.y, p.y + p.height, "top", "bottom")
                e["v_pos"] = 0
            else:
                e["h_align"] = _align(m.x, m.x + m.width, p.x, p.x + p.width, "left", "right")
                e["h_pos"] = 0
        entries.append(e)
        placed.append(m)
    return {"name": name, "monitors": entries}
