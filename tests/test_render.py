import pytest
from helpers import DESK
from PIL import Image

from panescape.config import configuration_from_dict
from panescape.layout import compute_layout
from panescape.render import CropSpec, Mapping, render_preview


def test_cover_crops_and_is_physical():
    layout = compute_layout(configuration_from_dict(DESK))
    m = Mapping(Image.new("RGB", (4000, 1000)), layout.canvas, CropSpec())
    x0, y0, x1, y1 = m.crop_box
    assert (y0, y1) == (0, 1000) and x0 > 0          # image wider than the desk: sides cropped
    # equal mm => equal source px on every monitor
    left, centre, _ = layout.placed
    bl, bc = m.box(left.active), m.box(centre.active)
    assert (bl[2] - bl[0]) / left.active.w == pytest.approx((bc[2] - bc[0]) / centre.active.w)


def test_contain_pads_instead_of_cropping():
    layout = compute_layout(configuration_from_dict(DESK))
    m = Mapping(Image.new("RGB", (4000, 1000), "white"), layout.canvas, CropSpec(fit="contain"))
    left = layout.placed[0]
    tile, _ = m.render(left.active, (216, 384))
    assert tile.getpixel((100, 2)) == (0, 0, 0)       # bar above the image
    assert tile.getpixel((100, 192)) == (255, 255, 255)


def test_anchor_parsing():
    assert CropSpec.from_dicts({"anchor": "top"}).anchor == (0.5, 0.0)
    assert CropSpec.from_dicts({"anchor": "center"}, {"anchor": [0.2, 0.8]}).anchor == (0.2, 0.8)
    with pytest.raises(ValueError):
        CropSpec.from_dicts({"anchor": "sideways"})


def test_preview_renders():
    layout = compute_layout(configuration_from_dict(DESK))
    img = Image.new("RGB", (3000, 1500), "navy")
    m = Mapping(img, layout.canvas, CropSpec())
    prev = render_preview(img, m, layout, width_px=800)
    assert prev.width == 800 and prev.height > 300
