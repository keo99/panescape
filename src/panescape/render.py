"""Cropping the source image to the physical layout and cutting per-monitor slices."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .layout import Layout, Rect

Image.MAX_IMAGE_PIXELS = 500_000_000  # allow big panoramas, still guard against bombs

ANCHORS = {
    "center": (0.5, 0.5), "centre": (0.5, 0.5), "middle": (0.5, 0.5),
    "top": (0.5, 0.0), "bottom": (0.5, 1.0), "left": (0.0, 0.5), "right": (1.0, 0.5),
    "top-left": (0.0, 0.0), "top-right": (1.0, 0.0),
    "bottom-left": (0.0, 1.0), "bottom-right": (1.0, 1.0),
}
UPSCALE_WARN = 1.25


@dataclass
class CropSpec:
    anchor: tuple[float, float] = (0.5, 0.5)
    zoom: float = 1.0
    fit: str = "cover"            # cover: fill everything (crop); contain: show whole image (bars)
    background: str = "#000000"

    @classmethod
    def from_dicts(cls, *dicts: dict | None) -> CropSpec:
        merged: dict = {}
        for d in dicts:
            if d:
                merged.update({k: v for k, v in d.items() if v is not None})
        anchor = merged.get("anchor", "center")
        if isinstance(anchor, str):
            key = anchor.strip().lower().replace(" ", "-").replace("_", "-")
            if key not in ANCHORS:
                raise ValueError(f"unknown anchor {anchor!r}; use {', '.join(ANCHORS)} or [x, y] in 0..1")
            anchor = ANCHORS[key]
        else:
            anchor = (float(anchor[0]), float(anchor[1]))
        fit = str(merged.get("fit", "cover")).lower()
        if fit not in ("cover", "contain"):
            raise ValueError("crop fit must be 'cover' or 'contain'")
        return cls(anchor=anchor, zoom=float(merged.get("zoom", 1.0)), fit=fit,
                   background=str(merged.get("background", "#000000")))

    def as_dict(self) -> dict:
        return {"anchor": list(self.anchor), "zoom": self.zoom, "fit": self.fit, "background": self.background}


def load_image(path: str | Path) -> Image.Image:
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    if img.mode in ("I;16", "I;16B", "I;16L", "I"):
        img = img.point(lambda v: v * (1 / 256)).convert("L")
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (0, 0, 0))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return img.convert("RGB")


class Mapping:
    """Maps physical mm (layout canvas coordinates) to source image pixels."""

    def __init__(self, image: Image.Image, canvas: Rect, crop: CropSpec):
        wi, hi = image.size
        # source px per mm. cover: canvas fits inside the image (edges cropped);
        # contain: image fits inside the canvas (bars).
        cover = min(wi / canvas.w, hi / canvas.h)
        contain = max(wi / canvas.w, hi / canvas.h)
        self.scale = (cover if crop.fit == "cover" else contain) * crop.zoom  # source px per mm
        need_w, need_h = canvas.w * self.scale, canvas.h * self.scale
        ax, ay = crop.anchor
        ox = (wi - need_w) * ax
        oy = (hi - need_h) * ay
        eps = 1e-6 * max(wi, hi)  # float noise when the canvas exactly fits one axis
        if abs(wi - need_w) < eps:
            ox, need_w = 0.0, float(wi)
        if abs(hi - need_h) < eps:
            oy, need_h = 0.0, float(hi)
        # pad the source if the requested area runs off the image (contain / zoom < 1)
        pad_l = max(0.0, -ox)
        pad_t = max(0.0, -oy)
        pad_r = max(0.0, ox + need_w - wi)
        pad_b = max(0.0, oy + need_h - hi)
        if pad_l or pad_t or pad_r or pad_b:
            import math
            pl, pt = math.ceil(pad_l), math.ceil(pad_t)
            padded = Image.new("RGB", (wi + pl + math.ceil(pad_r), hi + pt + math.ceil(pad_b)), crop.background)
            padded.paste(image, (pl, pt))
            image, ox, oy = padded, ox + pl, oy + pt
        self.image = image
        self.ox, self.oy = ox, oy
        self.canvas = canvas
        self.crop_box = (ox, oy, ox + need_w, oy + need_h)

    def box(self, r: Rect) -> tuple[float, float, float, float]:
        s = self.scale
        x0, y0 = self.ox + r.x * s, self.oy + r.y * s
        x1, y1 = self.ox + r.x1 * s, self.oy + r.y1 * s
        w, h = self.image.size
        # float rounding can push a hair past the edge
        return (max(0.0, x0), max(0.0, y0), min(float(w), x1), min(float(h), y1))

    def render(self, r: Rect, size: tuple[int, int]) -> tuple[Image.Image, float]:
        """Render physical rect ``r`` to an image of ``size`` px. Returns (image, upscale)."""
        box = self.box(r)
        img = self.image.resize(size, Image.Resampling.LANCZOS, box=box)
        upscale = max(size[0] / max(box[2] - box[0], 1e-9), size[1] / max(box[3] - box[1], 1e-9))
        return img, upscale


def save_image(img: Image.Image, path: Path, fmt: str = "png", quality: int = 95,
               png_compression: int = 1) -> Path:
    """Save a slice. PNG is lossless at every level; 1 is ~5x faster than 6 for
    photos, with files about a third larger."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt in ("jpg", "jpeg"):
        img.save(path, "JPEG", quality=quality, subsampling=0, optimize=True)
    elif fmt == "webp":
        img.save(path, "WEBP", quality=quality, method=6)
    else:
        img.save(path, "PNG", compress_level=png_compression)
    return path


