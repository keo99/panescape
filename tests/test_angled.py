"""Angled and tilted screens seen from one seat (opt-in with ``viewer:``)."""

import copy

import pytest
from helpers import DESK

from panescape import perspective
from panescape.config import ConfigError, configuration_from_dict
from panescape.layout import compute_layout

FLAT = {"monitors": [
    {"name": "centre", "resolution": "FHD", "active_area": [527, 296.5], "bezel": 6},
    {"name": "left", "resolution": "FHD", "active_area": [527, 296.5], "bezel": 6, "left_of": "centre", "gap": 2},
    {"name": "right", "resolution": "FHD", "active_area": [527, 296.5], "bezel": 6, "right_of": "centre", "gap": 2},
]}


def angled(**viewer):
    raw = copy.deepcopy(FLAT)
    raw["viewer"] = viewer or {"distance": 600}
    raw["monitors"][1]["angle"] = 30
    raw["monitors"][2]["angle"] = 30
    return raw


def test_viewer_and_angles_are_read_from_yaml():
    cfg = configuration_from_dict(angled(distance=650, height=40))
    assert (cfg.viewer.distance, cfg.viewer.height, cfg.viewer.offset) == (650, 40, 0)
    assert [m.angle for m in cfg.monitors] == [0, 30, 30]
    raw = copy.deepcopy(FLAT)
    raw["viewer"] = {}
    raw["monitors"][2]["tilt"] = "12.5deg"
    cfg = configuration_from_dict(raw)
    assert cfg.viewer.distance == 600 and cfg.monitors[2].tilt == 12.5
    assert configuration_from_dict(FLAT).viewer is None


@pytest.mark.parametrize("change, message", [
    (lambda r: r["viewer"].update(distance=50), "at least 100"),
    (lambda r: r["viewer"].update(eyes=1), "unknown viewer key"),
    (lambda r: r["monitors"][0].update(angle=10), "first monitor"),
    (lambda r: r["monitors"][1].update(angle=95), "between -80 and 80"),
    (lambda r: r["monitors"][1].update(tilt="steep"), "degrees"),
])
def test_bad_values_are_explained(change, message):
    raw = angled()
    change(raw)
    with pytest.raises(ConfigError, match=message):
        configuration_from_dict(raw)


def test_a_viewer_with_a_flat_desk_changes_nothing():
    flat = compute_layout(configuration_from_dict(DESK))
    raw = copy.deepcopy(DESK)
    raw["viewer"] = {"distance": 500, "height": 80, "offset": -100}
    seated = compute_layout(configuration_from_dict(raw))
    assert seated.canvas == flat.canvas
    for a, b in zip(flat, seated, strict=True):
        assert a.active == b.active and a.outer == b.outer and b.warp is None and b.quad is None


def test_angles_without_a_viewer_are_ignored():
    raw = angled()
    del raw["viewer"]
    lay = compute_layout(configuration_from_dict(raw))
    assert all(p.warp is None for p in lay)
    assert lay.canvas == compute_layout(configuration_from_dict(FLAT)).canvas


def test_angled_screens_meet_at_the_hinge_and_look_right_from_the_seat():
    lay = compute_layout(configuration_from_dict(angled(distance=600)))
    centre, left, right = lay.placed
    assert centre.warp is None and left.warp and right.warp
    eye_y = lay.eye[1]
    # the hinge (right's inner frame edge, through the gap) lines up with centre's edge at every height
    for v in (0, 100, 296.5):
        x, y = perspective.map_point(right.warp, -6 - 2, v)  # lit edge -> frame -> gap: centre's frame edge
        assert x == pytest.approx(centre.outer.x1, abs=1e-6)
        assert y == pytest.approx(right.active.y + v, abs=1e-6)
    # at eye height a line stays level across the angled screen; above it, the far side shows higher up
    level = [perspective.map_point(right.warp, u, eye_y - right.active.y)[1] for u in (0, 250, 527)]
    assert level == pytest.approx([eye_y] * 3)
    near_top = perspective.map_point(right.warp, 0, 0)[1]
    far_top = perspective.map_point(right.warp, 527, 0)[1]
    assert far_top < near_top  # the far edge is closer to you, so it covers more of the picture
    # mirror image on the left
    assert perspective.map_point(left.warp, 0, 0)[1] == pytest.approx(far_top)


