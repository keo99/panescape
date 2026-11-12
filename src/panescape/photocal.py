"""Calibrating a layout from one photo taken from your seat.

The screens show the photo pattern: a white panel on black, inset by a known
amount. A phone held where your eyes are sees the desk as you do, so:

1. find each screen's white panel in the photo (a bright quadrilateral) and its
   four corners, to a fraction of a pixel, by fitting lines to its edges;
2. pair the panels with the layout's monitors by arrangement;
3. adjust the layout's numbers (gaps, offsets, angles, tilts) and the eye
   position, together with the phone's direction and zoom, until the layout
   seen from that eye puts every corner where the photo shows it
   (Levenberg-Marquardt on the corner positions).

Needs numpy, and pillow-heif for iPhone photos: the ``photo`` extra.
No OpenCV: the screens are made easy to find on purpose.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageOps

INSTALL_HINT = ("Calibrating from a photo needs the 'photo' extra:\n"
                "    pipx inject panescape numpy pillow-heif\n"
                "or  pip install 'panescape[photo]'")
MAX_SIDE = 2400  # px: the photo is analysed at this size (plenty for sub-pixel corners)


def missing() -> str | None:
    """None when photo calibration can run, else what to install."""
    try:
        import numpy  # noqa: F401
    except ImportError:
        return INSTALL_HINT
    return None


class PhotoError(ValueError):
    pass


# --------------------------------------------------------------------------- the photo

@dataclass
class Photo:
    gray: object                  # numpy float array (h, w), 0..255, upright
    focal_px: float | None        # from the EXIF 35 mm equivalent, at this size
    size: tuple[int, int]
    preview: Image.Image          # the photo at this size (RGB), for showing what was found


def load_photo(path: str | Path, max_side: int = MAX_SIDE) -> Photo:
    import numpy as np
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    try:
        img = Image.open(path)
    except Exception as e:  # noqa: BLE001 - unreadable or unsupported file
        hint = " (iPhone photos need pillow-heif: see the 'photo' extra)" if str(path).lower().endswith(
            (".heic", ".heif")) else ""
        raise PhotoError(f"can't open {Path(path).name}: {e}{hint}") from None
    focal35 = None
    try:
        exif_ifd = img.getexif().get_ifd(0x8769)
        focal35 = float(exif_ifd.get(0xA405) or 0) or None  # FocalLengthIn35mmFilm
    except Exception:  # noqa: BLE001 - EXIF is optional
        focal35 = None
    img = ImageOps.exif_transpose(img).convert("RGB")
    k = min(1.0, max_side / max(img.size))
    if k < 1.0:
        img = img.resize((round(img.width * k), round(img.height * k)), Image.Resampling.LANCZOS)
    focal = focal35 / 43.27 * math.hypot(*img.size) if focal35 else None  # 43.27 mm: a 35 mm frame's diagonal
    gray = np.asarray(img.convert("L"), dtype=np.float64)
    return Photo(gray, focal, img.size, img)


# --------------------------------------------------------------------------- finding the screens

def _otsu(gray) -> float:
    import numpy as np
    hist, edges = np.histogram(gray, bins=256, range=(0, 256))
    p = hist / max(hist.sum(), 1)
    omega = np.cumsum(p)
    mu = np.cumsum(p * np.arange(256))
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mu[-1] * omega - mu) ** 2 / (omega * (1 - omega))
    return float(np.nanargmax(between))


def _components(mask) -> list:
    """Connected bright regions as lists of row runs (row, x0, x1), via union-find on runs."""
    import numpy as np
    runs, row_runs = [], []
    for y in range(mask.shape[0]):
        row = mask[y]
        d = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
        starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
        idx = []
        for a, b in zip(starts.tolist(), ends.tolist(), strict=True):
            idx.append(len(runs))
            runs.append((y, a, b))
        row_runs.append(idx)
    parent = list(range(len(runs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for y in range(1, len(row_runs)):
        above, here = row_runs[y - 1], row_runs[y]
        i = j = 0
        while i < len(above) and j < len(here):
            _, a0, a1 = runs[above[i]]
            _, b0, b1 = runs[here[j]]
            if a0 < b1 and b0 < a1:  # overlap (4-connected)
                ra, rb = find(above[i]), find(here[j])
                if ra != rb:
                    parent[rb] = ra
            if a1 < b1:
                i += 1
            else:
                j += 1
    groups: dict[int, list] = {}
    for i, r in enumerate(runs):
        groups.setdefault(find(i), []).append(r)
    return list(groups.values())


def _fit_line(points):
    """A line through edge points, ignoring points that stray from it (something in
    front of the screen, a notch): (point on it, unit direction)."""
    import numpy as np
    pts = np.asarray(points, dtype=np.float64)
    keep = np.ones(len(pts), dtype=bool)
    for _ in range(4):
        c = pts[keep].mean(axis=0)
        _, _, vt = np.linalg.svd(pts[keep] - c, full_matrices=False)
        normal = vt[1]
        dist = np.abs((pts - c) @ normal)
        limit = max(1.5, 2.5 * float(np.median(dist[keep])) * 1.4826)
        new = dist <= limit
        if new.sum() < max(5, 0.3 * len(pts)) or (new == keep).all():
            break
        keep = new
    c = pts[keep].mean(axis=0)
    _, _, vt = np.linalg.svd(pts[keep] - c, full_matrices=False)
    return c, vt[0]


def _intersect(l1, l2):
    import numpy as np
    (p, d), (q, e) = l1, l2
    m = np.array([[d[0], -e[0]], [d[1], -e[1]]])
    t = np.linalg.solve(m, q - p)
    return p + t[0] * d


def _mask(runs):
    import numpy as np
    y0 = min(r[0] for r in runs)
    y1 = max(r[0] for r in runs) + 1
    x0 = min(r[1] for r in runs)
    x1 = max(r[2] for r in runs)
    m = np.zeros((y1 - y0, x1 - x0), dtype=bool)
    for y, a, b in runs:
        m[y - y0, a - x0:b - x0] = True
    return m, x0, y0


def _quad(m, x0, y0) -> list[tuple[float, float]]:
    """The four corners (TL, TR, BR, BL) of a bright quadrilateral, from lines fitted to its edges."""
    import numpy as np
    rows = np.flatnonzero(m.any(axis=1))
    cols = np.flatnonzero(m.any(axis=0))
    left = [(np.argmax(m[r]) + x0, r + y0 + 0.5) for r in rows]                       # pixel edges, continuous
    right = [(m.shape[1] - np.argmax(m[r][::-1]) + x0, r + y0 + 0.5) for r in rows]
    top = [(c + x0 + 0.5, np.argmax(m[:, c]) + y0) for c in cols]
    bottom = [(c + x0 + 0.5, m.shape[0] - np.argmax(m[::-1, c]) + y0) for c in cols]
    pts = np.array(left + right + top + bottom, dtype=np.float64)
    s, d = pts.sum(axis=1), pts[:, 0] - pts[:, 1]
    tl, br, tr, bl = pts[s.argmin()], pts[s.argmax()], pts[d.argmax()], pts[d.argmin()]

    def between(edge, a, b, axis):
        lo, hi = sorted((a[axis], b[axis]))
        trim = 0.1 * (hi - lo)
        sel = [p for p in edge if lo + trim <= p[axis] <= hi - trim]
        if len(sel) < 5:
            raise PhotoError("a screen's edges are too short to measure: get closer, or use a sharper photo")
        return sel

    lines = [_fit_line(between(top, tl, tr, 0)), _fit_line(between(right, tr, br, 1)),
             _fit_line(between(bottom, bl, br, 0)), _fit_line(between(left, tl, bl, 1))]
    corners = [_intersect(lines[3], lines[0]), _intersect(lines[0], lines[1]),
               _intersect(lines[1], lines[2]), _intersect(lines[2], lines[3])]
    return [(float(c[0]), float(c[1])) for c in corners]


def _dots(m) -> int:
    """How many dark dots sit in the middle of a white panel: which monitor it is."""
    import numpy as np
    h, w = m.shape
    area = int(m.sum())
    count = 0
    sizes = []
    for comp in _components(~m):
        ys = [r[0] for r in comp]
        xs0 = [r[1] for r in comp]
        xs1 = [r[2] for r in comp]
        if min(ys) == 0 or max(ys) == h - 1 or min(xs0) == 0 or max(xs1) == w:
            continue  # outside the panel, not a hole in it
        size = sum(b - a for _, a, b in comp)
        cy = (min(ys) + max(ys) + 1) / 2
        cx = (min(xs0) + max(xs1)) / 2
        bw, bh = max(xs1) - min(xs0), max(ys) - min(ys) + 1
        if size < 0.0005 * area or not (0.1 * w < cx < 0.9 * w and 0.3 * h < cy < 0.7 * h):
            continue  # specks, or something in front of the panel away from the middle
        if not 0.4 < bw / max(bh, 1) < 2.5:
            continue
        sizes.append(size)
        count += 1
    if sizes and max(sizes) > 4 * np.median(sizes):
        return 0  # not a row of equal dots: something covers the middle
    return count


@dataclass
class Found:
    screens: dict[int, list]        # dot count (1 = first monitor) -> corners (TL, TR, BR, BL)
    cut_off: int = 0                # panels touching the photo's edge, left out


def find_screens(photo: Photo) -> Found:
    """Every white panel fully in the photo, by the number of dots in it."""
    g = photo.gray
    if float(g.max()) - float(g.min()) < 40:
        raise PhotoError("found no screens in the photo: is it a photo of the screens showing the photo pattern?")
    comps = _components(g > _otsu(g))
    min_area = 0.002 * g.size
    h, w = g.shape
    found = Found({})
    for c in sorted(comps, key=lambda c: -sum(b - a for _, a, b in c)):
        if sum(b - a for _, a, b in c) < min_area:
            break
        if min(r[0] for r in c) <= 1 or max(r[0] for r in c) >= h - 2 or \
                min(r[1] for r in c) <= 1 or max(r[2] for r in c) >= w - 1:
            found.cut_off += 1
            continue
        m, x0, y0 = _mask(c)
        n = _dots(m)
        if n == 0:
            raise PhotoError("a screen's dots can't be read: is something in front of the middle of a screen?")
        if n in found.screens:
            raise PhotoError(f"two screens show {n} dot{'s' * (n > 1)}: is the photo pattern from this layout?")
        found.screens[n] = _quad(m, x0, y0)
    if not found.screens:
        raise PhotoError("no screen is fully in the photo" if found.cut_off else
                         "found no screens in the photo: is it a photo of the screens showing the photo pattern?")
    return found


# --------------------------------------------------------------------------- fitting the layout

@dataclass
class Fit:
    monitors: dict[str, dict]       # name -> {key: new value} (only keys that are fitted)
    viewer: dict                    # distance, height, offset (mm)
    rms_px: float
    rms_mm: float                   # roughly, on the screens
    worst: tuple[str, float]        # monitor with the largest corner error (mm)
    flat: bool                      # every angle and tilt came out under a degree
    observed: list = field(default_factory=list)   # per photo: name -> corners found
    predicted: list = field(default_factory=list)  # per photo: name -> corners the fitted layout puts there
    photos: list = field(default_factory=list)     # per photo: the picture (for overlay)


def _rodrigues(w):
    import numpy as np
    th = float(np.linalg.norm(w))
    if th < 1e-12:
        return np.eye(3)
    k = np.asarray(w) / th
    kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(th) * kx + (1 - math.cos(th)) * kx @ kx


def _unrodrigues(r):
    import numpy as np
    th = math.acos(max(-1.0, min(1.0, (np.trace(r) - 1) / 2)))
    if th < 1e-9:
        return np.zeros(3)
    v = np.array([r[2, 1] - r[1, 2], r[0, 2] - r[2, 0], r[1, 0] - r[0, 1]]) / (2 * math.sin(th))
    return v * th


FLIP = ((1, 0, 0), (0, 1, 0), (0, 0, -1))  # layout (z towards you) -> camera (z into the desk)


def _fitted_keys(cfg) -> list[tuple[str, str]]:
    """(monitor, key) pairs the photo can measure, from each monitor's placement."""
    keys = []
    for i, m in enumerate(cfg.monitors):
        if i == 0:
            continue
        if m.position is not None:
            keys += [(m.name, "position_x"), (m.name, "position_y")]
        elif m.attach and m.attach[0] in ("below", "above"):
            keys += [(m.name, "gap"), (m.name, "h_pos")]
        else:
            keys += [(m.name, "gap"), (m.name, "v_pos")]
        keys += [(m.name, "angle"), (m.name, "tilt")]
    return keys


