"""Tag editing, per-picture framing and importing (project level and the Pictures tab)."""

import os

import pytest
from PIL import Image

from panescape.project import ImageJob, Project
from panescape.tags import delete_tag, edit_tags, load_tags, rename_tag, tags_for

DESK = "monitors:\n  - {name: a, resolution: 1920x1080, size: 24in}\n  - {name: b, resolution: 1920x1080, size: 24in}\n"


def make(tmp_path, n=3):
    (tmp_path / "configurations").mkdir()
    (tmp_path / "configurations" / "desk.yml").write_text(DESK)
    (tmp_path / "setup.yml").write_text("configuration: desk\nmode: single\n")
    pics = tmp_path / "pictures"
    pics.mkdir()
    for i in range(n):
        Image.new("RGB", (800, 300), (40 * i, 80, 120)).save(pics / f"p{i}.jpg")
    return Project(tmp_path)


def test_tag_edits_keep_comments_and_respect_globs(tmp_path):
    p = tmp_path / "tags.yml"
    p.write_text("# mine\na.jpg: [x]   # keep\n\"city-*\": [urban]\n")
    warn = edit_tags(p, {"a.jpg": ({"y"}, set()), "city-1.jpg": (set(), {"urban"})})
    assert warn and "city-*" in warn[0]
    text = p.read_text()
    assert "# mine" in text and "# keep" in text and "a.jpg: [x, y]" in text
    assert rename_tag(p, "x", "y") == 1                 # merge
    assert load_tags(p)["a.jpg"] == {"y"}
    delete_tag(p, "y")
    assert "a.jpg" not in load_tags(p)
    assert tags_for("city-9.jpg", load_tags(p)) == {"urban"}


def test_picture_crop_precedence(tmp_path):
    proj = make(tmp_path)
    cfg = proj.load_config(None)
    job = ImageJob(proj.pictures / "p0.jpg")
    assert proj.crop_for(cfg, job).anchor == (0.5, 0.5)
    proj.set_picture_crop("p0.jpg", {"anchor": [0.2, 0.8], "zoom": 1.5})
    assert "# How each picture is framed" in proj.crops_file.read_text()
    c = proj.crop_for(cfg, job)
    assert c.anchor == (0.2, 0.8) and c.zoom == 1.5
    # a setup.yml image entry still wins
    assert proj.crop_for(cfg, ImageJob(job.source, {"zoom": 2})).zoom == 2
    proj.set_picture_crop("p0.jpg", None)
    assert proj.crop_for(cfg, job).zoom == 1.0


def test_crop_changes_the_render(tmp_path):
    proj = make(tmp_path)
    cfg = proj.load_config(None)
    job = ImageJob(proj.pictures / "p0.jpg")
    first = proj.render(cfg, job, preview=False)
    proj.set_picture_crop("p0.jpg", {"zoom": 1.4})
    again = proj.render(cfg, job, preview=False)
    assert again.key != first.key and not again.skipped


def test_import_never_overwrites(tmp_path):
    proj = make(tmp_path, n=1)
    src = tmp_path / "elsewhere"
    src.mkdir()
    Image.new("RGB", (10, 10)).save(src / "p0.jpg")
    (src / "notes.txt").write_text("x")
    new = proj.import_pictures([src / "p0.jpg", src / "notes.txt"])
    assert [p.name for p in new] == ["p0-2.jpg"]


