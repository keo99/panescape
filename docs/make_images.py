"""Rebuild the README images from a CC0 photo.

    python docs/make_images.py

Photo: "Panorama of the valley of the Triglav mountain massif" by
otrow_photography, CC0 1.0 (public domain), via Wikimedia Commons:
https://commons.wikimedia.org/wiki/File:Panorama_of_the_valley_of_the_Triglav_mountain_massif.jpg
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from panescape.config import load_configuration
from panescape.layout import Layout, Rect, compute_layout
from panescape.render import CropSpec, Mapping, load_image, render_preview

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "images"
CACHE = HERE / ".cache"
PHOTO_URL = ("https://upload.wikimedia.org/wikipedia/commons/f/f1/"
             "Panorama_of_the_valley_of_the_Triglav_mountain_massif.jpg")
CONFIG = ROOT / "examples" / "configurations" / "portrait-landscape-portrait.yml"
WIDTH = 1600
BG, HOUSING, EDGE, TEXT = (246, 246, 244), (38, 38, 42), (70, 70, 76), (30, 30, 30)


def photo() -> Path:
    path = CACHE / "triglav-valley.jpg"
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(PHOTO_URL, headers={"User-Agent": "panescape-docs/0.1"})
        with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
            f.write(r.read())
    return path


def font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def draw_desk(layout: Layout, tiles: dict, title: str, width: int = WIDTH) -> Image.Image:
    """Monitors drawn to physical scale; ``tiles[name]`` fills each lit area."""
    xs = [p.outer.x for p in layout] + [p.outer.x1 for p in layout]
    ys = [p.outer.y for p in layout] + [p.outer.y1 for p in layout]
    margin = 0.03 * (max(xs) - min(xs))
    head = 56
    k = width / (max(xs) - min(xs) + 2 * margin)
    x0, y0 = min(xs) - margin, min(ys) - margin
    h = int((max(ys) - min(ys) + 2 * margin) * k) + head
    img = Image.new("RGB", (width, h), BG)
    d = ImageDraw.Draw(img)
    d.text((int(margin * k), 14), title, fill=TEXT, font=font(26))

    def box(r: Rect):
        return (round((r.x - x0) * k), round((r.y - y0) * k) + head,
                round((r.x1 - x0) * k), round((r.y1 - y0) * k) + head)

    for p in layout:
        d.rounded_rectangle(box(p.outer), radius=6, fill=HOUSING, outline=EDGE, width=2)
    for p in layout:
        bx = box(p.active)
        img.paste(tiles[p.monitor.name].resize((bx[2] - bx[0], bx[3] - bx[1]), Image.Resampling.LANCZOS),
                  bx[:2])
    return img


def pixel_spanning_tiles(img: Image.Image, layout: Layout) -> dict:
    """What pixel-based tools do: lay screens out by pixel count, centred vertically."""
    mons = [p.monitor for p in layout]
    W = sum(m.pixels[0] for m in mons)
    H = max(m.pixels[1] for m in mons)
    s = min(img.width / W, img.height / H)
    ox, oy = (img.width - W * s) / 2, (img.height - H * s) / 2
    tiles, x = {}, 0
    for m in mons:
        w, h = m.pixels
        y = (H - h) / 2
        tiles[m.name] = img.crop((round(ox + x * s), round(oy + y * s),
                                  round(ox + (x + w) * s), round(oy + (y + h) * s)))
        x += w
    return tiles


def panescape_tiles(img: Image.Image, layout: Layout) -> dict:
    m = Mapping(img, layout.canvas, CropSpec())
    out = {}
    for p in layout:
        w, h = p.monitor.pixels
        out[p.monitor.name] = m.render(p.active, (w // 4, h // 4))[0]
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    src = load_image(photo())
    cfg = load_configuration(CONFIG)
    layout = compute_layout(cfg)

    naive = draw_desk(layout, pixel_spanning_tiles(src, layout), "Pixel spanning: sizes jump at every edge")
    ours = draw_desk(layout, panescape_tiles(src, layout), "panescape: one picture, true to scale")
    both = Image.new("RGB", (WIDTH, naive.height + ours.height), BG)
    both.paste(naive, (0, 0))
    both.paste(ours, (0, naive.height))
    both.save(OUT / "comparison.jpg", quality=85, optimize=True, progressive=True)

    mapping = Mapping(src, layout.canvas, CropSpec())
    render_preview(src, mapping, layout, width_px=WIDTH).save(OUT / "preview.jpg", quality=85, optimize=True)
    gui_screenshot(photo())
    for f in sorted(OUT.glob("*.*g")):
        print(f"{f.relative_to(ROOT)}  {f.stat().st_size // 1024} KiB")


def gui_screenshot(picture: Path) -> None:
    """The window on the example desk (needs PySide6; drawn offscreen)."""
    import os
    import shutil
    import tempfile
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QThreadPool
        from PySide6.QtWidgets import QApplication, QLabel
    except ImportError:
        print("PySide6 not installed: skipping the GUI screenshots")
        return
    from panescape.gui.main_window import MainWindow
    from panescape.project import Project
    from panescape.tags import edit_tags
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "panescape"
        (root / "configurations").mkdir(parents=True)
        (root / "pictures").mkdir()
        os.environ["XDG_CACHE_HOME"] = str(Path(tmp) / "cache")
        shutil.copy(CONFIG, root / "configurations" / "my_desk.yml")
        shutil.copy(picture, root / "pictures" / "triglav.jpg")
        # a few views cut from the same CC0 photo, so the grid has something to show
        src = load_image(picture)
        w, h = src.size
        views = {"triglav-peaks.jpg": (0.05, 0.0, 0.55, 0.45), "triglav-meadow.jpg": (0.3, 0.45, 0.95, 1.0),
                 "triglav-forest.jpg": (0.0, 0.3, 0.4, 0.8), "triglav-ridge.jpg": (0.55, 0.05, 1.0, 0.5),
                 "triglav-valley.jpg": (0.2, 0.2, 0.8, 0.75)}
        for name, (x0, y0, x1, y1) in views.items():
            src.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h))).save(root / "pictures" / name,
                                                                                 quality=85)
        tags = {"triglav.jpg": {"landscape", "mountains", "snow", "panorama"},
                "triglav-peaks.jpg": {"mountains", "snow", "sky"}, "triglav-meadow.jpg": {"landscape", "snow"},
                "triglav-forest.jpg": {"trees", "snow"}, "triglav-ridge.jpg": {"mountains", "sky"},
                "triglav-valley.jpg": {"landscape", "mountains"}}
        edit_tags(root / "pictures" / "tags.yml", {n: (t, set()) for n, t in tags.items()})
        (root / "setup.yml").write_text("configuration: my_desk\nmode: slideshow\n")
        app = QApplication.instance() or QApplication([])
        app.setOrganizationName("panescape-docs")
        app.setApplicationName("panescape-docs")
        win = MainWindow(Project(root))
        win.setWindowTitle("panescape")
        win.findChild(QLabel, "project_path").setText("~/panescape")
        win.resize(1400, 800)
        win.show()

        def settle():
            for _ in range(10):
                QThreadPool.globalInstance().waitForDone(10000)
                app.processEvents()

        ed = win.layout_editor
        ed.preview.setCurrentIndex(ed.preview.findText("triglav.jpg"))
        ed.list.setCurrentRow(1)
        settle()
        win.grab().save(str(OUT / "gui-layout.png"))

        win.tabs.setCurrentWidget(win.pictures_tab)
        tab = win.pictures_tab
        tab.filter.setText("snow + !trees")
        tab.apply_filter()
        tab.items["triglav.jpg"].setSelected(True)
        settle()
        win.grab().save(str(OUT / "gui-pictures.png"))
        win.close()


if __name__ == "__main__":
    main()
