import pytest
from helpers import DESK

from panescape import units
from panescape.config import configuration_from_dict, rotate_bezel
from panescape.layout import compute_layout


def test_units():
    assert units.parse_length("28in") == pytest.approx(711.2)
    assert units.parse_length('1"') == pytest.approx(25.4)
    assert units.parse_length(3, "mm") == 3
    assert units.parse_resolution("4K") == (3840, 2160)
    assert units.parse_resolution("1080x1920") == (1920, 1080)
    assert units.parse_duration("1h30m") == 5400
    assert units.parse_duration("90s") == 90
    with pytest.raises(ValueError):
        units.parse_duration("ten minutes")


def test_rotate_bezel_portrait_right_moves_top_to_right():
    native = {"top": 1, "right": 2, "bottom": 3, "left": 4}
    assert rotate_bezel(native, 90) == {"right": 1, "bottom": 2, "left": 3, "top": 4}
    assert rotate_bezel(native, 270) == {"left": 1, "top": 2, "right": 3, "bottom": 4}


def test_layout_positions():
    cfg = configuration_from_dict(DESK)
    left, centre, right = compute_layout(cfg).placed
    assert left.monitor.pixels == (2160, 3840)
    assert left.monitor.active == pytest.approx((341.28, 620.93))
    # housings touch except for the 2 mm gap
    assert centre.outer.x == pytest.approx(left.outer.x1 + 2)
    assert right.outer.x == pytest.approx(centre.outer.x1)
    # centre housing is 20 mm lower (y grows downwards)
    lc = left.outer.y + left.outer.h / 2
    cc = centre.outer.y + centre.outer.h / 2
    assert cc - lc == pytest.approx(20)
    # active area sits inside the bezels
    assert centre.active.x - centre.outer.x == pytest.approx(6)



def test_gap_is_air_between_frames_and_bezels_add_on_top():
    cfg = configuration_from_dict({"monitors": [
        {"name": "a", "resolution": "FHD", "active_area": [500, 280], "bezel": 8},
        {"name": "b", "resolution": "FHD", "active_area": [500, 280], "bezel": 6, "gap": 2},
    ]})
    a, b = compute_layout(cfg).placed
    assert b.outer.x - a.outer.x1 == pytest.approx(2)        # the gap you measure with a ruler
    assert b.active.x - a.active.x1 == pytest.approx(8 + 2 + 6)  # lit areas: bezel + gap + bezel

def test_size_from_diagonal_and_legacy_form():
    cfg = configuration_from_dict({"monitors": {
        "layout": "LR", "edges": "joined",
        1: {"resolution": "3840x2160", "orientation": "vertical", "size": 28},
        2: {"resolution": "FHD", "orientation": "horizontal", "size": 24, "v_pos": -20, "spacing": 3, "overlap": 1},
    }})
    a, b = cfg.monitors
    assert a.rotation == 90 and a.active[1] == pytest.approx(619.87, abs=0.05)
    assert b.gap == 2


def test_v_align_top_and_explicit_position():
    cfg = configuration_from_dict({"monitors": [
        {"name": "a", "resolution": "FHD", "active_area": [500, 300]},
        {"name": "b", "resolution": "FHD", "active_area": [500, 300], "v_align": "top", "v_pos": 10},
        {"name": "c", "resolution": "FHD", "active_area": [500, 300], "position": [0, 400]},
    ]})
    a, b, c = compute_layout(cfg).placed
    assert a.outer.y - b.outer.y == pytest.approx(10)    # b's top edge 10 mm higher
    assert c.outer.y - a.outer.y == pytest.approx(400 - (-150))
