"""The menu bar: tab shortcuts, the app-menu entry, opening another folder."""

import os

import pytest
from helpers import make_project

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture
def window(tmp_path, monkeypatch):
    from panescape.gui import common, layout_editor, main_window
    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("panescape-tests")
    app.setApplicationName("panescape-tests")
    for mod in (common, layout_editor, main_window):
        monkeypatch.setattr(mod, "error", lambda _p, _t, text: pytest.fail(f"error dialog: {text}"), raising=False)
    monkeypatch.setattr(main_window, "confirm", lambda *a, **k: True)
    monkeypatch.setattr("panescape.backends.detect", lambda: None)
    (tmp_path / "one").mkdir()
    w = main_window.MainWindow(make_project(tmp_path / "one"))
    yield w
    for other in main_window._windows:
        other.close()
    main_window._windows.clear()
    w.close()


def actions(w, menu: str) -> dict:
    return {a.text().replace("&", ""): a for a in w.menus[menu].actions() if a.text()}


def test_tab_shortcuts(window):
    view = actions(window, "View")
    assert [a.shortcut().toString() for a in view.values()] == ["Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4"]
    view["Unsplash"].trigger()
    assert window.tabs.currentWidget() is window.unsplash_tab
    assert actions(window, "File")["Quit"].shortcut().toString() == "Ctrl+Q"


def test_app_menu_entry_toggle(window):
    from panescape import launcher
    act = actions(window, "Help")["Show panescape in the app menu"]
    act.setChecked(True)
    assert launcher.installed()
    act.setChecked(False)
    assert not launcher.installed()


def test_open_another_folder(window, tmp_path, monkeypatch):
    from panescape.gui import main_window
    other = tmp_path / "two"
    other.mkdir()
    monkeypatch.setattr(main_window.QFileDialog, "getExistingDirectory", lambda *a, **k: str(other))
    window.show()
    window.open_folder()
    assert len(main_window._windows) == 1
    new = main_window._windows[0]
    assert new.project.root == other.resolve()
    assert (other / "setup.yml").exists() and (other / "pictures").is_dir()
    assert not window.isVisible() and new.isVisible()