def _panels(lay) -> dict:
    """3D corners of each screen's white panel, relative to the first monitor's centre."""
    import numpy as np

    from .calibrate import photo_inset_px
    a = lay.placed[0].active
    origin = np.array([a.x + a.w / 2, a.y + a.h / 2, 0.0])
    out = {}
    for p in lay:
        tl, tr, br, bl = (np.array(c) - origin for c in p.corners3d)
        pw, ph = p.monitor.pixels
        k = photo_inset_px((pw, ph))
        u0, v0 = k / pw, k / ph
        out[p.monitor.name] = [tl + u * (tr - tl) + v * (bl - tl)
                               for u, v in ((u0, v0), (1 - u0, v0), (1 - u0, 1 - v0), (u0, 1 - v0))]
    return out


def _pose_from(corners3, seen, f, size):
    """Camera rotation and centre from one rectangle's 3D corners and where a photo shows them."""
    import numpy as np

    from .perspective import solve_homography
    p0, p1, _, p3 = corners3
    wx, wy = float(np.linalg.norm(p1 - p0)), float(np.linalg.norm(p3 - p0))
    ex, ey = (p1 - p0) / wx, (p3 - p0) / wy
    e = np.column_stack([ex, ey, np.cross(ex, ey)])
    hm = np.array(solve_homography([(0, 0), (wx, 0), (wx, wy), (0, wy)], [tuple(c) for c in seen])).reshape(3, 3)
    cx, cy = size[0] / 2, size[1] / 2
    b = np.array([[1 / f, 0, -cx / f], [0, 1 / f, -cy / f], [0, 0, 1]]) @ hm
    lam = 1 / np.linalg.norm(b[:, 0])
    if (lam * b[:, 2])[2] < 0:
        lam = -lam
    a1, a2, t = lam * b[:, 0], lam * b[:, 1], lam * b[:, 2]
    u, _, vt = np.linalg.svd(np.column_stack([a1, a2, -np.cross(a1, a2)]) @ e.T)  # M = R . FLIP, det -1
    m = u @ vt
    if np.linalg.det(m) > 0:
        m = u @ np.diag([1, 1, -1]) @ vt
    rot = m @ np.array(FLIP)
    centre = p0 - m.T @ t
    return rot, centre


