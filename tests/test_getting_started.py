"""The getting-started strip on a new, empty folder."""

import json
import os

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from panescape.project import Project  # noqa: E402


@pytest.fixture
def window(tmp_path, monkeypatch):
    from panescape.gui import common, layout_editor, main_window
    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("panescape-tests")
    app.setApplicationName("panescape-tests")
    for mod in (common, layout_editor, main_window):
        monkeypatch.setattr(mod, "error", lambda _p, _t, text: pytest.fail(f"error dialog: {text}"), raising=False)
    monkeypatch.setattr("panescape.backends.detect", lambda: None)
    for d in ("configurations", "pictures", "backgrounds"):
        (tmp_path / d).mkdir()
    (tmp_path / "setup.yml").write_text("configuration: my_desk\nmode: slideshow\n")
    w = main_window.MainWindow(Project(tmp_path))
    w.show()
    yield w
    w.close()


def labels(w):
    return [b.text() for b in w.guide.buttons.values()]


def test_steps_tick_off_and_the_guide_goes_away(window, monkeypatch):
    root = window.project.root
    assert window.guide.isVisible()
    assert labels(window) == ["1. Describe your monitors", "2. Add pictures", "3. Apply to the desktop"]
    assert window.guide.buttons["monitors"].font().bold()

    made = []
    monkeypatch.setattr(window.layout_editor, "new_config", lambda: made.append(True))
    window.guide.buttons["monitors"].click()
    assert made and window.tabs.currentWidget() is window.layout_editor

    (root / "configurations" / "my_desk.yml").write_text("monitors:\n  - {name: main, resolution: FHD, size: 24in}\n")
    from PIL import Image
    Image.new("RGB", (800, 400), "blue").save(root / "pictures" / "sea.png")
    window.guide.refresh()
    assert labels(window)[:2] == ["✓ Describe your monitors", "✓ Add pictures"]
    assert window.guide.buttons["apply"].font().bold()
    window.guide.buttons["apply"].click()
    assert window.tabs.currentWidget() is window.apply_tab

    window.project.state_path().write_text(json.dumps({"mode": "single"}))
    window.guide.refresh()
    assert not window.guide.isVisible()


def test_closing_the_guide_is_remembered(window):
    window.guide.dismiss()
    window.guide.refresh()
    assert not window.guide.isVisible()
    window.guide.bring_back()
    assert window.guide.isVisible()


def test_the_first_layout_gets_the_name_setup_yml_expects(window):
    from panescape.gui.layout_editor import NewConfigDialog
    dlg = NewConfigDialog(window, [], None, False, "my_desk")
    assert dlg.name.text() == "my_desk"
    assert NewConfigDialog(window, ["my_desk"], None, False, "my_desk").name.text() == ""


def test_friendly_messages_without_a_layout(window):
    assert "No layout yet" in window.apply_tab.summary.text()
    assert window.pictures_tab.desk.error.startswith("No layout yet")
