"""Calibrating from a photo: a fake photo of a known desk, taken by a known camera,
must give the desk's numbers back."""

import copy

import pytest

np = pytest.importorskip("numpy")

from PIL import Image, ImageChops, ImageFilter  # noqa: E402

from panescape import perspective, photocal, yamledit  # noqa: E402
from panescape.calibrate import photo_pattern  # noqa: E402
from panescape.config import configuration_from_dict  # noqa: E402
from panescape.layout import compute_layout  # noqa: E402

ANGLED = {"viewer": {"distance": 620, "height": 60, "offset": -30}, "monitors": [
    {"name": "centre", "resolution": "FHD", "active_area": [527, 296.5], "bezel": 6},
    {"name": "left", "resolution": "4K", "active_area": [620.9, 341.3], "rotation": "portrait-right", "bezel": 12,
     "left_of": "centre", "gap": 3, "v_pos": 25, "angle": 28, "tilt": 2},
    {"name": "right", "resolution": "4K", "active_area": [620.9, 341.3], "rotation": "portrait-left", "bezel": 12,
     "right_of": "centre", "gap": 1.5, "v_pos": 18, "angle": 33}]}
LAPTOP = {"viewer": {"distance": 560, "height": -40}, "monitors": [
    {"name": "monitor", "resolution": "4K", "active_area": [697.3, 392.2], "bezel": 8},
    {"name": "laptop", "resolution": "1920x1200", "active_area": [301.6, 188.5], "bezel": 6,
     "below": "monitor", "gap": 95, "h_pos": -40, "tilt": 24}]}


def fake_photo(raw, rotvec=(0.06, -0.05, 0.015), f=1000.0, size=(3000, 2000), messy=False) -> Image.Image:
    """What a camera at the viewer's eyes sees while the screens show the photo pattern."""
    lay = compute_layout(configuration_from_dict(raw))
    eye = np.array(lay.eye)
    m = photocal._rodrigues(np.array(rotvec)) @ np.array(photocal.FLIP)
    cx, cy = size[0] / 2, size[1] / 2
    photo = Image.new("RGB", size, (25, 22, 20))
    for p in lay:
        pw, ph = (v // 2 for v in p.monitor.pixels)
        quad = []
        for c in p.corners3d:
            v = m @ (np.array(c) - eye)
            quad.append((cx + f * v[0] / v[2], cy + f * v[1] / v[2]))
        h = perspective.solve_homography(quad, [(0, 0), (pw, 0), (pw, ph), (0, ph)])
        screen = photo_pattern((pw, ph)).transform(size, Image.Transform.PERSPECTIVE, h[:8],
                                                   Image.Resampling.BICUBIC, fillcolor=(0, 0, 0))
        photo = ImageChops.lighter(photo, screen)
    if messy:  # out of focus, dimmer screens, uneven room light, sensor noise
        a = np.asarray(photo.filter(ImageFilter.GaussianBlur(2.5)), float)
        hh, ww = a.shape[:2]
        a = a * 0.8 + np.linspace(0, 60, ww)[None, :, None] + np.linspace(0, 30, hh)[:, None, None]
        a += np.random.default_rng(1).normal(0, 8, a.shape)
        photo = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    return photo


def unmeasured(raw):
    """The layout as someone starts: no viewer, no gaps, offsets or angles."""
    out = copy.deepcopy(raw)
    out.pop("viewer", None)
    for e in out["monitors"][1:]:
        for k in ("gap", "v_pos", "h_pos", "angle", "tilt"):
            e.pop(k, None)
    return out


@pytest.mark.parametrize("messy", [False, True])
def test_an_angled_desk_is_measured_from_one_photo(tmp_path, messy):
    path = tmp_path / "desk.jpg"
    fake_photo(ANGLED, messy=messy).save(path, quality=88)
    res = photocal.analyse(path, unmeasured(ANGLED))
    assert not res.flat and res.rms_mm < 0.5
    left, right = res.monitors["left"], res.monitors["right"]
    assert left["gap"] == pytest.approx(3, abs=0.5) and left["v_pos"] == pytest.approx(25, abs=0.5)
    assert left["angle"] == pytest.approx(28, abs=0.5) and left["tilt"] == pytest.approx(2, abs=0.5)
    assert right["gap"] == pytest.approx(1.5, abs=0.6) and right["angle"] == pytest.approx(33, abs=0.5)
    assert res.viewer["distance"] == pytest.approx(620, abs=10)
    assert res.viewer["height"] == pytest.approx(60, abs=5)
    assert res.viewer["offset"] == pytest.approx(-30, abs=5)


def test_a_flat_desk_only_gets_gaps_and_offsets(tmp_path):
    flat = copy.deepcopy(ANGLED)
    for e in flat["monitors"]:
        e.pop("angle", None)
        e.pop("tilt", None)
    path = tmp_path / "desk.png"
    fake_photo(flat, rotvec=(0.03, 0.02, -0.01)).save(path)
    raw = unmeasured(flat)
    res = photocal.analyse(path, raw)
    assert res.flat
    found = photocal.changes(res, raw)
    assert {k for _, k, _, _ in found} == {"gap", "v_pos"}  # angled screens stay opt-in
    doc = yamledit.loads("# mine\n" + yamledit.safe_dump(raw))
    photocal.apply_changes(doc, found)
    text = yamledit.dumps(doc)
    assert text.startswith("# mine") and "viewer" not in text
    cfg = configuration_from_dict(yamledit.plain(doc))
    assert cfg.monitors[1].v_pos == pytest.approx(25, abs=0.5)


def test_a_laptop_leaning_back(tmp_path):
    path = tmp_path / "laptop.png"
    fake_photo(LAPTOP, rotvec=(0.25, 0, 0), f=1300).save(path)
    res = photocal.analyse(path, unmeasured(LAPTOP))
    laptop = res.monitors["laptop"]
    assert laptop["tilt"] == pytest.approx(24, abs=1)
    assert laptop["gap"] == pytest.approx(95, abs=1.5) and laptop["h_pos"] == pytest.approx(-40, abs=1)


def test_problems_are_explained(tmp_path):
    raw = unmeasured(ANGLED)
    fake_photo(ANGLED, f=1900).save(tmp_path / "cut.png")   # zoomed in: the side screens are cut off
    with pytest.raises(photocal.PhotoError, match="cut off"):
        photocal.analyse(tmp_path / "cut.png", raw)
    Image.new("RGB", (800, 600)).save(tmp_path / "dark.png")
    with pytest.raises(photocal.PhotoError, match="expected 3"):
        photocal.analyse(tmp_path / "dark.png", raw)
    (tmp_path / "x.heic").write_bytes(b"nope")
    with pytest.raises(photocal.PhotoError, match="can't open"):
        photocal.analyse(tmp_path / "x.heic", raw)


def test_the_photo_pattern_is_inset_white_on_black():
    img = photo_pattern((400, 200))
    assert img.getpixel((0, 0)) == (0, 0, 0) and img.getpixel((200, 100)) == (255, 255, 255)
    assert img.getpixel((9, 100)) == (0, 0, 0) and img.getpixel((10, 100)) == (255, 255, 255)