def fit(raw: dict, photos: list[dict], sizes: list, focals: list | None = None, iterations: int = 60) -> Fit:
    """Adjust ``raw`` (a plain layout dict) so the layout, seen from the photos' cameras, matches them.

    ``photos``: per photo, {monitor name: corners found}. All photos are taken from
    the same seat, each with its own direction and zoom; every monitor must be in
    one of them, and each photo must share a monitor with one fitted before it."""
    import numpy as np

    from .config import configuration_from_dict
    from .layout import compute_layout

    focals = focals or [None] * len(photos)
    base = copy.deepcopy(raw)
    base["viewer"] = dict(base.get("viewer") or {}) or {"distance": 600}
    cfg0 = configuration_from_dict(base)
    names = [m.name for m in cfg0.monitors]
    seen = set().union(*(set(p) for p in photos)) if photos else set()
    missing_names = [n for n in names if n not in seen]
    if missing_names:
        raise PhotoError(f"{', '.join(missing_names)} {'is' if len(missing_names) == 1 else 'are'} in none of the "
                         "photos: every screen must be fully in at least one")
    keys = _fitted_keys(cfg0)
    by_name = {m.name: m for m in cfg0.monitors}

    def current(name, key):
        m = by_name[name]
        if key in ("position_x", "position_y"):
            return m.position[0 if key.endswith("x") else 1]
        return float(getattr(m, key))

    def build(vals):
        doc = copy.deepcopy(base)
        es = {str(e.get("name") or f"monitor{i + 1}"): e for i, e in enumerate(doc["monitors"])}
        for (name, key), v in zip(keys, vals, strict=True):
            e = es[name]
            if key.startswith("position_"):
                pos = list(e.get("position") or [0, 0])
                pos[0 if key.endswith("x") else 1] = float(v)
                e["position"] = pos
            else:
                e[key] = float(v)
        return compute_layout(configuration_from_dict(doc))

    nk = len(keys)
    vals = np.array([current(n, k) for n, k in keys], dtype=np.float64)
    order: list[int] = []

    def residuals(pv, used):
        lay = build(pv[:nk])
        panels = _panels(lay)
        res, pred = [], []
        for slot, i in enumerate(used):
            c = pv[nk + 7 * slot: nk + 7 * slot + 7]
            m = _rodrigues(c[:3]) @ np.array(FLIP)
            centre, f = c[3:6], c[6]
            cx, cy = sizes[i][0] / 2, sizes[i][1] / 2
            shown = {}
            for name, found in photos[i].items():
                q = []
                for corner3, (ox, oy) in zip(panels[name], found, strict=True):
                    v = m @ (corner3 - centre)
                    if v[2] <= 1e-6:
                        raise PhotoError("behind the camera")
                    x, y = cx + f * v[0] / v[2], cy + f * v[1] / v[2]
                    q.append((x, y))
                    res += [x - ox, y - oy]
                shown[name] = q
            pred.append(shown)
            if focals[i]:
                res.append((f - focals[i]) / (0.05 * focals[i]))  # trust the EXIF zoom to ~5 %
        return np.array(res), pred

    def solve(pv, used):
        steps = np.array([0.05 if k[1] not in ("angle", "tilt") else 0.02 for k in keys]
                         + [1e-5, 1e-5, 1e-5, 0.5, 0.5, 0.5, 0.5] * len(used))
        damping = 1e-3
        r, pred = residuals(pv, used)
        cost = float(r @ r)
        for _ in range(iterations):
            jac = np.empty((r.size, pv.size))
            for j in range(pv.size):
                d = np.zeros_like(pv)
                d[j] = steps[j]
                try:
                    jac[:, j] = (residuals(pv + d, used)[0] - r) / steps[j]
                except (PhotoError, ValueError):
                    jac[:, j] = 0.0
            jtj, jtr = jac.T @ jac, jac.T @ r
            improved, delta = False, np.zeros_like(pv)
            for _ in range(10):
                delta = np.linalg.solve(jtj + damping * np.diag(np.diag(jtj) + 1e-9), -jtr)
                try:
                    r2, pred2 = residuals(pv + delta, used)
                except (PhotoError, ValueError):
                    damping *= 10
                    continue
                c2 = float(r2 @ r2)
                if c2 < cost:
                    pv, r, pred, cost = pv + delta, r2, pred2, c2
                    damping = max(damping / 3, 1e-9)
                    improved = True
                    break
                damping *= 10
            if not improved or float(np.abs(delta).max()) < 1e-6:
                break
        return pv, r, pred

    # photos one at a time, each starting from a screen already placed: the first monitor first
    known = {names[0]}
    todo = list(range(len(photos)))
    pv = vals
    while todo:
        pick = next((i for i in todo if names[0] in photos[i]), None)
        if pick is None:
            pick = next((i for i in todo if known & set(photos[i])), None)
        if pick is None:
            raise PhotoError("a photo shares no screen with the others: each photo must include a screen "
                             "from another photo (or the first monitor)")
        ref = names[0] if names[0] in photos[pick] else next(n for n in names if n in known and n in photos[pick])
        f0 = focals[pick] or 0.75 * max(sizes[pick])
        rot, centre = _pose_from(_panels(build(pv[:nk]))[ref], photos[pick][ref], f0, sizes[pick])
        order.append(pick)
        todo.remove(pick)
        pv = np.concatenate([pv, _unrodrigues(rot), centre, [f0]])
        pv, r, pred = solve(pv, order)
        known |= set(photos[pick])

    errs, worst_by = [], {}
    px_mm = []
    for slot, i in enumerate(order):
        c = pv[nk + 7 * slot: nk + 7 * slot + 7]
        k = c[6] / max(float(np.linalg.norm(c[3:6])), 1.0)  # px per mm at the screens' distance
        px_mm.append(k)
        for name, found in photos[i].items():
            e = np.hypot(*(np.array(pred[slot][name]) - np.array(found)).T)
            errs += list(e / k)
            worst_by[name] = max(worst_by.get(name, 0.0), float(e.max() / k))
    rms_mm = float(np.sqrt(np.mean(np.square(errs))))
    out: dict[str, dict] = {}
    for (name, key), v in zip(keys, pv[:nk], strict=True):
        out.setdefault(name, {})[key] = round(float(v), 1)
    flat = all(abs(v) < 1.0 for d in out.values() for k, v in d.items() if k in ("angle", "tilt"))
    centre = np.mean([pv[nk + 7 * s + 3: nk + 7 * s + 6] for s in range(len(order))], axis=0)
    viewer = {"distance": round(float(centre[2]), 0), "height": round(float(-centre[1]), 0),
              "offset": round(float(centre[0]), 0)}
    predicted = [None] * len(photos)
    for slot, i in enumerate(order):
        predicted[i] = pred[slot]
    return Fit(out, viewer, rms_mm * float(np.mean(px_mm)), rms_mm, max(worst_by.items(), key=lambda kv: kv[1]),
               flat, observed=[{k: list(v) for k, v in p.items()} for p in photos], predicted=predicted)


