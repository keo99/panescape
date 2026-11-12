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
from itertools import permutations
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
    """Total least squares line through points: (point on it, unit direction)."""
    import numpy as np
    pts = np.asarray(points, dtype=np.float64)
    c = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - c, full_matrices=False)
    return c, vt[0]


def _intersect(l1, l2):
    import numpy as np
    (p, d), (q, e) = l1, l2
    m = np.array([[d[0], -e[0]], [d[1], -e[1]]])
    t = np.linalg.solve(m, q - p)
    return p + t[0] * d


def _quad(runs) -> list[tuple[float, float]]:
    """The four corners (TL, TR, BR, BL) of a bright quadrilateral, from lines fitted to its edges."""
    import numpy as np
    y0 = min(r[0] for r in runs)
    y1 = max(r[0] for r in runs) + 1
    x0 = min(r[1] for r in runs)
    x1 = max(r[2] for r in runs)
    m = np.zeros((y1 - y0, x1 - x0), dtype=bool)
    for y, a, b in runs:
        m[y - y0, a - x0:b - x0] = True
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


def find_screens(photo: Photo, count: int) -> list[list[tuple[float, float]]]:
    """The ``count`` largest bright quadrilaterals, as corner lists (TL, TR, BR, BL)."""
    import numpy as np
    g = photo.gray
    if float(g.max()) - float(g.min()) < 40:
        raise PhotoError(f"found no screens in the photo, expected {count}: "
                         "is it a photo of the screens showing the photo pattern?")
    mask = g > _otsu(g)
    comps = _components(mask)
    min_area = 0.002 * g.size
    big = sorted((c for c in comps if sum(b - a for _, a, b in c) >= min_area),
                 key=lambda c: -sum(b - a for _, a, b in c))
    if len(big) < count:
        raise PhotoError(f"found {len(big)} screen{'s' * (len(big) != 1)} in the photo, expected {count}: "
                         "is every screen in the frame, showing the photo pattern?")
    h, w = g.shape
    for c in big[:count]:
        if min(r[0] for r in c) <= 1 or max(r[0] for r in c) >= h - 2 or \
                min(r[1] for r in c) <= 1 or max(r[2] for r in c) >= w - 1:
            raise PhotoError("a screen is cut off at the edge of the photo: step back or turn the phone "
                             "so every screen is fully in the frame")
    quads = [_quad(c) for c in big[:count]]
    for q in quads:
        if np.linalg.norm(np.subtract(q[0], q[2])) < 20:
            raise PhotoError("a screen is too small in the photo to measure")
    return quads


def pair_with_monitors(quads, layout) -> dict[str, list]:
    """{monitor name: corners}, pairing by arrangement (as the desktop's screens are paired)."""
    from .backends.base import _normalise
    names = [p.monitor.name for p in layout]
    mm = _normalise([(p.active.x + p.active.w / 2, p.active.y + p.active.h / 2) for p in layout])
    px = _normalise([(sum(x for x, _ in q) / 4, sum(y for _, y in q) / 4) for q in quads])

    def cost(perm):
        return sum((mm[i][0] - px[j][0]) ** 2 + (mm[i][1] - px[j][1]) ** 2 for i, j in enumerate(perm))

    best = min(permutations(range(len(quads)), len(names)), key=cost)
    return {names[i]: quads[j] for i, j in enumerate(best)}


# --------------------------------------------------------------------------- fitting the layout

@dataclass
class Fit:
    monitors: dict[str, dict]       # name -> {key: new value} (only keys that are fitted)
    viewer: dict                    # distance, height, offset (mm)
    rms_px: float
    rms_mm: float                   # roughly, on the screens
    worst: tuple[str, float]        # monitor with the largest corner error (mm)
    flat: bool                      # every angle and tilt came out under a degree
    observed: dict = field(default_factory=dict)   # name -> corners found in the photo
    predicted: dict = field(default_factory=dict)  # name -> corners the fitted layout puts there


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


