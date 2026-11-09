"""Stacked and 2x2 arrangements: layout, matching to the desktop, detect skeleton."""

import pytest

from panescape.config import ConfigError, configuration_from_dict
from panescape.layout import compute_layout

QHD = {"resolution": "QHD", "active_area": [596.7, 335.7], "bezel": 10}

GRID = {"monitors": [
    {"name": "top-left", **QHD},
    {"name": "top-right", **QHD, "right_of": "top-left", "gap": 2},
    {"name": "bottom-left", **QHD, "below": "top-left", "gap": 4, "h_align": "left"},
    {"name": "bottom-right", **QHD, "right_of": "bottom-left", "gap": 2, "v_align": "top"},
]}


def _placed(cfg):
    return {p.monitor.name: p for p in compute_layout(cfg)}


def test_two_by_two_grid():
    p = _placed(configuration_from_dict(GRID))
    tl, tr, bl, br = p["top-left"], p["top-right"], p["bottom-left"], p["bottom-right"]
    assert tr.outer.x == pytest.approx(tl.outer.x1 + 2) and tr.outer.y == pytest.approx(tl.outer.y)
    assert bl.outer.y == pytest.approx(tl.outer.y1 + 4) and bl.outer.x == pytest.approx(tl.outer.x)
    assert br.outer.x == pytest.approx(tr.outer.x) and br.outer.y == pytest.approx(bl.outer.y)


def test_stack_with_offset_and_above():
    cfg = configuration_from_dict({"monitors": [
        {"name": "main", **QHD},
        {"name": "laptop", "resolution": "FHD", "active_area": [344, 194], "bezel": 5,
         "below": "main", "gap": 60, "h_align": "center", "h_pos": -100},
        {"name": "tv", "resolution": "4K", "size": "55in", "above": "main", "gap": 30, "h_align": "right"},
    ]})
    p = _placed(cfg)
    main, laptop, tv = p["main"], p["laptop"], p["tv"]
    assert laptop.outer.y == pytest.approx(main.outer.y1 + 60)
    centre = lambda r: r.x + r.w / 2  # noqa: E731
    assert centre(laptop.outer) - centre(main.outer) == pytest.approx(-100)
    assert tv.outer.y1 == pytest.approx(main.outer.y - 30)
    assert tv.outer.x1 == pytest.approx(main.outer.x1)


def test_forward_references_and_cycles():
    # a monitor may refer to one listed after it
    cfg = configuration_from_dict({"monitors": [
        {"name": "a", **QHD},
        {"name": "c", **QHD, "below": "b"},
        {"name": "b", **QHD, "right_of": "a"},
    ]})
    p = _placed(cfg)
    assert p["c"].outer.x == pytest.approx(p["b"].outer.x + (p["b"].outer.w - p["c"].outer.w) / 2)
    with pytest.raises(ValueError, match="circle"):
        compute_layout(configuration_from_dict({"monitors": [
            {"name": "a", **QHD},
            {"name": "b", **QHD, "right_of": "c"},
            {"name": "c", **QHD, "below": "b"},
        ]}))
    with pytest.raises(ConfigError):
        configuration_from_dict({"monitors": [{"name": "a", **QHD, "below": "b"}, {"name": "b", **QHD}]})
    with pytest.raises(ConfigError):
        configuration_from_dict({"monitors": [{"name": "a", **QHD}, {"name": "b", **QHD, "below": "x"}]})


def _os(name, x, y, w=2560, h=1440, product="P"):
    from panescape.backends.gnome import OsMonitor
    return OsMonitor(name, "V", product, name, name, x, y, w, h, 1.0, 0, (w, h), False, False)


def test_grid_matches_by_arrangement_not_x_order():
    from panescape.backends.gnome import OsLayout, match_monitors
    cfg = configuration_from_dict(GRID)
    centres = {p.monitor.name: (p.active.x + p.active.w / 2, p.active.y + p.active.h / 2)
               for p in compute_layout(cfg)}
    lay = OsLayout([_os("DP-1", 0, 0), _os("DP-2", 2560, 0), _os("DP-3", 0, 1440), _os("DP-4", 2560, 1440)],
                   logical=True)
    pairing = match_monitors(cfg.monitors, lay, centres)
    assert {k: v.connector for k, v in pairing.items()} == {
        "top-left": "DP-1", "top-right": "DP-2", "bottom-left": "DP-3", "bottom-right": "DP-4"}


def test_skeleton_for_grid_and_column():
    from panescape.skeleton import config_skeleton
    grid = config_skeleton([_os("DP-4", 2560, 1440), _os("DP-1", 0, 0), _os("DP-3", 0, 1440),
                            _os("DP-2", 2560, 0)], "g")
    by = {e["name"]: e for e in grid["monitors"]}
    assert list(by) == ["top-left", "top-right", "bottom-left", "bottom-right"]
    assert by["top-right"]["right_of"] == "top-left" and by["top-right"]["v_align"] == "top"
    assert by["bottom-left"]["below"] == "top-left" and by["bottom-left"]["h_align"] == "left"
    assert by["bottom-right"]["right_of"] == "bottom-left"
    cfg = configuration_from_dict({"monitors": [{**e, "resolution": "QHD", "size": "27in"}
                                                for e in grid["monitors"]]})
    assert len(compute_layout(cfg).placed) == 4

    col = config_skeleton([_os("HDMI-1", 300, 1440, 1920, 1080), _os("DP-1", 0, 0)], "c")
    assert [e["name"] for e in col["monitors"]] == ["top", "bottom"]
    assert col["monitors"][1]["below"] == "top" and col["monitors"][1]["h_align"] == "center"
