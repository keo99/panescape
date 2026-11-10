"""The Apply tab: editing the setup, applying, and what it says about the desktop."""

import os
import types

import pytest
from helpers import make_project, os_layout

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def per_output_backend(shown):
    from panescape.backends import base
    be = types.ModuleType("fakeoutputs")
    be.KIND = "per_output"
    be.get_layout = os_layout
    be.match_monitors = base.match_monitors
    be.native_size = base.native_size
    be.set_outputs = lambda frame, **_k: shown.append(dict(frame))
    return be


@pytest.fixture
def tab(tmp_path, monkeypatch):
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from panescape.gui import apply_tab, common
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(apply_tab, "error", lambda _p, _t, text: pytest.fail(f"error dialog: {text}"))
    monkeypatch.setattr(common, "error", lambda *_a: None)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    proj = make_project(tmp_path)
    (tmp_path / "setup.yml").write_text("# my setup\nconfiguration: desk   # the desk\nmode: slideshow\n"
                                        "interval: 15m\ntransition: 2s\nfilter: ''\n")
    (tmp_path / "pictures" / "tags.yml").write_text("a.png: [sea]\nb.png: [sea, night]\n")
    shown: list = []
    be = per_output_backend(shown)
    # the test runner must not start a real runner process
    monkeypatch.setattr(proj, "ensure_slideshow_runner", lambda: None)
    t = apply_tab.ApplyTab(proj, lambda: be, lambda: True)
    t.shown = shown

    def settle():
        for _ in range(3):
            QThreadPool.globalInstance().waitForDone(10000)
            app.processEvents()
    t.settle = settle
    settle()
    return t


def test_form_shows_and_writes_the_setup(tab, tmp_path):
    assert tab.title.text() == "Setup “default”"
    assert tab.config.currentText() == "desk" and tab.slideshow.isChecked()
    assert tab.interval.seconds() == 900 and tab.transition.value() == 2
    assert "3 pictures" in tab.summary.text() and tab.strip.count() == 3

    tab.interval.num.setValue(2)
    tab.interval.unit.setCurrentIndex(2)  # hours
    tab.shuffle.setChecked(True)
    tab.filter.setText("sea + !night")
    tab._filter_done()
    text = (tmp_path / "setup.yml").read_text()
    assert "# my setup" in text and "# the desk" in text  # comments kept
    s = tab.project.load_setup()
    assert s["interval"] == "2h" and s["shuffle"] is True and s["filter"] == "sea + !night"
    assert "<b>a.png</b>" in tab.summary.text() and tab.strip.count() == 1

    tab.single.setChecked(True)
    assert tab.project.load_setup()["mode"] == "single" and not tab.interval.isEnabled()


def test_bad_filter_is_not_written(tab):
    tab.filter.setText("sea + (")
    tab._filter_done()
    assert tab.project.load_setup()["filter"] == ""
    assert "color" in tab.filter_note.text()


def test_choosing_pictures(tab, monkeypatch):
    from panescape.gui import apply_tab
    monkeypatch.setattr(apply_tab.PickPictures, "exec", lambda self: apply_tab.QDialog.Accepted)
    monkeypatch.setattr(apply_tab.PickPictures, "chosen", lambda self: ["c.png", "a*"])
    tab.some_pics.setChecked(True)
    assert tab.project.load_setup()["images"] == ["c.png", "a*"]
    assert "2 pictures" in tab.summary.text()
    tab.entries.item(0).setSelected(True)
    tab.remove_entries()
    assert tab.project.load_setup()["images"] == ["a*"]
    tab.entries.item(0).setSelected(True)
    tab.remove_entries()  # empty list: back to all
    assert tab.project.load_setup()["images"] == "all" and tab.all_pics.isChecked()


def test_apply_and_pending_changes(tab):
    assert "Nothing has been applied" in tab.status.text()
    tab.apply()
    tab.settle()
    assert len(tab.shown) == 1
    assert "Slideshow of <b>3</b>" in tab.status.text() and "setup “default”" in tab.status.text()
    assert not tab.pending.isVisibleTo(tab)
    assert "Stuck" in tab.countdown.text()  # no runner in the tests
    assert "wallpaper set" in tab.log.toPlainText()

    tab.interval.num.setValue(5)
    assert tab.pending.isVisibleTo(tab) and "changed since" in tab.pending.text()
    tab.apply()
    tab.settle()
    assert not tab.pending.isVisibleTo(tab)


def test_runner_and_autostart(tab, tmp_path):
    from panescape.project import autostart_file
    tab.refresh_runner()
    assert "only needed while a slideshow" in tab.runner_text.text()
    assert not tab.start_runner.isEnabled()
    tab.apply()
    tab.settle()
    tab.refresh_runner()
    assert "Not running" in tab.runner_text.text() and tab.start_runner.isEnabled()
    tab.autostart.setChecked(True)
    assert autostart_file().exists() and str(tmp_path / "config") in str(autostart_file())
    tab.autostart.setChecked(False)
    assert not autostart_file().exists()


def test_setter_is_shared_and_keeps_the_simple_form(tab, tmp_path):
    tab.project.update_shared({"setter": "swaybg"})
    text = (tmp_path / "setup.yml").read_text()
    assert "setter: swaybg" in text and "setups:" not in text
    tab.project.create_setup("evening")
    tab.project.update_shared({"setter": "swww"})
    doc = tab.project._setup_doc()
    assert doc["setter"] == "swww" and "setter" not in doc["setups"]["default"]
    assert tab.project.load_setup("evening")["setter"] == "swww"


def test_durations():
    from panescape.gui.apply_tab import DurationEdit, clock, nice_duration
    d = DurationEdit()
    d.set_value("1h30m")
    assert d.value() == "90m"
    d.set_value(45)
    assert d.value() == "45s"
    assert nice_duration(5400) == "1 h 30 min" and clock(75) == "1:15"
