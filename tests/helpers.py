"""Shared test fixtures: a three-monitor desk and a fake desktop backend.

Imports are inside the functions so the pure-geometry tests don't pull in
the rest of the package.
"""

from __future__ import annotations

from pathlib import Path

# two portrait 4K 28" panels either side of a landscape 24" FHD panel
DESK = {
    "monitors": [
        {"name": "left", "model": "Samsung U28E590", "rotation": "portrait-right",
         "bezel": {"left": 20, "right": 10, "top": 15, "bottom": 15}},
        {"name": "centre", "model": "Dell P2425H", "bezel": 6, "gap": 2, "v_pos": -20},
        {"name": "right", "model": "Samsung U28E590", "rotation": "portrait-left", "bezel": 10, "v_pos": 0},
    ]
}


def os_layout():
    """What GNOME reports for DESK: logical pixels, 4K panels at 150 %."""
    from panescape.backends import gnome

    def mk(c, x, y, w, h, s, r):
        return gnome.OsMonitor(c, "V", "P", c, c, x, y, w, h, s, r, (0, 0), False, False)
    return gnome.OsLayout([mk("DP-6", 0, 62, 1440, 2560, 1.5, 90),
                           mk("DP-8", 1440, 765, 1920, 1080, 1.0, 0),
                           mk("DP-7", 3360, 0, 1440, 2560, 1.5, 270)], logical=True)


def fake_backend(layout, applied: list):
    """The GNOME backend with the desktop calls replaced (records what would be set)."""
    import types

    from panescape.backends import gnome
    be = types.ModuleType("fakegnome")
    be.get_layout = lambda: layout
    be.match_monitors = gnome.match_monitors
    be.compose = gnome.compose
    be.write_slideshow = gnome.write_slideshow
    be.read_slideshow = gnome.read_slideshow
    be.set_wallpaper = lambda p: applied.append(Path(p))
    return be


def make_project(tmp_path: Path, names=("a", "b", "c")):
    from PIL import Image

    from panescape import yamledit
    from panescape.project import Project
    (tmp_path / "pictures").mkdir()
    (tmp_path / "configurations").mkdir()
    (tmp_path / "configurations" / "desk.yml").write_text(yamledit.safe_dump(DESK))
    colours = ["red", "blue", "green", "white", "yellow"]
    for n, col in zip(names, colours[: len(names)], strict=True):
        Image.new("RGB", (1200, 600), col).save(tmp_path / "pictures" / f"{n}.png")
    return Project(tmp_path)