def analyse(paths, raw: dict) -> Fit:
    """Photo file(s) + plain layout dict -> the fitted numbers."""
    from .config import configuration_from_dict
    if missing():
        raise PhotoError(INSTALL_HINT)
    if isinstance(paths, (str, Path)):
        paths = [paths]
    names = [m.name for m in configuration_from_dict(raw).monitors]
    loaded, observations = [], []
    for path in paths:
        photo = load_photo(path)
        try:
            found = find_screens(photo)
        except PhotoError as e:
            raise PhotoError(f"{Path(path).name}: {e}") from None
        extra = [n for n in found.screens if n > len(names)]
        if extra:
            raise PhotoError(f"{Path(path).name}: a screen shows {extra[0]} dots but the layout has "
                             f"{len(names)} monitors: is the photo pattern from this layout?")
        loaded.append(photo)
        observations.append({names[n - 1]: q for n, q in found.screens.items()})
    result = fit(raw, observations, [p.size for p in loaded], [p.focal_px for p in loaded])
    result.photos = [p.preview for p in loaded]
    return result


def overlay(fit_result: Fit, index: int = 0) -> Image.Image:
    """A photo with what was found (green) and where the fitted layout puts it (pink)."""
    from PIL import ImageDraw, ImageFont
    out = fit_result.photos[index].convert("RGB").copy()
    d = ImageDraw.Draw(out)
    w = max(2, round(max(out.size) / 600))
    try:
        font = ImageFont.load_default(size=max(14, max(out.size) // 50))
    except TypeError:
        font = ImageFont.load_default()
    for name, q in fit_result.observed[index].items():
        d.polygon([tuple(p) for p in q], outline=(40, 220, 90), width=w)
        cx = sum(p[0] for p in q) / 4
        cy = sum(p[1] for p in q) / 4
        d.text((cx, cy + max(out.size) / 30), name, fill=(40, 220, 90), font=font, anchor="mm",
               stroke_width=2, stroke_fill=(0, 0, 0))
    for q in (fit_result.predicted[index] or {}).values():
        for x, y in q:
            d.ellipse((x - 3 * w, y - 3 * w, x + 3 * w, y + 3 * w), outline=(255, 60, 200), width=w)
    return out


# --------------------------------------------------------------------------- using the result

def changes(result: Fit, raw: dict) -> list[tuple[str | None, str, object, object]]:
    """What the fit would change: [(monitor name or None for the viewer, key, old, new)].

    A desk that came out flat (no angle or tilt over a degree) only gets its gaps
    and offsets, unless the layout already has a viewer: angled screens stay opt-in.
    """
    from .config import configuration_from_dict
    cfg = configuration_from_dict(raw)
    angled = cfg.viewer is not None or not result.flat
    out: list[tuple[str | None, str, object, object]] = []
    by_name = {m.name: m for m in cfg.monitors}
    for name, values in result.monitors.items():
        m = by_name[name]
        if "position_x" in values:
            old = list(m.position or (0, 0))
            new = [values["position_x"], values["position_y"]]
            if [round(v, 1) for v in old] != new:
                out.append((name, "position", old, new))
        for key in ("gap", "v_pos", "h_pos", "angle", "tilt"):
            if key not in values or (key in ("angle", "tilt") and not angled):
                continue
            new = values[key]
            if key in ("angle", "tilt") and abs(new) < 0.3:
                new = 0.0
            old = float(getattr(m, key))
            if round(old, 1) != round(new, 1):
                out.append((name, key, round(old, 1), new))
    if angled:
        old_v = cfg.viewer
        for key in ("distance", "height", "offset"):
            old = getattr(old_v, key) if old_v else None
            new = result.viewer[key]
            if old is None or round(old) != round(new):
                out.append((None, key, old, new))
    return out


def apply_changes(doc, found: list, put=None) -> None:
    """Write ``changes(...)`` into a round-trip layout document (comments are kept)."""
    from ruamel.yaml.comments import CommentedMap

    def tidy(v):
        v = round(float(v), 1)
        return int(v) if v == int(v) else v

    def setkey(entry, key, value):
        if put is not None:
            put(entry, key, value)
        else:
            entry[key] = value

    entries = {str(e.get("name") or f"monitor{i + 1}"): e for i, e in enumerate(doc["monitors"])}
    for name, key, _old, new in found:
        if name is None:
            if not isinstance(doc.get("viewer"), dict):
                keys = list(doc.keys())
                doc.insert(keys.index("monitors") if "monitors" in keys else len(keys), "viewer", CommentedMap())
            doc["viewer"][key] = tidy(new)
            continue
        e = entries[name]
        if key == "position":
            setkey(e, key, [tidy(v) for v in new])
        elif key in ("angle", "tilt") and not new:
            e.pop(key, None)
        else:
            setkey(e, key, tidy(new))
