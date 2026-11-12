"""Calibration pattern: lines drawn in physical millimetres across the whole desk.

If the configuration matches reality, every line continues straight across
the bezels. Rulers along each screen edge read desk coordinates in mm, so a
misalignment can be read off and corrected:

* horizontal (cyan) lines step at a side-by-side boundary  -> fix ``v_pos``
* vertical (yellow) lines step at a stacked boundary        -> fix ``h_pos``
* 45 degree lines step once the above line up               -> fix ``gap``/``bezel``
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFont

from .layout import Layout, Placed, Rect

BG = (18, 18, 22)
H_LINE = (0, 200, 230)       # horizontal lines (cyan)
V_LINE = (240, 200, 0)       # vertical lines (yellow)
D_UP = (255, 110, 40)        # rising diagonals (orange)
D_DOWN = (90, 210, 90)       # falling diagonals (green)
CIRCLE = (200, 90, 220)
RULER = (235, 235, 235)
TEXT = (255, 255, 255)

GRID_MM = 50.0
DIAG_MM = 100.0

GUIDE = """\
How to read the pattern (lengths in mm; v_pos + is up, h_pos + is right):
  1. Spacing: each screen shows the configured distance between its lit area
     and its neighbours' ("lit gap"). Measure those with a ruler; fix 'gap'
     (or 'bezel') until they match.
  2. Cyan horizontal lines must continue across side-by-side screens.
     Read both edge rulers at the same physical height on either side of the
     bezel. On the screen that is placed relative to the other:
         v_pos += its reading - the other screen's reading
  3. Yellow vertical lines must continue across stacked screens:
         h_pos += the other screen's reading - its reading
  4. Orange/green 45 degree lines and the magenta circle should now run
     straight across every boundary; a jump means step 1 is still off.
