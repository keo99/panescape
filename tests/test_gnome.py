import pytest
from helpers import DESK, os_layout
from PIL import Image

from panescape.backends import gnome
from panescape.config import configuration_from_dict


def test_match_by_position_and_serial():
    cfg = configuration_from_dict(DESK)
    pairing = gnome.match_monitors(cfg.monitors, os_layout())
    assert {k: v.connector for k, v in pairing.items()} == {"left": "DP-6", "centre": "DP-8", "right": "DP-7"}
    cfg.monitors[0].match, cfg.monitors[2].match = {"serial": "DP-7"}, {"serial": "DP-6"}
    pairing = gnome.match_monitors(cfg.monitors, os_layout())
    assert pairing["left"].connector == "DP-7" and pairing["right"].connector == "DP-6"


def test_compose_uses_logical_layout_at_max_scale():
    lay = os_layout()
    seen = {}

    def render_for(om, size):
        seen[om.connector] = size
        return Image.new("RGB", size, "white")

    img = gnome.compose(lay, render_for)
    assert img.size == (7200, 3933)                   # (4800 x 2622 logical) x 1.5
    assert seen == {"DP-6": (2160, 3840), "DP-8": (2880, 1620), "DP-7": (2160, 3840)}
    assert img.getpixel((10, 10)) == (0, 0, 0)        # DP-6 starts 62 logical px down


def test_slideshow_roundtrip(tmp_path):
    files = [tmp_path / "a.png", tmp_path / "b.png"]
    xml = gnome.write_slideshow(tmp_path / "s.xml", files, 600, 2, start=1_700_000_000)
    got, interval, trans = gnome.read_slideshow(xml)
    assert got == files and interval == pytest.approx(600) and trans == pytest.approx(2)
    single = gnome.write_slideshow(tmp_path / "one.xml", files[:1], 60, 0)
    assert gnome.read_slideshow(single)[0] == files[:1]


def test_arrangement_matching_keeps_row_order_with_offsets():
    # small vertical offsets must not outweigh the left-to-right order
    from panescape.layout import compute_layout
    cfg = configuration_from_dict(DESK)
    centres = {p.monitor.name: (p.active.x + p.active.w / 2, p.active.y + p.active.h / 2)
               for p in compute_layout(cfg)}
    pairing = gnome.match_monitors(cfg.monitors, os_layout(), centres)
    assert {k: v.connector for k, v in pairing.items()} == {"left": "DP-6", "centre": "DP-8", "right": "DP-7"}
