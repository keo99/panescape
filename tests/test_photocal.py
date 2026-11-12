"""Calibrating from photos: fake photos of a known desk, taken by a known camera,
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


def fake_photo(raw, rotvec=(0.06, -0.05, 0.015), f=1000.0, size=(3000, 2000), messy=False,
               clutter=False) -> Image.Image:
    """What a camera at the viewer's eyes sees while the screens show the photo pattern.
    ``clutter``: a top bar across the first screen, a desktop icon over a panel's edge and
    another beside the dots, glare above a panel and a lamp in the room."""
    from PIL import ImageDraw
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
        pattern = photo_pattern((pw, ph), p.monitor.index + 1)
        if clutter and p.monitor.index == 0:
            d = ImageDraw.Draw(pattern)
            d.rectangle((0, 0, pw, round(0.03 * ph)), fill=(40, 40, 40))           # the top bar
            k = round(0.1 * min(pw, ph))
            d.rectangle((k - 20, ph // 3, k + 40, ph // 3 + 60), fill=(90, 60, 30))  # an icon over the edge
            d.rectangle((pw // 3, ph // 2 + 30, pw // 3 + 45, ph // 2 + 75), fill=(200, 90, 20))  # beside the dots
        screen = pattern.transform(size, Image.Transform.PERSPECTIVE, h[:8],
                                   Image.Resampling.BICUBIC, fillcolor=(0, 0, 0))
        photo = ImageChops.lighter(photo, screen)
        if clutter and p.monitor.index == 1:  # glare on the glossy border above this panel
            k = 0.1 * min(pw, ph)
            glare = Image.new("RGB", (pw, ph))
            ImageDraw.Draw(glare).polygon([(pw * 0.3, 2), (pw * 0.7, 2), (pw * 0.62, k + 2), (pw * 0.38, k + 2)],
                                          fill=(200, 200, 200))
            photo = ImageChops.lighter(photo, glare.transform(size, Image.Transform.PERSPECTIVE, h[:8],
                                                              Image.Resampling.BICUBIC, fillcolor=(0, 0, 0)))
    if clutter:  # a lamp
        ImageDraw.Draw(photo).ellipse((size[0] * 0.45, 20, size[0] * 0.45 + 160, 180), fill=(255, 250, 235))
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
    fake_photo(ANGLED, messy=messy, clutter=messy).save(path, quality=88)
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


def test_two_photos_when_the_desk_doesnt_fit_in_one(tmp_path):
    left, right = tmp_path / "left.png", tmp_path / "right.png"
    fake_photo(ANGLED, rotvec=(0.06, 0.3, 0), f=1200).save(left)     # turned left: left + centre (right cut off)
    fake_photo(ANGLED, rotvec=(0.06, -0.3, 0), f=1200).save(right)   # turned right: centre + right (left cut off)
    res = photocal.analyse([left, right], unmeasured(ANGLED))
    assert set(res.observed[0]) == {"left", "centre"} and set(res.observed[1]) == {"centre", "right"}
    assert res.monitors["left"]["angle"] == pytest.approx(28, abs=0.5)
    assert res.monitors["right"]["angle"] == pytest.approx(33, abs=0.5)
    assert res.monitors["right"]["v_pos"] == pytest.approx(18, abs=0.5)
    assert res.viewer["distance"] == pytest.approx(620, abs=10)


def test_problems_are_explained(tmp_path):
    raw = unmeasured(ANGLED)
    fake_photo(ANGLED, f=1900).save(tmp_path / "cut.png")   # zoomed in: the side screens are cut off
    with pytest.raises(photocal.PhotoError, match="in none of the photos"):
        photocal.analyse(tmp_path / "cut.png", raw)
    Image.new("RGB", (800, 600)).save(tmp_path / "dark.png")
    with pytest.raises(photocal.PhotoError, match="no screens"):
        photocal.analyse(tmp_path / "dark.png", raw)
    (tmp_path / "x.heic").write_bytes(b"nope")
    with pytest.raises(photocal.PhotoError, match="can't open"):
        photocal.analyse(tmp_path / "x.heic", raw)


def test_the_photo_pattern_is_inset_white_on_black_with_numbered_dots():
    img = photo_pattern((400, 200), 3)
    assert img.getpixel((0, 0)) == (0, 0, 0) and img.getpixel((200, 60)) == (255, 255, 255)
    assert img.getpixel((19, 60)) == (0, 0, 0) and img.getpixel((20, 60)) == (255, 255, 255)
    row = [img.getpixel((x, 100))[0] for x in range(20, 380)]  # across the panel
    dots = sum(1 for a, b in zip(row, row[1:], strict=False) if a == 255 and b == 0)
    assert dots == 3
