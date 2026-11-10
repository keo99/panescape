"""Unsplash tab: search, suggestions from your tags, download into pictures/.

The access key is handled by panescape.unsplash (system keyring or a 0600
file, sent only to api.unsplash.com). This tab never shows it again after it
has been saved, and never writes it anywhere itself.
"""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .. import unsplash
from ..project import Project, ProjectError
from .common import confirm, error, run_async

ICON = QSize(200, 134)
PREVIEW = QSize(440, 300)
RED = "#d04030"
ORIENTATIONS = (("Landscape", "landscape"), ("Any shape", None), ("Portrait", "portrait"), ("Square", "squarish"))


def where_text(where: str | None) -> str:
    home = Path.home()
    f = unsplash.key_file()
    shown = f"~/{f.relative_to(home)}" if f.is_relative_to(home) else str(f)
    return {"keyring": "in your system keyring", "file": f"in {shown} (only you can read it)",
            "environment": f"from ${unsplash.KEY_ENV}"}.get(where or "", "")


def _esc(text) -> str:
    return html.escape(str(text or ""))


class UnsplashTab(QWidget):
    message = Signal(str)
    downloaded = Signal(str)  # path of a new picture

    def __init__(self, project: Project, client_factory=None, parent=None):
        super().__init__(parent)
        self.project = project
        self._factory = client_factory or (lambda key: unsplash.Client(key))
        self.client: unsplash.Client | None = None
        self.key_where: str | None = None
        self.photos: dict[str, dict] = {}
        self._pixmaps: dict[str, QPixmap] = {}
        self._query: tuple | None = None   # (query, orientation) of the shown results
        self._page = 1
        self._pages = 1
        self._gen = 0
        self._busy = False
        self._layout = None

        outer = QVBoxLayout(self)

        # ---------------------------------------------------------- the key
        self.key_box = QGroupBox("Unsplash access key")
        kl = QVBoxLayout(self.key_box)
        intro = QLabel(
            "To search Unsplash, panescape needs a free access key of your own:<br>"
            f"1. Sign in at <a href='{unsplash.NEW_APP_URL}'>unsplash.com/oauth/applications</a> and create a "
            "“New Application” (any name, e.g. <i>my panescape</i>).<br>"
            "2. Copy its <b>Access Key</b> (not the Secret key) and paste it here.<br>"
            "The key is kept in your system keyring (or a file only you can read), is sent only to "
            "api.unsplash.com, and is never written into your project folder. A new key allows 50 requests "
            "an hour; pictures themselves don't count.")
        intro.setWordWrap(True)
        intro.setOpenExternalLinks(True)
        intro.setTextFormat(Qt.RichText)
        kl.addWidget(intro)
        krow = QHBoxLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("paste the Access Key")
        self.key_edit.setClearButtonEnabled(True)
        self.key_edit.returnPressed.connect(self.save_key)
        self.key_save = QPushButton("Save key")
        self.key_save.clicked.connect(self.save_key)
        self.key_cancel = QPushButton("Cancel")
        self.key_cancel.clicked.connect(lambda: self._show_key_box(False))
        krow.addWidget(self.key_edit, 1)
        krow.addWidget(self.key_save)
        krow.addWidget(self.key_cancel)
        kl.addLayout(krow)
        outer.addWidget(self.key_box)

        srow = QHBoxLayout()
        self.key_status = QLabel()
        self.key_status.setStyleSheet("color: palette(placeholder-text)")
        srow.addWidget(self.key_status, 1)
        self.change_key = QPushButton("Change key…")
        self.change_key.clicked.connect(lambda: self._show_key_box(True))
        self.remove_key = QPushButton("Remove key")
        self.remove_key.clicked.connect(self.forget_key)
        srow.addWidget(self.change_key)
        srow.addWidget(self.remove_key)
        self.key_row = QWidget()
        self.key_row.setLayout(srow)
        srow.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.key_row)

        # ---------------------------------------------------------- search
        self.main = QWidget()
        ml = QVBoxLayout(self.main)
        ml.setContentsMargins(0, 0, 0, 0)
        qrow = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("search Unsplash, e.g. alpine lake")
        self.query.returnPressed.connect(self.search)
        self.orientation = QComboBox()
        for label, value in ORIENTATIONS:
            self.orientation.addItem(label, value)
        self.orientation.setToolTip("Landscape pictures suit a wide desk best")
        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self.search)
        self.suggest_btn = QPushButton("Suggest for me")
        self.suggest_btn.setToolTip("Photos like the ones you have, from your most used tags")
        self.suggest_btn.clicked.connect(self.suggest)
        qrow.addWidget(self.query, 1)
        qrow.addWidget(self.orientation)
        qrow.addWidget(self.search_btn)
        qrow.addWidget(self.suggest_btn)
        ml.addLayout(qrow)
        self.info = QLabel()
        self.info.setWordWrap(True)
        ml.addWidget(self.info)

        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.IconMode)
        self.grid.setIconSize(ICON)
        self.grid.setGridSize(QSize(ICON.width() + 16, ICON.height() + 40))
        self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setMovement(QListWidget.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setSelectionMode(QAbstractItemView.SingleSelection)
        self.grid.currentItemChanged.connect(lambda cur, _prev: self.show_photo(cur))
        self.grid.itemDoubleClicked.connect(lambda _it: self.download())
        ll.addWidget(self.grid, 1)
        self.more = QPushButton("More results")
        self.more.clicked.connect(self.load_more)
        self.more.setVisible(False)
        ll.addWidget(self.more)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        self.preview = QLabel()
        self.preview.setFixedSize(PREVIEW)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("background: palette(alternate-base); border-radius: 4px")
        rl.addWidget(self.preview, 0, Qt.AlignHCenter)
        self.credit = QLabel()
        self.credit.setOpenExternalLinks(True)
        self.credit.setTextFormat(Qt.RichText)
        self.credit.setWordWrap(True)
        rl.addWidget(self.credit)
        self.about = QLabel()
        self.about.setWordWrap(True)
        rl.addWidget(self.about)
        self.quality = QLabel()
        self.quality.setWordWrap(True)
        rl.addWidget(self.quality)
        self.download_btn = QPushButton("Download to my pictures")
        self.download_btn.setMinimumHeight(32)
        self.download_btn.clicked.connect(self.download)
        rl.addWidget(self.download_btn)
        self.fetch_tags = QCheckBox("Add Unsplash's tags (1 extra request)")
        self.fetch_tags.setToolTip(
            "Search results don't include Unsplash's tags, so getting them costs one more request per "
            "download.\nUnticked, the picture is tagged from your search words and the photo's description "
            "instead.\nEither way a download uses one request for the report Unsplash requires; the image "
            "file itself is free.")
        self.fetch_tags.setChecked(_setting_bool("unsplash/fetch_tags", True))
        self.fetch_tags.toggled.connect(lambda on: QSettings().setValue("unsplash/fetch_tags", on))
        rl.addWidget(self.fetch_tags)
        self.after = QLabel()
        self.after.setWordWrap(True)
        rl.addWidget(self.after)
        rl.addStretch(1)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        ml.addWidget(split, 1)
        outer.addWidget(self.main, 1)

        self.load_key()
        self.show_photo(None)

    # ------------------------------------------------------------------ key
    def load_key(self) -> None:
        try:
            key, where = unsplash.load_key()
            self.client = self._factory(key) if key else None
        except unsplash.UnsplashError as e:
            self.client, where = None, None
            self.message.emit(f"Unsplash key not usable: {e}")
        self.key_where = where if self.client else None
        self._show_key_box(self.client is None)
        self._update_key_status()

    def _show_key_box(self, show: bool) -> None:
        if not show and self.client is None:
            return
        self.key_box.setVisible(show)
        self.key_cancel.setVisible(self.client is not None)
        self.key_row.setVisible(self.client is not None and not show)
        self.main.setEnabled(self.client is not None)
        self.key_edit.clear()
        if show:
            self.key_edit.setFocus()

    def _update_key_status(self) -> None:
        if self.client is None:
            return
        left = self.client.remaining
        self.key_status.setText(f"Unsplash key stored {where_text(self.key_where)}"
                                + (f"  ·  {left} requests left this hour" if left is not None else ""))
        self.remove_key.setEnabled(self.key_where != "environment")
        self.change_key.setEnabled(self.key_where != "environment")

    def save_key(self) -> None:
        text = self.key_edit.text()
        self.key_edit.clear()  # don't keep it in the widget
        try:
            client = self._factory(unsplash.check_key_format(text))
        except unsplash.UnsplashError as e:
            error(self, "Unsplash key", str(e))
            return
        self.key_save.setEnabled(False)
        self.message.emit("checking the key with Unsplash…")

        def ok(_remaining):
            self.key_save.setEnabled(True)
            try:
                where = unsplash.save_key(text)
            except (unsplash.UnsplashError, OSError) as e:
                error(self, "Unsplash key", f"couldn't store the key: {e}")
                return
            self.client, self.key_where = client, where
            self._show_key_box(False)
            self._update_key_status()
            self.message.emit(f"Unsplash key saved {where_text(where)}")

        def bad(err):
            self.key_save.setEnabled(True)
            error(self, "Unsplash key", f"{err}\n\nThe key was not saved.")
        run_async(client.check, ok, bad)

    def forget_key(self) -> None:
        if not confirm(self, "Remove key", "Remove the Unsplash key from this computer? Pictures you "
                       "downloaded stay.", "Remove"):
            return
        unsplash.delete_key()
        self.client = None
        self.grid.clear()
        self.photos.clear()
        self.load_key()
        self.message.emit("Unsplash key removed")

    # ------------------------------------------------------------------ searching
    def _call(self, fn, on_done, what: str) -> None:
        if self.client is None or self._busy:
            return
        self._busy = True
        for b in (self.search_btn, self.suggest_btn, self.more):
            b.setEnabled(False)
        self.info.setText(what)

        def finish():
            self._busy = False
            for b in (self.search_btn, self.suggest_btn, self.more):
                b.setEnabled(True)
            self._update_key_status()

        def done(result):
            finish()
            on_done(result)

        def failed(err):
            finish()
            self.info.setText(f"<span style='color:{RED}'>{_esc(err)}</span>")
            if "didn't accept the access key" in err:
                self._show_key_box(True)
        run_async(fn, done, failed)

    def search(self) -> None:
        q = self.query.text().strip()
        if not q:
            return
        self._query = (q, self.orientation.currentData())
        self._page = 1
        client = self.client
        self._call(lambda: client.search(q, page=1, orientation=self._query[1]),
                   lambda r: self._show_results(r, replace=True), f"searching for “{_esc(q)}”…")

    def load_more(self) -> None:
        if not self._query or self._page >= self._pages:
            return
        q, orient = self._query
        page = self._page + 1
        client = self.client

        def done(r):
            self._page = page
            self._show_results(r, replace=False)
        self._call(lambda: client.search(q, page=page, orientation=orient), done, "loading more…")

    def suggest(self) -> None:
        client, proj = self.client, self.project
        self._query = None

        def done(res):
            used, photos = res
            self._pages = 1
            self._fill(photos, replace=True)
            self.info.setText(f"Suggested from your most used tags: <b>{_esc(', '.join(used))}</b> "
                              f"({len(photos)} photos you don't have yet)")
        self._call(lambda: unsplash.suggest(client, proj), done, "finding photos like yours…")

    def _show_results(self, r: dict, replace: bool) -> None:
        self._pages = int(r.get("total_pages") or 1)
        self._fill(r.get("results") or [], replace)
        q = self._query[0] if self._query else ""
        self.info.setText(f"{int(r.get('total') or 0):,} photos for “{_esc(q)}”" if r.get("total")
                          else f"Nothing found for “{_esc(q)}”.")

    def _fill(self, photos: list[dict], replace: bool) -> None:
        if replace:
            self._gen += 1
            self.grid.clear()
            self.photos.clear()
            self.show_photo(None)
        have = self._have()
        for ph in photos:
            pid = str(ph.get("id") or "")
            if not pid or pid in self.photos:
                continue
            if self._query and not ph.get("_found_by"):
                ph["_found_by"] = [self._query[0]]  # free tags if Unsplash's aren't fetched
            self.photos[pid] = ph
            name = (ph.get("user") or {}).get("name") or "?"
            it = QListWidgetItem(("✓ " if pid in have else "") + name)
            it.setData(Qt.UserRole, pid)
            it.setToolTip(f"Photo by {name} on Unsplash" + ("\n(already in your pictures)" if pid in have else ""))
            it.setSizeHint(QSize(ICON.width() + 12, ICON.height() + 36))
            self.grid.addItem(it)
            self._load_pixmap(pid, (ph.get("urls") or {}).get("small"), lambda pm, it=it: it.setIcon(QIcon(pm)))
        self.more.setVisible(self._query is not None and self._page < self._pages)

    def _have(self) -> set[str]:
        try:
            return unsplash.library_ids(self.project)
        except (OSError, ProjectError):
            return set()

    def _load_pixmap(self, key: str, url: str | None, apply) -> None:
        """Hotlinked image, kept in memory only (Unsplash asks for hotlinking)."""
        if not url:
            return
        if key in self._pixmaps:
            apply(self._pixmaps[key])
            return
        gen = self._gen

        def done(data: bytes):
            img = QImage.fromData(data)
            if img.isNull():
                return
            pm = QPixmap.fromImage(img)
            if len(self._pixmaps) > 400:
                self._pixmaps.clear()
            self._pixmaps[key] = pm
            if gen == self._gen:
                apply(pm)
        run_async(lambda: unsplash.fetch_image(url), done)

    # ------------------------------------------------------------------ one photo
    def _desk_layout(self):
        if self._layout is None:
            try:
                from ..layout import compute_layout
                self._layout = compute_layout(self.project.load_config(None))
            except Exception:  # noqa: BLE001 - no valid layout: no sharpness note
                self._layout = False
        return self._layout or None

    def refresh_layout(self) -> None:
        self._layout = None
        cur = self.grid.currentItem()
        if cur is not None:
            self.show_photo(cur)

    def current_photo(self) -> dict | None:
        it = self.grid.currentItem()
        return self.photos.get(it.data(Qt.UserRole)) if it is not None else None

    def show_photo(self, item) -> None:
        ph = self.photos.get(item.data(Qt.UserRole)) if item is not None else None
        self.after.setText("")
        if ph is None:
            self.preview.setPixmap(QPixmap())
            self.preview.setText("Pick a photo")
            for w in (self.credit, self.about, self.quality):
                w.setText("")
            self.download_btn.setEnabled(False)
            return
        pid = ph["id"]
        self.preview.setText("loading…")
        self.preview.setPixmap(QPixmap())
        urls = ph.get("urls") or {}
        self._load_pixmap(f"{pid}:regular", urls.get("regular") or urls.get("small"),
                          lambda pm, pid=pid: self._set_preview(pid, pm))
        user = ph.get("user") or {}
        profile, page = unsplash.credit_links(ph)
        self.credit.setText(f"Photo by <a href='{_esc(profile)}'>{_esc(user.get('name') or user.get('username'))}"
                            f"</a> on <a href='{_esc(page)}'>Unsplash</a>")
        self.about.setText(_esc((ph.get("description") or ph.get("alt_description") or "").capitalize()))
        w, h = int(ph.get("width") or 0), int(ph.get("height") or 0)
        lay = self._desk_layout()
        if lay is not None and w and h:
            f = unsplash.enlargement(w, h, lay)
            self.quality.setText(f"✓ {w}×{h} px: sharp on every screen." if f <= 1.05 else
                                 f"⚠ {w}×{h} px: enlarged {f:.1f}× across your desk, so it may look soft.")
        else:
            self.quality.setText(f"{w}×{h} px" if w else "")
        have = pid in self._have()
        self.download_btn.setEnabled(not self._busy)
        self.download_btn.setText("Download again" if have else "Download to my pictures")
        if have:
            self.after.setText("Already in your pictures.")

    def _set_preview(self, pid: str, pm: QPixmap) -> None:
        cur = self.current_photo()
        if cur and cur.get("id") == pid:
            self.preview.setPixmap(pm.scaled(PREVIEW, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def download(self) -> None:
        ph = self.current_photo()
        if ph is None or self.client is None or self._busy:
            return
        client, proj, fetch = self.client, self.project, self.fetch_tags.isChecked()
        self._busy = True
        self.download_btn.setEnabled(False)
        self.download_btn.setText("Downloading…")
        name = (ph.get("user") or {}).get("name") or "?"

        def work():
            path, full = client.download(ph, proj.pictures, fetch_tags=fetch)
            tags = unsplash.save_download(proj, path, full)
            return path, tags

        def done(res):
            self._busy = False
            path, tags = res
            self.download_btn.setEnabled(True)
            self.download_btn.setText("Download again")
            self.after.setText(f"Saved as <b>{_esc(path.name)}</b>"
                               + (f" with the tags {_esc(', '.join(tags))}" if tags else "")
                               + ". Credit is kept in pictures/credits.yml.")
            for i in range(self.grid.count()):
                it = self.grid.item(i)
                if it.data(Qt.UserRole) == ph["id"] and not it.text().startswith("✓"):
                    it.setText("✓ " + it.text())
            self._update_key_status()
            self.message.emit(f"downloaded {path.name} (photo by {name} on Unsplash)")
            self.downloaded.emit(str(path))

        def failed(err):
            self._busy = False
            self.download_btn.setEnabled(True)
            self.download_btn.setText("Download to my pictures")
            self._update_key_status()
            error(self, "Download", err)
        run_async(work, done, failed)


def _setting_bool(key: str, default: bool) -> bool:
    v = QSettings().value(key, default)
    return v if isinstance(v, bool) else str(v).lower() in ("true", "1")


def credit_html(project: Project, name: str) -> str:
    """'Photo by X on Unsplash' for a downloaded picture, or ''."""
    c = unsplash.credit_for(project, Path(name).name)
    if not c or c.get("source") != "unsplash":
        return ""
    return (f"Photo by <a href='{_esc(c.get('profile'))}'>{_esc(c.get('photographer'))}</a> on "
            f"<a href='{_esc(c.get('photo'))}'>Unsplash</a>")
