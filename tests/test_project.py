from pathlib import Path

import pytest
from helpers import fake_backend, make_project, os_layout
from PIL import Image

from panescape.backends import gnome
from panescape.project import ProjectError, _continue_start
from panescape.tags import save_tags


def test_render_and_explicit_slideshow(tmp_path):
    proj = make_project(tmp_path)
    cfg = proj.load_config(None)
    r = proj.render(cfg, proj.resolve_images(["a.png"])[0])
    assert {p.name for p in r.slices.values()} == {"1_left.png", "2_centre.png", "3_right.png"}
    assert Image.open(r.slices["left"]).size == (2160, 3840)
    assert proj.render(cfg, r.job).skipped  # cached

    applied = []
    be = fake_backend(os_layout(), applied)
    rule = {"source": "cli", "images": ["a", "b.png"], "interval": "5m", "transition": "1s"}
    target = proj.run(be, rule)
    assert target.suffix == ".xml" and applied == [target]
    files, interval, trans = gnome.read_slideshow(target)
    assert len(files) == 2 and interval == pytest.approx(300) and trans == pytest.approx(1)
    comp = Image.open(files[0])
    assert comp.size == (7200, 3933)
    assert comp.getpixel((3000, 100)) == (0, 0, 0)      # gap above the Dell
    assert comp.getpixel((3000, 2000))[0] > 200          # inside the Dell: red

    target2 = proj.sync(be, add=["c.png"])               # add pins c
    assert len(gnome.read_slideshow(target2)[0]) == 3
    assert not target.exists()                           # old slideshow cleaned up
    assert proj.sync(be) is None                         # nothing changed -> no reapply


def test_sync_follows_setup_filter(tmp_path):
    proj = make_project(tmp_path, names=("a", "b", "c", "d"))
    (tmp_path / "setup.yml").write_text(
        "configuration: desk\nmode: slideshow\nimages: all\nfilter: abstract\ninterval: 10m\ntransition: 0\n")
    save_tags(proj.tags_file, {"a.png": {"abstract"}, "b.png": {"abstract"}})
    be = fake_backend(os_layout(), [])

    def names():
        return [Path(o).name for o in proj.load_state()["order"]]

    proj.run(be, {"source": "setup"})
    assert names() == ["a.png", "b.png"]

    # newly tagged image joins on sync
    save_tags(proj.tags_file, {"a.png": {"abstract"}, "b.png": {"abstract"}, "c.png": {"abstract", "cg"}})
    proj.sync(be)
    assert names() == ["a.png", "b.png", "c.png"]

    # pinned image survives losing its tag
    proj.sync(be, add=["b.png"])
    save_tags(proj.tags_file, {"a.png": {"abstract"}, "c.png": {"abstract"}})
    proj.sync(be)
    assert names() == ["a.png", "c.png", "b.png"]

    # deleted pictures are dropped instead of breaking the slideshow
    (tmp_path / "pictures" / "a.png").unlink()
    proj.sync(be)
    assert names() == ["c.png", "b.png"]

    # editing setup.yml is picked up too
    (tmp_path / "setup.yml").write_text("configuration: desk\nmode: single\nimages: all\nfilter: '!abstract'\n")
    proj.sync(be)
    assert proj.load_state()["mode"] == "single" and names() == ["b.png"]
    proj.sync(be, add=["c.png"])        # adding to a single image makes a slideshow
    assert proj.load_state()["mode"] == "slideshow" and names() == ["b.png", "c.png"]


def test_sync_needs_an_applied_wallpaper(tmp_path):
    proj = make_project(tmp_path)
    with pytest.raises(ProjectError):
        proj.sync(fake_backend(os_layout(), []))


def test_project_filter(tmp_path):
    proj = make_project(tmp_path, names=("lake", "alps", "city"))
    save_tags(proj.tags_file, {"lake.png": {"landscape", "water"},
                               "alps.png": {"landscape", "water", "mountains"}})
    kept = proj.filter_jobs(proj.resolve_images("all"), "landscape + water + !mountains")
    assert [j.source.name for j in kept] == ["lake.png"]
    with pytest.raises(ProjectError):
        proj.filter_jobs(proj.resolve_images("all"), "snow")


def test_continuity_keeps_current_image():
    old = {"order": ["a", "b", "c"], "start": 1000.0, "interval_s": 60.0}
    now = 1000.0 + 60 * 3 * 5 + 60 + 25          # 5 full cycles, then 25 s into "b"
    start = _continue_start(old, ["new", "a", "b", "c"], 60.0, now)
    elapsed = now - start
    assert int(elapsed // 60) == 2 and elapsed % 60 == pytest.approx(25)   # still "b", same moment


def test_cache_follows_geometry_not_config_text(tmp_path):
    import copy

    import yaml
    from helpers import DESK

    proj = make_project(tmp_path, names=("a",))
    cfg_path = tmp_path / "configurations" / "desk.yml"
    job = proj.resolve_images(["a.png"])[0]
    first = proj.render(proj.load_config(None), job)
    assert not first.skipped
    stamps = {n: p.stat().st_mtime_ns for n, p in first.slices.items()}

    # a serial number can't change the picture: nothing is re-rendered
    desk = copy.deepcopy(DESK)
    desk["monitors"][1]["serial"] = "XYZ"
    cfg_path.write_text(yaml.safe_dump(desk))
    assert proj.render(proj.load_config(None), job).skipped

    # moving the centre screen inside the desk outline re-renders only that screen
    desk["monitors"][1]["v_pos"] = -25
    cfg_path.write_text(yaml.safe_dump(desk))
    r = proj.render(proj.load_config(None), job)
    assert not r.skipped
    changed = {n for n, p in r.slices.items() if p.stat().st_mtime_ns != stamps[n]}
    assert changed == {"centre"}


def test_render_many_keeps_order(tmp_path):
    proj = make_project(tmp_path, names=("a", "b", "c"))
    cfg = proj.load_config(None)
    jobs = proj.resolve_images("all")
    out = proj.render_many(cfg, jobs, then=lambda job, r: job.source.name)
    assert [name for _, name in out] == ["a.png", "b.png", "c.png"]
    assert all(r.mapping is None for r, _ in out)  # decoded sources released
