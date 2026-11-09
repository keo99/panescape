import pytest
from helpers import DESK

from panescape import calibrate
from panescape.config import configuration_from_dict
from panescape.layout import compute_layout


def test_pattern_tiles_render_at_native_size():
    layout = compute_layout(configuration_from_dict(DESK))
    for p in layout:
        img = calibrate.draw(layout, p.active, p.monitor.pixels, p)
        assert img.size == p.monitor.pixels
        assert len(set(img.getdata())) > 3  # lines, rulers and labels were drawn


def test_horizontal_lines_are_continuous_in_desk_space():
    # the same desk y must land on the same row of pixels relative to each screen's top
    layout = compute_layout(configuration_from_dict(DESK))
    left, centre, _ = layout.placed
    y = 300.0  # a horizontal grid line visible on both
    for p in (left, centre):
        img = calibrate.draw(layout, p.active, p.monitor.pixels, None)
        row = round((y - p.active.y) * p.monitor.pixels[1] / p.active.h)
        assert img.getpixel((p.monitor.pixels[0] // 2 + 7, row)) == calibrate.H_LINE


def test_lit_gaps():
    layout = compute_layout(configuration_from_dict(DESK))
    left, centre, right = layout.placed
    gaps = calibrate.lit_gaps(layout, centre)
    # left's right bezel 10 + gap 2 + centre's left bezel 6
    assert gaps == {"left": ("left", pytest.approx(18.0))}
    assert calibrate.lit_gaps(layout, left) == {}
