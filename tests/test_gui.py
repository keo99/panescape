"""The layout editor and setup bar, driven without a display (Qt offscreen)."""

import os

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from panescape.project import Project  # noqa: E402

CONFIG = """\
# my desk, measured with a tape
monitors:
  - name: left
    model: Samsung U28E590
    rotation: portrait-right
  - name: centre          # the Dell
    model: Dell P2425H
    gap: 2
    v_pos: -20
  - name: right
    model: Samsung U28E590
    rotation: portrait-left
    right_of: centre
"""


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    a.setOrganizationName("panescape-tests")
    a.setApplicationName("panescape-tests")
    return a


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from panescape.gui import common, layout_editor, main_window
    for mod in (common, layout_editor, main_window):
        monkeypatch.setattr(mod, "confirm", lambda *a, **k: True, raising=False)
        monkeypatch.setattr(mod, "error", lambda _p, _t, text: pytest.fail(f"error dialog: {text}"), raising=False)
    monkeypatch.setattr("panescape.backends.detect", lambda: None)
    (tmp_path / "configurations").mkdir()
    (tmp_path / "pictures").mkdir()
    (tmp_path / "configurations" / "desk.yml").write_text(CONFIG)
    (tmp_path / "configurations" / "laptop.yml").write_text(
        "monitors:\n  - {name: main, resolution: FHD, size: 14in}\n")
    (tmp_path / "setup.yml").write_text("# mine\nconfiguration: desk\nmode: single\n")
    w = main_window.MainWindow(Project(tmp_path))
    yield w
    w.layout_editor.dirty = False
    w.close()


def test_opens_the_setups_layout(window):
    ed = window.layout_editor
    assert ed.name == "desk"
    assert ed.names() == ["left", "centre", "right"]
    assert ed.cfg is not None and not ed.dirty
    assert window.setups.currentText() == "default"


def test_drag_changes_gap_and_v_pos_and_save_keeps_comments(window, tmp_path):
    ed = window.layout_editor
    ed.nudge(1, 1.5, -3.0)   # right 1.5 mm, up 3 mm
    e = ed.monitors()[1]
    assert e["gap"] == 3.5 and e["v_pos"] == -17
    assert ed.dirty and ed.form.v_pos.value() == -17
    ed.nudge(2, 0, 2)        # right screen 2 mm down
    assert ed.monitors()[2]["v_pos"] == -2
    assert ed.save()
    text = (tmp_path / "configurations" / "desk.yml").read_text()
    assert "# my desk, measured with a tape" in text and "# the Dell" in text
    assert "gap: 3.5" in text and "v_pos: -17" in text


def test_anchor_does_not_move(window):
    ed = window.layout_editor
    ed.nudge(0, 10, 10)
    assert not ed.dirty


def test_placement_mode_switch(window):
    ed = window.layout_editor
    ed.list.setCurrentRow(2)
    ed.form.mode.setCurrentIndex(ed.form.mode.findData("below"))
    e = ed.monitors()[2]
    assert e.get("below") == "left" or e.get("below") == "centre"
    assert "right_of" not in e and ed.cfg is not None
    ed.nudge(2, 4, 1)
    assert e["h_pos"] == 4 and e["gap"] == 1


def test_rename_updates_references(window):
    ed = window.layout_editor
    ed.list.setCurrentRow(1)
    ed.rename_monitor("middle")
    assert ed.names() == ["left", "middle", "right"]
    assert ed.monitors()[2]["right_of"] == "middle"
    assert ed.cfg is not None


def test_add_and_remove(window):
    ed = window.layout_editor
    ed.list.setCurrentRow(2)
    ed.add_monitor()
    assert ed.names()[-1] == "monitor4" and ed.monitors()[3]["right_of"] == "right"
    ed.remove_monitor()
    assert ed.names() == ["left", "centre", "right"]
    # removing the middle one re-attaches the right one
    ed.list.setCurrentRow(1)
    ed.remove_monitor()
    assert ed.names() == ["left", "right"]
    assert ed.cfg is not None


def test_move_up_makes_new_anchor(window):
    ed = window.layout_editor
    ed.list.setCurrentRow(1)
    ed.move_monitor(-1)
    assert ed.names()[0] == "centre"
    assert not any(k in ed.monitors()[0] for k in ("right_of", "left_of", "below", "above"))
    assert ed.cfg is not None


def test_bad_value_shows_problem_not_crash(window):
    ed = window.layout_editor
    ed.list.setCurrentRow(0)
    ed.apply_edit({"model": None}, True)   # no model -> no size/resolution
    assert ed.cfg is None and "needs" in ed.problems.text()
    ed.apply_edit({"resolution": "3840x2160", "size": "28in"}, True)
    assert ed.cfg is not None


def test_choosing_a_layout_updates_the_setup(window, tmp_path):
    ed = window.layout_editor
    ed.choose("laptop")
    assert ed.name == "laptop"
    assert Project(tmp_path).load_setup()["configuration"] == "laptop"
    assert "# mine" in (tmp_path / "setup.yml").read_text()


def test_setups_bar(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("evening", True))
    window.new_setup()
    p = Project(tmp_path)
    assert p.setup_names() == ["default", "evening"] and p.active_setup() == "evening"
    window.layout_editor.choose("laptop")
    assert p.load_setup("evening")["configuration"] == "laptop"
    assert p.load_setup("default")["configuration"] == "desk"
    window.switch_setup("default")
    assert window.layout_editor.name == "desk"
    window.delete_setup()
    assert p.setup_names() == ["evening"] or p.setup_names() == ["default"]