Re-run after editing, or use --watch to re-apply on every save."""


def _font(px: int):
    try:
        return ImageFont.load_default(size=max(10, px))
    except TypeError:
        return ImageFont.load_default()


def draw(layout: Layout, rect: Rect, size: tuple[int, int], placed: Placed | None = None) -> Image.Image:
    """Render the part of the desk inside ``rect`` (mm) into an image of ``size`` px."""
    W, H = size
    sx, sy = W / rect.w, H / rect.h
    s = (sx + sy) / 2  # px per mm (non-square pixels are rare and tiny)
    img = Image.new("RGB", size, BG)
    d = ImageDraw.Draw(img)

    def X(x: float) -> float:
        return (x - rect.x) * sx

    def Y(y: float) -> float:
        return (y - rect.y) * sy

    thin = max(1, round(0.45 * s))
    canvas = layout.canvas
    x_lo, x_hi = canvas.x - canvas.h, canvas.x1 + canvas.h
    # horizontal & vertical lines on a desk-wide grid
    k = math.floor(rect.y / GRID_MM)
    while k * GRID_MM <= rect.y1:
        y = k * GRID_MM
        d.line([(0, Y(y)), (W, Y(y))], fill=H_LINE, width=thin * (2 if k % 2 == 0 else 1))
        k += 1
    k = math.floor(rect.x / GRID_MM)
    while k * GRID_MM <= rect.x1:
        x = k * GRID_MM
        d.line([(X(x), 0), (X(x), H)], fill=V_LINE, width=thin * (2 if k % 2 == 0 else 1))
        k += 1
    # diagonals: x + y = c (rising to the right) and x - y = c (falling)
    span = canvas.w + canvas.h
    c = -span
    while c <= 2 * span:
        if rect.x + rect.y - 1 <= c <= rect.x1 + rect.y1 + 1:
            d.line([(X(x_lo), Y(c - x_lo)), (X(x_hi), Y(c - x_hi))], fill=D_UP, width=thin)
        if rect.x - rect.y1 - 1 <= c <= rect.x1 - rect.y + 1:
            d.line([(X(x_lo), Y(x_lo - c)), (X(x_hi), Y(x_hi - c))], fill=D_DOWN, width=thin)
        c += DIAG_MM
    # a circle centred on the desk
    cx, cy = canvas.x + canvas.w / 2, canvas.y + canvas.h / 2
    r = 0.45 * min(canvas.w, canvas.h)
    d.ellipse([X(cx - r), Y(cy - r), X(cx + r), Y(cy + r)], outline=CIRCLE, width=thin * 2)

    if placed is not None:
        _rulers(d, rect, sx, sy, s)
        _label(d, layout, placed, W, H, s)
    return img


PHOTO_INSET = 0.05  # the photo pattern's white panel is inset by this share of the screen's shorter side


def photo_inset_px(size: tuple[int, int]) -> int:
    return max(2, round(PHOTO_INSET * min(size)))


def photo_pattern(size: tuple[int, int]) -> Image.Image:
    """For calibrating from a photo: a white panel on black, inset from the screen's
    edges by a known amount, so each screen is an unmistakable bright quadrilateral."""
    img = Image.new("RGB", size, (0, 0, 0))
    k = photo_inset_px(size)
    ImageDraw.Draw(img).rectangle((k, k, size[0] - 1 - k, size[1] - 1 - k), fill=(255, 255, 255))
    return img


def draw_placed(layout: Layout, placed: Placed, size: tuple[int, int]) -> Image.Image:
    """The pattern for one screen. An angled screen (a layout with a viewer) gets the
    pattern as seen through it from the seat, so the lines run straight from there;
    its rulers are left off (they only make sense on a flat desk)."""
    if placed.warp is None:
        return draw(layout, placed.active, size, placed)
    from . import perspective
    W, H = size
    aw, ah = placed.active.w, placed.active.h
    out_to_canvas = perspective.compose(placed.warp, (aw / W, 0, 0, 0, ah / H, 0, 0, 0, 1))
    stretch = max(perspective.scale_at(out_to_canvas, x, y) for x, y in ((0, 0), (W, 0), (0, H), (W, H)))
    density = min(1 / max(stretch, 1e-9), 12.0)  # pattern px per desk mm: about the screen's own
    xs = [x for x, _ in placed.quad]
    ys = [y for _, y in placed.quad]
    box = Rect(min(xs) - 2, min(ys) - 2, max(xs) - min(xs) + 4, max(ys) - min(ys) + 4)
    pw, ph = max(1, round(box.w * density)), max(1, round(box.h * density))
    pattern = draw(layout, box, (pw, ph))
    canvas_to_px = (pw / box.w, 0, -box.x * pw / box.w, 0, ph / box.h, -box.y * ph / box.h, 0, 0, 1)
    m = perspective.compose(canvas_to_px, out_to_canvas)
    img = pattern.transform(size, Image.Transform.PERSPECTIVE, m[:8], Image.Resampling.BICUBIC, fillcolor=BG)
    s = (W / aw + H / ah) / 2
    _label(ImageDraw.Draw(img), layout, placed, W, H, s)
    return img


def _rulers(d: ImageDraw.ImageDraw, rect: Rect, sx: float, sy: float, s: float) -> None:
    """Edge rulers in desk millimetres (1 mm ticks where the screen is dense enough)."""
    font = _font(round(3.2 * s))
    w = max(1, round(0.3 * s))
    W, H = rect.w * sx, rect.h * sy
    step = 1 if s >= 4 else 5                 # px per mm high enough for 1 mm ticks?
    label_every = 10 if 10 * s >= 45 else 50  # keep labels from colliding

    def tick_len(mm: int) -> float:
        return (9 if mm % 50 == 0 else 5 if mm % 10 == 0 else 3 if mm % 5 == 0 else 1.6) * s

    for mm in range(math.ceil(rect.y), math.floor(rect.y1) + 1):
        if mm % step:
            continue
        y = (mm - rect.y) * sy
        n = tick_len(mm)
        d.line([(0, y), (n, y)], fill=RULER, width=w)
        d.line([(W - n, y), (W, y)], fill=RULER, width=w)
        if mm % label_every == 0:
            d.text((n + 2, y), str(mm), fill=RULER, font=font, anchor="lm")
            d.text((W - n - 2, y), str(mm), fill=RULER, font=font, anchor="rm")
    for mm in range(math.ceil(rect.x), math.floor(rect.x1) + 1):
        if mm % step:
            continue
        x = (mm - rect.x) * sx
        n = tick_len(mm)
        d.line([(x, 0), (x, n)], fill=RULER, width=w)
        d.line([(x, H - n), (x, H)], fill=RULER, width=w)
        if mm % label_every == 0:
            d.text((x, n + 2), str(mm), fill=RULER, font=font, anchor="mt")
            d.text((x, H - n - 2), str(mm), fill=RULER, font=font, anchor="mb")


def lit_gaps(layout: Layout, placed: Placed) -> dict[str, tuple[str, float]]:
    """Nearest neighbour's lit area to the left / above: {side: (name, mm)}."""
    a = placed.active
    out: dict[str, tuple[str, float]] = {}
    for p in layout:
        if p is placed:
            continue
        b = p.active
        v_overlap = min(a.y1, b.y1) - max(a.y, b.y) > 0
        h_overlap = min(a.x1, b.x1) - max(a.x, b.x) > 0
        if v_overlap and b.x1 <= a.x + 1e-6:
            dist = a.x - b.x1
            if "left" not in out or dist < out["left"][1]:
                out["left"] = (p.monitor.name, dist)
        if h_overlap and b.y1 <= a.y + 1e-6:
            dist = a.y - b.y1
            if "above" not in out or dist < out["above"][1]:
                out["above"] = (p.monitor.name, dist)
    return out


def _label(d: ImageDraw.ImageDraw, layout: Layout, placed: Placed, W: int, H: int, s: float) -> None:
    m = placed.monitor
    a = placed.active
    lines = [m.name,
             f"{m.pixels[0]}x{m.pixels[1]}  {a.w:.1f} x {a.h:.1f} mm",
             f"bezel L{m.bezel['left']:g} R{m.bezel['right']:g} T{m.bezel['top']:g} B{m.bezel['bottom']:g}"]
    if m.attach:
        lines.append(f"{m.attach[0]} {m.attach[1]}  gap {m.gap:g}")
    elif m.index:
        lines.append(f"gap {m.gap:g}")
    if m.index:
        lines.append(f"v_pos {m.v_pos:g}  h_pos {m.h_pos:g}")
    for side, (name, dist) in lit_gaps(layout, placed).items():
        lines.append(f"lit gap to {name}: {dist:.1f} mm" + ("" if side in name else f" ({side})"))
    font = _font(round(6 * s))
    small = _font(round(4 * s))
    y = H / 2 - 18 * s
    for i, text in enumerate(lines):
        f = font if i == 0 else small
        d.text((W / 2, y), text, fill=TEXT, font=f, anchor="mm", stroke_width=max(1, round(0.6 * s)),
               stroke_fill=BG)
        y += (9 if i == 0 else 6.5) * s


def preview(layout: Layout, px_per_mm: float = 2.0) -> Image.Image:
    """Whole-desk pattern (used for the to-scale preview)."""
    c = layout.canvas
    return draw(layout, c, (max(1, round(c.w * px_per_mm)), max(1, round(c.h * px_per_mm))))