def test_a_laptop_leaning_back_below_the_monitor():
    raw = {"viewer": {"distance": 650, "height": -100}, "monitors": [
        {"name": "monitor", "resolution": "FHD", "active_area": [527, 296.5], "bezel": 0},
        {"name": "laptop", "resolution": "FHD", "active_area": [344, 193.5], "bezel": 0,
         "below": "monitor", "gap": 0, "tilt": 30}]}
    lay = compute_layout(configuration_from_dict(raw))
    monitor, laptop = lay.placed
    top_left = perspective.map_point(laptop.warp, 0, 0)
    assert top_left[1] == pytest.approx(monitor.active.y1, abs=1e-6)  # hinged on the monitor's bottom edge
    # leaning back, its bottom edge comes towards you and looks lower than when flat
    assert perspective.map_point(laptop.warp, 0, 193.5)[1] > top_left[1] + 193.5


def test_a_screen_behind_your_eyes_is_an_error():
    raw = angled(distance=150)
    raw["monitors"][2]["angle"] = 80
    with pytest.raises(ValueError, match="past your eyes"):
        compute_layout(configuration_from_dict(raw))


def test_homography_helpers_round_trip():
    src = [(0, 0), (10, 0), (10, 5), (0, 5)]
    dst = [(1, 2), (13, 1), (12, 9), (2, 7)]
    h = perspective.solve_homography(src, dst)
    for a, b in zip(src, dst, strict=True):
        assert perspective.map_point(h, *a) == pytest.approx(b)
    back = perspective.invert(h)
    assert perspective.map_point(back, 13, 1) == pytest.approx((10, 0))


def _render(tmp_path, raw):
    from helpers import make_project
    from PIL import Image

    from panescape import yamledit
    tmp_path.mkdir(parents=True, exist_ok=True)
    proj = make_project(tmp_path, names=("a",))
    ramp = Image.linear_gradient("L").resize((1200, 600))  # a picture with detail, so warping shows
    Image.merge("RGB", (ramp, ramp.rotate(90).resize((1200, 600)), ramp)).save(tmp_path / "pictures" / "a.png")
    (tmp_path / "configurations" / "desk.yml").write_text(yamledit.safe_dump(raw))
    cfg = proj.load_config(None)
    return proj.render(cfg, proj.resolve_images(["a.png"])[0], force=True), cfg


def test_a_flat_desk_with_a_viewer_renders_the_same_pixels(tmp_path):
    from PIL import Image, ImageChops
    plain, _ = _render(tmp_path / "plain", copy.deepcopy(FLAT))
    seated_raw = copy.deepcopy(FLAT)
    seated_raw["viewer"] = {"distance": 500, "height": 50}
    seated, _ = _render(tmp_path / "seated", seated_raw)
    for name in plain.slices:
        with Image.open(plain.slices[name]) as a, Image.open(seated.slices[name]) as b:
            assert ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox() is None


def test_angled_screens_render_warped_slices_and_patterns(tmp_path):
    from PIL import Image

    from panescape import calibrate
    rendered, cfg = _render(tmp_path, angled(distance=600))
    for m in cfg.monitors:
        with Image.open(rendered.slices[m.name]) as im:
            assert im.size == m.pixels
    flat, _ = _render(tmp_path / "flat", copy.deepcopy(FLAT))
    with Image.open(rendered.slices["right"]) as a, Image.open(flat.slices["right"]) as b:
        assert list(a.convert("RGB").getdata()) != list(b.convert("RGB").getdata())
    lay = rendered.layout
    for p in lay:
        assert calibrate.draw_placed(lay, p, (192, 108)).size == (192, 108)