# --------------------------------------------------------------------------- the tab

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def tab(tmp_path, monkeypatch):
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from panescape.gui import common, pictures_tab
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(pictures_tab, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(pictures_tab, "error", lambda _p, _t, text: pytest.fail(f"error dialog: {text}"))
    monkeypatch.setattr(common, "error", lambda *_a: None)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    proj = make(tmp_path)
    t = pictures_tab.PicturesTab(proj, lambda: None)

    def settle():
        QThreadPool.globalInstance().waitForDone(5000)
        app.processEvents()
    t.settle = settle
    settle()
    return t


def select(tab, *names):
    tab.grid.clearSelection()
    for n in names:
        tab.items[n].setSelected(True)


def test_grid_and_thumbnails(tab):
    assert sorted(tab.items) == ["p0.jpg", "p1.jpg", "p2.jpg"]
    assert tab.sizes["p1.jpg"] == (800, 300)
    assert not tab.items["p1.jpg"].icon().isNull()


def test_tagging_several_and_chips(tab):
    select(tab, "p0.jpg", "p1.jpg")
    tab.add_tag.setText("Sunset, water")
    tab._add_typed_tags()
    tags = load_tags(tab.project.tags_file)
    assert tags["p0.jpg"] == tags["p1.jpg"] == {"sunset", "water"}
    select(tab, "p1.jpg")
    tab._change_tags(set(), {"water"})
    select(tab, "p0.jpg", "p1.jpg")
    chips = {tab.chips.itemAt(i).widget().text() for i in range(tab.chips.count())}
    assert chips == {"sunset  ✕", "water  1/2"}


def test_filter_hides_and_sets_the_setup(tab):
    edit_tags(tab.project.tags_file, {"p0.jpg": ({"sea"}, set()), "p1.jpg": ({"sea", "night"}, set())})
    tab.refresh()
    tab.filter.setText("sea + !night")
    tab.apply_filter()
    hidden = {n for n, it in tab.items.items() if it.isHidden()}
    assert hidden == {"p1.jpg", "p2.jpg"} and "1 of 3" in tab.count.text()
    tab.filter.setText("!*")       # untagged
    tab.apply_filter()
    assert {n for n, it in tab.items.items() if not it.isHidden()} == {"p2.jpg"}
    tab.filter.setText("sea +")    # incomplete: shows the problem, keeps the grid
    tab.apply_filter()
    assert "ends unexpectedly" in tab.count.text()
    tab.filter.setText("sea")
    tab.use_filter_for_setup()
    assert tab.project.load_setup()["filter"] == "sea"


def test_rename_and_delete_tag(tab, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    edit_tags(tab.project.tags_file, {"p0.jpg": ({"sea"}, set()), "p1.jpg": ({"ocean"}, set())})
    tab.refresh()
    row = next(i for i in range(tab.tag_list.count()) if tab.tag_list.item(i).data(256) == "ocean")
    tab.tag_list.setCurrentRow(row)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("sea", True))
    tab.rename_tag()
    assert tab.all_tags() == {"sea": 2}
    row = next(i for i in range(tab.tag_list.count()) if tab.tag_list.item(i).data(256) == "sea")
    tab.tag_list.setCurrentRow(row)
    tab.delete_tag()
    assert tab.all_tags() == {}


def test_framing_drag_and_reset(tab):
    select(tab, "p0.jpg")
    tab.settle()
    assert tab.desk.image is not None and tab.frame_box.isEnabled()
    # the picture is narrower than the desk (in proportion): it fills the width,
    # so only vertical moves change anything
    tab.desk._pan(-1000, 0)
    assert tab.desk.crop.anchor == (0.5, 0.5)
    tab.desk._pan(0, -1000)            # drag far up: shows the bottom of the picture
    assert tab.desk.crop.anchor == (0.5, 1.0)
    tab._save_crop()
    assert tab.project.picture_crops()["p0.jpg"]["anchor"] == [0.5, 1.0]
    tab._reset_crop()
    tab._save_crop()
    assert "p0.jpg" not in tab.project.picture_crops()


def test_import_and_trash(tab, tmp_path):
    src = tmp_path / "new.png"
    Image.new("RGB", (64, 64)).save(src)
    tab.import_files([str(src)])
    assert "new.png" in tab.items and tab.selected_names() == ["new.png"]
    tab._change_tags({"fresh"}, set())
    tab.trash_selected()
    assert "new.png" not in tab.items
    assert "new.png" not in load_tags(tab.project.tags_file)