def fit(raw: dict, corners: dict[str, list], photo_size: tuple[int, int], focal_px: float | None = None,
        iterations: int = 60) -> Fit:
    """Adjust ``raw`` (a plain layout dict) so the layout, seen from the fitted eye, matches ``corners``."""
    import numpy as np

    from .calibrate import photo_inset_px
    from .config import configuration_from_dict
    from .layout import compute_layout

    base = copy.deepcopy(raw)
    base["viewer"] = dict(base.get("viewer") or {}) or {"distance": 600}
    cfg0 = configuration_from_dict(base)
    keys = _fitted_keys(cfg0)
    by_name = {m.name: m for m in cfg0.monitors}

    def current(name, key):
        m = by_name[name]
        if key == "position_x":
            return m.position[0]
        if key == "position_y":
            return m.position[1]
        return float(getattr(m, key))

    w, h = photo_size
    cx, cy = w / 2, h / 2
    f0 = focal_px or 0.75 * max(w, h)

    def build(vals, viewer):
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
        doc["viewer"] = {"distance": float(viewer[0]), "height": float(viewer[1]), "offset": float(viewer[2])}
        return doc

    def targets(lay):
        """3D corners of each screen's white panel, and the eye, in layout coordinates."""
        out = {}
        for p in lay:
            tl, tr, br, bl = (np.array(c) for c in p.corners3d)
            pw, ph = p.monitor.pixels
            k = photo_inset_px((pw, ph))
            u0, v0 = k / pw, k / ph
            out[p.monitor.name] = [tl + u * (tr - tl) + v * (bl - tl)
                                   for u, v in ((u0, v0), (1 - u0, v0), (1 - u0, 1 - v0), (u0, 1 - v0))]
        return out

    def project(lay, rot, f):
        eye = np.array(lay.eye)
        m = rot @ np.array(FLIP)
        pts = {}
        for name, corners3 in targets(lay).items():
            q = []
            for c in corners3:
                v = m @ (c - eye)
                if v[2] <= 1e-6:
                    raise PhotoError("behind the camera")
                q.append((cx + f * v[0] / v[2], cy + f * v[1] / v[2]))
            pts[name] = q
        return pts

    # --- start: the eye from the first monitor's shape in the photo (a plane's pose from a homography)
    lay0 = compute_layout(cfg0)
    anchor = lay0.placed[0]
    a = anchor.active
    from .perspective import solve_homography
    t0 = targets(lay0)[anchor.monitor.name]
    hmat = np.array(solve_homography([(float(c[0]), float(c[1])) for c in t0], corners[anchor.monitor.name]))
    hmat = hmat.reshape(3, 3)
    kinv = np.array([[1 / f0, 0, -cx / f0], [0, 1 / f0, -cy / f0], [0, 0, 1]])
    b = kinv @ hmat
    lam = 1 / np.linalg.norm(b[:, 0])
    if (lam * b[:, 2])[2] < 0:
        lam = -lam
    m1, m2, t = lam * b[:, 0], lam * b[:, 1], lam * b[:, 2]
    m3 = -np.cross(m1, m2)  # M = R . FLIP has determinant -1
    u, _, vt = np.linalg.svd(np.column_stack([m1, m2, m3]) @ np.array(FLIP))
    rot = u @ vt
    mm = rot @ np.array(FLIP)
    eye = -mm.T @ t  # camera centre in the anchor plane's coordinates (z = distance)
    acx, acy = a.x + a.w / 2, a.y + a.h / 2
    viewer0 = (max(150.0, float(eye[2])), acy - float(eye[1]), float(eye[0]) - acx)

    vals = [current(n, k) for n, k in keys]
    params = np.array(vals + list(viewer0) + list(_unrodrigues(rot)) + [f0], dtype=np.float64)
    nk = len(keys)
    obs = np.array([c for name in (p.monitor.name for p in lay0) for c in corners[name]]).ravel()

    def residuals(pv):
        doc = build(pv[:nk], pv[nk:nk + 3])
        lay = compute_layout(configuration_from_dict(doc))
        pts = project(lay, _rodrigues(pv[nk + 3:nk + 6]), pv[nk + 6])
        pred = np.array([c for p in lay for c in pts[p.monitor.name]]).ravel()
        prior = []
        if focal_px:
            prior.append((pv[nk + 6] - focal_px) / (0.05 * focal_px))  # trust the EXIF zoom to ~5 %
        return np.concatenate([pred - obs, np.array(prior)]), lay, pts

    steps = np.array([0.05 if k[1] not in ("angle", "tilt") else 0.02 for k in keys]
                     + [0.5, 0.5, 0.5] + [1e-5] * 3 + [0.5])
    damping = 1e-3
    r, lay, pts = residuals(params)
    cost = float(r @ r)
    for _ in range(iterations):
        jac = np.empty((r.size, params.size))
        for j in range(params.size):
            d = np.zeros_like(params)
            d[j] = steps[j]
            try:
                jac[:, j] = (residuals(params + d)[0] - r) / steps[j]
            except (PhotoError, ValueError):
                jac[:, j] = 0.0
        jtj, jtr = jac.T @ jac, jac.T @ r
        improved = False
        for _ in range(10):
            delta = np.linalg.solve(jtj + damping * np.diag(np.diag(jtj) + 1e-9), -jtr)
            try:
                r2, lay2, pts2 = residuals(params + delta)
            except (PhotoError, ValueError):
                damping *= 10
                continue
            c2 = float(r2 @ r2)
            if c2 < cost:
                params, r, lay, pts, cost = params + delta, r2, lay2, pts2, c2
                damping = max(damping / 3, 1e-9)
                improved = True
                break
            damping *= 10
        if not improved or float(np.abs(delta).max()) < 1e-6:
            break

    n_obs = obs.size
    err = np.hypot(*(r[:n_obs].reshape(-1, 2).T))
    rms_px = float(np.sqrt((err ** 2).mean()))
    px_per_mm = params[nk + 6] / max(params[nk], 1.0)  # at the first screen's distance
    per_monitor = {}
    for i, p in enumerate(lay):
        per_monitor[p.monitor.name] = float(err[4 * i:4 * i + 4].max() / px_per_mm)
    worst = max(per_monitor.items(), key=lambda kv: kv[1])
    out: dict[str, dict] = {}
    for (name, key), v in zip(keys, params[:nk], strict=True):
        out.setdefault(name, {})[key] = round(float(v), 1)
    flat = all(abs(v) < 1.0 for d in out.values() for k, v in d.items() if k in ("angle", "tilt"))
    viewer = {"distance": round(float(params[nk]), 0), "height": round(float(params[nk + 1]), 0),
              "offset": round(float(params[nk + 2]), 0)}
    return Fit(out, viewer, rms_px, rms_px / px_per_mm, worst, flat,
               observed={k: list(v) for k, v in corners.items()}, predicted=pts)