def render_preview(source: Image.Image, mapping: Mapping, layout: Layout, width_px: int = 2400) -> Image.Image:
    """A to-scale picture of the desk: dimmed full image, housings, lit areas, labels."""
    xs = [p.outer.x for p in layout] + [p.outer.x1 for p in layout]
    ys = [p.outer.y for p in layout] + [p.outer.y1 for p in layout]
    margin = 0.04 * (max(xs) - min(xs))
    vx0, vy0 = min(xs) - margin, min(ys) - margin
    vw, vh = max(xs) - min(xs) + 2 * margin, max(ys) - min(ys) + 2 * margin
    k = width_px / vw  # preview px per mm
    size = (width_px, int(round(vh * k)))

    # downscale the source first so the affine sample is not aliased
    src = mapping.image
    shrink = max(1.0, mapping.scale / k)
    if shrink > 1.0:
        src = src.resize((max(1, int(src.width / shrink)), max(1, int(src.height / shrink))),
                         Image.Resampling.LANCZOS)
    s = mapping.scale / shrink
    ox, oy = mapping.ox / shrink, mapping.oy / shrink
    # output px (u, v) -> mm (vx0 + u/k, vy0 + v/k) -> src px
    a = s / k
    data = (a, 0, ox + vx0 * s, 0, a, oy + vy0 * s)
    full = src.transform(size, Image.Transform.AFFINE, data, Image.Resampling.BICUBIC, fillcolor=(24, 24, 24))

    dim = Image.blend(full, Image.new("RGB", size, (24, 24, 24)), 0.65)
    out = dim.copy()
    draw = ImageDraw.Draw(out)

    def px(r: Rect):
        return (int(round((r.x - vx0) * k)), int(round((r.y - vy0) * k)),
                int(round((r.x1 - vx0) * k)) - 1, int(round((r.y1 - vy0) * k)) - 1)

    for p in layout:
        draw.rectangle(px(p.outer), fill=(40, 40, 44), outline=(90, 90, 96), width=2)
    for p in layout:
        x0, y0, x1, y1 = px(p.active)
        out.paste(full.crop((x0, y0, x1 + 1, y1 + 1)), (x0, y0))
    # outline of the area the image was cropped to
    cb = px(layout.canvas)
    draw.rectangle(cb, outline=(255, 200, 0), width=2)
    try:
        font = ImageFont.load_default(size=max(14, width_px // 90))
    except TypeError:
        font = ImageFont.load_default()
    for p in layout:
        x0, y0, _, _ = px(p.active)
        m = p.monitor
        label = f"{m.name}  {m.pixels[0]}x{m.pixels[1]}"
        draw.text((x0 + 8, y0 + 8), label, fill=(255, 255, 255), font=font,
                  stroke_width=2, stroke_fill=(0, 0, 0))
    return out