def test_new_layout_copy(window, tmp_path, monkeypatch):
    from panescape.gui import layout_editor as le

    class FakeDialog:
        Accepted = 1

        def __init__(self, *a, **k):
            from types import SimpleNamespace
            self.name = SimpleNamespace(text=lambda: "desk2")
            self.source = "copy"

        def exec(self):
            return 1
    monkeypatch.setattr(le, "NewConfigDialog", FakeDialog)
    monkeypatch.setattr(le.QDialog, "Accepted", 1)
    window.layout_editor.new_config()
    assert (tmp_path / "configurations" / "desk2.yml").read_text() == CONFIG
    assert Project(tmp_path).load_setup()["configuration"] == "desk2"


def _housings(ed):
    """Each housing's place relative to the first monitor, to 0.1 mm (offsets are stored to 0.1 mm)."""
    first = ed.layout_.placed[0].outer
    return {p.monitor.name: (pytest.approx(p.outer.x - first.x, abs=0.06), pytest.approx(p.outer.y - first.y, abs=0.06))
            for p in ed.layout_.placed}


def test_measured_from_names_the_real_default_and_changes_keep_monitors_in_place(window):
    ed = window.layout_editor
    f = ed.form
    ed.list.setCurrentRow(1)  # centre: in a plain row, so measured from the first monitor
    assert f.v_ref.itemText(0) == "left (the first monitor)"
    ed.list.setCurrentRow(2)  # right: right_of centre
    assert f.v_ref.itemText(0) == "centre (the one it's next to)"

    before = {k: (x.expected, y.expected) for k, (x, y) in _housings(ed).items()}
    f.v_ref.setCurrentIndex(f.v_ref.findData("left"))  # measure right from left instead
    assert _housings(ed) == before
    right = ed.monitors()[2]
    assert right["v_ref"] == "left" and right["v_pos"] == -20  # centre is 20 mm lower than left, and so is right
    assert f.v_pos.value() == -20

    ed.list.setCurrentRow(1)
    f.v_ref.setCurrentIndex(f.v_ref.findData("left"))  # the same as the default: nothing moves
    assert _housings(ed) == before and ed.monitors()[1]["v_pos"] == -20

    f.v_align.setCurrentIndex(f.v_align.findData("top"))  # centre vs the (taller) left: line up the tops
    assert _housings(ed) == before
    centre = ed.monitors()[1]
    assert centre["v_align"] == "top" and f.v_pos.value() == centre["v_pos"] < -20  # its top is further down


def test_angled_screens_switch_writes_the_viewer_and_angles(window):
    ed = window.layout_editor
    assert not ed.seat.on.isChecked() and not ed.form.angle_box.isVisibleTo(ed.form)
    ed.seat.on.setChecked(True)
    assert ed.doc["viewer"] == {"distance": 600}
    assert list(ed.doc).index("viewer") < list(ed.doc).index("monitors")
    assert ed.form.angle_box.isVisibleTo(ed.form)
    ed.list.setCurrentRow(2)
    ed.form.angle.setValue(25)
    assert ed.monitors()[2]["angle"] == 25
    assert ed.layout_.placed[2].warp is not None
    ed.seat.height.setValue(-40)
    assert ed.doc["viewer"] == {"distance": 600, "height": -40}
    ed.list.setCurrentRow(0)
    assert not ed.form.angle_row.isVisibleTo(ed.form)  # the anchor has no angle of its own
    ed.seat.on.setChecked(False)
    assert "viewer" not in ed.doc and all(p.warp is None for p in ed.layout_.placed)
    assert ed.monitors()[2]["angle"] == 25  # kept for when it's switched back on


def test_photo_dialog_measures_and_fills_in_the_layout(window, tmp_path):
    pytest.importorskip("numpy")
    import copy

    from PySide6.QtCore import QThreadPool
    from test_photocal import fake_photo

    from panescape import yamledit
    from panescape.gui.photo_dialog import PhotoDialog
    ed = window.layout_editor
    truth = copy.deepcopy(yamledit.plain(ed.doc))
    truth["viewer"] = {"distance": 650, "offset": 600, "height": -60}  # the first monitor is on the left
    truth["monitors"][2].update(angle=25, gap=4)
    path = tmp_path / "photo.png"
    fake_photo(truth, rotvec=(0.02, 0.01, 0), f=700).save(path)

    d = PhotoDialog(ed)
    d.add([str(path)])
    for _ in range(3):
        QThreadPool.globalInstance().waitForDone(20000)
        QApplication.processEvents()
    assert d.use_btn.isEnabled(), d.result.text()
    assert "turned towards you" in d.result.text()
    d.use()
    right = ed.monitors()[2]
    assert right["angle"] == pytest.approx(25, abs=0.5) and right["gap"] == pytest.approx(4, abs=0.5)
    assert ed.doc["viewer"]["distance"] == pytest.approx(650, abs=10)
    assert ed.dirty and ed.layout_.placed[2].warp is not None


def test_photo_dialog_gets_out_of_the_way_while_you_take_the_photo(window):
    from PySide6.QtCore import Qt

    from panescape.gui.photo_dialog import PhotoDialog
    ed = window.layout_editor
    d = PhotoDialog(ed)
    d.show()
    assert not d.isModal()
    window.show()
    d._pattern_up("pattern applied")          # what happens once the pattern is on the screens
    QApplication.processEvents()
    assert not d.isVisible() and window.isMinimized()
    window.setWindowState(Qt.WindowNoState)   # you bring panescape back from the dock
    QApplication.processEvents()
    assert d.isVisible() and not d.waiting
    d.close()