def analyse(path: str | Path, raw: dict) -> Fit:
    """Photo file + plain layout dict -> the fitted numbers."""
    from .config import configuration_from_dict
    from .layout import compute_layout
    if missing():
        raise PhotoError(INSTALL_HINT)
    photo = load_photo(path)
    seated = copy.deepcopy(raw)
    seated["viewer"] = dict(seated.get("viewer") or {}) or {"distance": 600}
    lay = compute_layout(configuration_from_dict(seated))
    quads = find_screens(photo, len(lay.placed))
    corners = pair_with_monitors(quads, lay)
    result = fit(raw, corners, photo.size, photo.focal_px)
    result.photo = photo.preview  # type: ignore[attr-defined]
    return result


def overlay(fit_result: Fit, image: Image.Image) -> Image.Image:
    """The photo with what was found (green) and where the fitted layout puts it (magenta)."""
    from PIL import ImageDraw, ImageFont
    out = image.convert("RGB").copy()
    d = ImageDraw.Draw(out)
    w = max(2, round(max(out.size) / 600))
    try:
        font = ImageFont.load_default(size=max(14, max(out.size) // 50))
    except TypeError:
        font = ImageFont.load_default()
    for name, q in fit_result.observed.items():
        d.polygon([tuple(p) for p in q], outline=(40, 220, 90), width=w)
        cx = sum(p[0] for p in q) / 4
        cy = sum(p[1] for p in q) / 4
        d.text((cx, cy), name, fill=(40, 220, 90), font=font, anchor="mm", stroke_width=2, stroke_fill=(0, 0, 0))
    for q in fit_result.predicted.values():
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
