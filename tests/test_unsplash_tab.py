"""The Unsplash tab with a fake client (no network, no real key)."""

import os

import pytest
from helpers import make_project
from test_unsplash import KEY, jpeg_bytes, photo

from panescape import unsplash

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class FakeClient:
    reject = False

    def __init__(self, key):
        self.key = unsplash.check_key_format(key)
        self.remaining = None
        self.searches = []

    def check(self):
        if FakeClient.reject:
            raise unsplash.KeyRejected("Unsplash didn't accept the access key (401)")
        self.remaining = 49
        return 49

    def search(self, q, page=1, orientation="landscape", **_k):
        self.searches.append((q, page, orientation))
        self.remaining = 48
        res = [photo(f"{q[:4]:_<4}{page}{i:06d}", name=f"Pho Tographer{i}", tags=("lake",)) for i in range(3)]
        return {"total": 6, "total_pages": 2, "results": res}

    def download(self, ph, dest):
        full = dict(ph, tags=[{"title": "lake"}, {"title": "HD wallpapers"}, {"title": "dawn"}])
        path = dest / unsplash.file_name(full)
        path.write_bytes(jpeg_bytes(400, 200))
        return path, full


@pytest.fixture
def tab(tmp_path, monkeypatch):
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from panescape.gui import common, unsplash_tab
    app = QApplication.instance() or QApplication([])
    errors = []
    monkeypatch.setattr(unsplash_tab, "error", lambda _p, _t, text: errors.append(text))
    monkeypatch.setattr(unsplash_tab, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(common, "error", lambda *_a: None)
    monkeypatch.setattr(unsplash, "fetch_image", lambda url, **_k: jpeg_bytes(120, 80))
    FakeClient.reject = False
    proj = make_project(tmp_path, names=("a",))
    t = unsplash_tab.UnsplashTab(proj, client_factory=FakeClient)
    t.errors = errors

    def settle():
        for _ in range(3):
            QThreadPool.globalInstance().waitForDone(5000)
            app.processEvents()
    t.settle = settle
    return t


def all_text(widget) -> str:
    from PySide6.QtWidgets import QLabel, QLineEdit
    widgets = widget.findChildren(QLabel) + widget.findChildren(QLineEdit)
    return " ".join(w.text() for w in widgets)


def test_key_is_checked_stored_privately_and_never_shown(tab):
    assert tab.key_box.isVisibleTo(tab) and not tab.main.isEnabled()
    tab.key_edit.setText("not a key")
    tab.save_key()
    assert tab.errors and unsplash.load_key() == (None, None)
    assert tab.key_edit.text() == ""

    tab.key_edit.setText(KEY)
    tab.save_key()
    tab.settle()
    assert unsplash.load_key() == (KEY, "file")
    assert not tab.key_box.isVisibleTo(tab) and tab.main.isEnabled()
    assert "only you can read it" in tab.key_status.text() and "49 requests" in tab.key_status.text()
    assert KEY not in all_text(tab)
    for f in tab.project.root.rglob("*"):
        if f.is_file() and f.suffix in (".yml", ".json", ".txt"):
            assert KEY not in f.read_text()


def test_a_rejected_key_is_not_stored(tab):
    FakeClient.reject = True
    tab.key_edit.setText(KEY)
    tab.save_key()
    tab.settle()
    assert unsplash.load_key() == (None, None)
    assert tab.errors and "not saved" in tab.errors[-1]


def test_search_select_download(tab):
    unsplash.save_key(KEY)
    tab.load_key()
    downloaded = []
    tab.downloaded.connect(downloaded.append)
    tab.query.setText("lake")
    tab.search()
    tab.settle()
    assert tab.grid.count() == 3 and "6 photos" in tab.info.text()
    assert tab.more.isVisibleTo(tab)
    assert not tab.grid.item(0).icon().isNull()
    tab.load_more()
    tab.settle()
    assert tab.grid.count() == 6 and not tab.more.isVisibleTo(tab)

    tab.grid.setCurrentRow(0)
    tab.settle()
    assert "utm_source=panescape" in tab.credit.text() and "Unsplash" in tab.credit.text()
    assert "6000×3000 px: enlarged 1.3×" in tab.quality.text()  # the 4K panels need more
    tab.download()
    tab.settle()
    assert len(downloaded) == 1
    name = os.path.basename(downloaded[0])
    assert name.endswith("-unsplash.jpg") and (tab.project.pictures / name).exists()
    assert "lake, dawn" in tab.after.text()
    assert tab.grid.item(0).text().startswith("✓")
    from panescape.gui.unsplash_tab import credit_html
    assert "Pho Tographer0" in credit_html(tab.project, name)


def test_suggest_uses_tags(tab):
    unsplash.save_key(KEY)
    tab.load_key()
    (tab.project.pictures / "tags.yml").write_text("a.png: [harbour]\n")
    tab.suggest()
    tab.settle()
    assert "harbour" in tab.info.text() and tab.grid.count() == 3
    assert tab.client.searches[0][0] == "harbour"


def test_remove_key(tab):
    unsplash.save_key(KEY)
    tab.load_key()
    tab.forget_key()
    assert unsplash.load_key() == (None, None) and tab.key_box.isVisibleTo(tab)
