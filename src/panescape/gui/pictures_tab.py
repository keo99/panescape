"""Pictures tab: browse, import and tag pictures, filter them, frame each one.

Tags go to pictures/tags.yml and framing to pictures/crops.yml (comments
kept). If panescape's wallpaper is on the desktop, it is updated shortly
after a change, like ``panescape tag`` does.
"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QFile, QFileSystemWatcher, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QImage, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QCompleter,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .. import tags as tagmod
from ..project import IMAGE_EXTS, ImageJob, Project, ProjectError
from ..render import CropSpec
from .common import confirm, error, run_async
from .desk_view import DeskView
from .widgets import Chip, FlowLayout, make_thumbnail

ICON = QSize(196, 132)
UNTAGGED = "\x00untagged"
PREVIEW_PX = 1800


class PictureGrid(QListWidget):
    """Thumbnail grid that also accepts image files dropped from a file manager."""

    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListWidget.IconMode)
        self.setIconSize(ICON)
        self.setGridSize(QSize(ICON.width() + 16, ICON.height() + 44))
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setWordWrap(True)
        self.setUniformItemSizes(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        # setMovement(Static) switches drops off on the viewport, which is the widget
        # that actually receives them; switch them back on.
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)

    def _files(self, e) -> list[str]:
        if not e.mimeData().hasUrls():
            return []
        return [u.toLocalFile() for u in e.mimeData().urls()
                if u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in IMAGE_EXTS]

    def dragEnterEvent(self, e):
        if self._files(e):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if self._files(e):
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = self._files(e)
        if files:
            e.acceptProposedAction()
            self.files_dropped.emit(files)


class PicturesTab(QWidget):
    message = Signal(str)

    def __init__(self, project: Project, backend_getter, parent=None):
        super().__init__(parent)
        self.project = project
        self.backend = backend_getter
        self.tags: dict[str, set[str]] = {}
        self.items: dict[str, QListWidgetItem] = {}   # file name -> grid item
        self.sizes: dict[str, tuple[int, int]] = {}   # file name -> full pixel size
        self.previews: dict[str, QImage] = {}
        self.cfg = None
        self.layout_ = None
        self._crop_name = None
        self._loading_thumbs: set[str] = set()

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Filter:"))
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("tags, e.g.  landscape + water + !mountains    (| = or, ! = not, * wildcards)")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(lambda _t: self._filter_timer.start())
        top.addWidget(self.filter, 1)
        self.count = QLabel()
        top.addWidget(self.count)
        self.use_filter = QPushButton("Use for setup")
        self.use_filter.setToolTip("Make this filter choose the active setup's pictures")
        self.use_filter.clicked.connect(self.use_filter_for_setup)
        top.addWidget(self.use_filter)
        imp = QPushButton("Add pictures…")
        imp.setToolTip("Copy pictures into the pictures folder (or drop files onto the grid)")
        imp.clicked.connect(self.import_dialog)
        top.addWidget(imp)
        root.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        # --- tag sidebar
        side = QWidget()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(QLabel("Tags"))
        self.tag_list = QListWidget()
        self.tag_list.setToolTip("Click to filter by a tag; Ctrl+click to add it to the filter")
        self.tag_list.itemClicked.connect(self._tag_clicked)
        sl.addWidget(self.tag_list, 1)
        row = QHBoxLayout()
        for text, slot in (("Rename…", self.rename_tag), ("Delete…", self.delete_tag)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        sl.addLayout(row)
        split.addWidget(side)

        # --- grid
        self.grid = PictureGrid()
        self.grid.itemSelectionChanged.connect(self._selection_changed)
        self.grid.files_dropped.connect(self.import_files)
        split.addWidget(self.grid)

        # --- selection panel
        panel = QWidget()
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(4, 0, 0, 0)
        self.title = QLabel()
        self.title.setWordWrap(True)
        f = self.title.font()
        f.setBold(True)
        self.title.setFont(f)
        pl.addWidget(self.title)
        self.credit = QLabel()
        self.credit.setOpenExternalLinks(True)
        self.credit.setTextFormat(Qt.RichText)
        pl.addWidget(self.credit)
        self.chips_box = QWidget()
        self.chips = FlowLayout(self.chips_box)
        pl.addWidget(self.chips_box)
        arow = QHBoxLayout()
        self.add_tag = QLineEdit()
        self.add_tag.setPlaceholderText("add tags (comma or space separated), Enter")
        self.add_tag.returnPressed.connect(self._add_typed_tags)
        self.completer = QCompleter([])
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.add_tag.setCompleter(self.completer)
        arow.addWidget(self.add_tag, 1)
        pl.addLayout(arow)
        self.notes = QLabel()
        self.notes.setWordWrap(True)
        self.notes.setStyleSheet("color: palette(placeholder-text)")
        pl.addWidget(self.notes)

        self.frame_box = QGroupBox("Framing on the desk")
        fl = QVBoxLayout(self.frame_box)
        self.desk = DeskView()
        self.desk.set_framing(True)
        self.desk.labels = False
        self.desk.setMinimumHeight(220)
        self.desk.crop_changed.connect(self._crop_moved)
        self.desk.crop_done.connect(lambda: self._crop_save_timer.start())
        fl.addWidget(self.desk, 1)
        frow = QHBoxLayout()
        self.fit = QComboBox()
        self.fit.addItem("Fill the screens (crop edges)", "cover")
        self.fit.addItem("Show the whole picture (bars)", "contain")
        self.fit.currentIndexChanged.connect(self._fit_changed)
        frow.addWidget(self.fit, 1)
        reset = QPushButton("Reset")
        reset.setToolTip("Centre the picture and fit it again")
        reset.clicked.connect(self._reset_crop)
        frow.addWidget(reset)
        fl.addLayout(frow)
        self.quality = QLabel()
        self.quality.setWordWrap(True)
        fl.addWidget(self.quality)
        pl.addWidget(self.frame_box, 1)

        brow = QHBoxLayout()
        self.show_btn = QPushButton("Show in folder")
        self.show_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.project.pictures))))
        brow.addWidget(self.show_btn)
        self.trash_btn = QPushButton("Move to trash…")
        self.trash_btn.clicked.connect(self.trash_selected)
        brow.addWidget(self.trash_btn)
        pl.addLayout(brow)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(400)
        split.addWidget(scroll)
        split.setStretchFactor(1, 1)
        split.setSizes([180, 760, 440])
        root.addWidget(split, 1)

        self._filter_timer = QTimer(self, singleShot=True, interval=150, timeout=self.apply_filter)
        self._crop_save_timer = QTimer(self, singleShot=True, interval=400, timeout=self._save_crop)
        self._sync_timer = QTimer(self, singleShot=True, interval=2500, timeout=self._sync_now)
        self._syncing = False
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(lambda _p: self._refresh_timer.start())
        self.watcher.fileChanged.connect(lambda _p: self._refresh_timer.start())
        self._refresh_timer = QTimer(self, singleShot=True, interval=300, timeout=self.refresh)
        self._placeholder = self._placeholder_icon()
        self.refresh()
        self.refresh_layout()

    # ------------------------------------------------------------------ loading
    def _placeholder_icon(self) -> QIcon:
        pm = QPixmap(ICON)
        pm.fill(QColor(60, 60, 66))
        return QIcon(pm)

    def _watch(self) -> None:
        for p in (self.project.pictures, self.project.tags_file, self.project.crops_file):
            if p.exists() and str(p) not in self.watcher.files() + self.watcher.directories():
                self.watcher.addPath(str(p))

    def refresh(self) -> None:
        """Re-read pictures/ and tags.yml; keeps the selection."""
        self.project.pictures.mkdir(parents=True, exist_ok=True)
        self._watch()
        try:
            self.tags = tagmod.load_tags(self.project.tags_file)
        except Exception as e:  # noqa: BLE001
            self.tags = {}
            self.message.emit(f"tags.yml: {e}")
        selected = set(self.selected_names())
        pics = self.project.all_pictures()
        names = [p.name for p in pics]
        for name in list(self.items):
            if name not in names:
                self.grid.takeItem(self.grid.row(self.items.pop(name)))
                self.previews.pop(name, None)
        for p in pics:
            item = self.items.get(p.name)
            if item is None:
                item = QListWidgetItem(self._placeholder, p.name)
                item.setData(Qt.UserRole, str(p))
                item.setSizeHint(self.grid.gridSize())
                self.grid.addItem(item)
                self.items[p.name] = item
                self._load_thumb(p)
            item.setSelected(p.name in selected)
            own = sorted(tagmod.tags_for(p.name, self.tags))
            item.setToolTip(f"{p.name}\n{', '.join(own) or 'no tags'}")
            item.setText(p.name if len(p.name) <= 28 else p.name[:25] + "…")
        self.grid.sortItems()
        self._fill_tag_list()
        self.completer.model().setStringList(sorted(self.all_tags()))
        self.apply_filter()
        self._selection_changed()

    def _load_thumb(self, path: Path) -> None:
        name = path.name
        if name in self._loading_thumbs:
            return
        self._loading_thumbs.add(name)

        def done(result):
            self._loading_thumbs.discard(name)
            img, size = result
            self.sizes[name] = size
            item = self.items.get(name)
            if item is not None and not img.isNull():
                item.setIcon(QIcon(QPixmap.fromImage(img)))
            if name == self._crop_name:
                self._update_quality()

        def failed(err):
            self._loading_thumbs.discard(name)
            item = self.items.get(name)
            if item is not None:
                item.setToolTip(f"{name}\ncan't be read: {err}")
        run_async(lambda: make_thumbnail(path), done, failed)

    def refresh_layout(self) -> None:
        """The active setup's layout (after the setup or the layout changed)."""
        from ..layout import compute_layout
        try:
            self.cfg = self.project.load_config(None)
            self.layout_ = compute_layout(self.cfg)
        except Exception as e:  # noqa: BLE001
            self.cfg, self.layout_ = None, None
            self.desk.set_layout(None, valid=False, error=f"No usable layout: {e}")
        else:
            self.desk.set_layout(self.layout_)
        self.use_filter.setText(f"Use for setup “{self.project.active_setup() or 'default'}”")
        self._selection_changed()

    # ------------------------------------------------------------------ tags
    def all_tags(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for name in self.items:
            for t in tagmod.tags_for(name, self.tags):
                counts[t] = counts.get(t, 0) + 1
        return counts

    def _fill_tag_list(self) -> None:
        current = self.tag_list.currentItem().data(Qt.UserRole) if self.tag_list.currentItem() else None
        self.tag_list.clear()
        counts = self.all_tags()
        untagged = sum(1 for n in self.items if not tagmod.tags_for(n, self.tags))
        it = QListWidgetItem(f"All pictures  ({len(self.items)})")
        it.setData(Qt.UserRole, "")
        self.tag_list.addItem(it)
        for t in sorted(counts):
            it = QListWidgetItem(f"{t}  ({counts[t]})")
            it.setData(Qt.UserRole, t)
            self.tag_list.addItem(it)
        if untagged:
            it = QListWidgetItem(f"no tags  ({untagged})")
            it.setData(Qt.UserRole, UNTAGGED)
            f = it.font()
            f.setItalic(True)
            it.setFont(f)
            self.tag_list.addItem(it)
        for i in range(self.tag_list.count()):
            if self.tag_list.item(i).data(Qt.UserRole) == current:
                self.tag_list.setCurrentRow(i)

    def _tag_clicked(self, item) -> None:
        from PySide6.QtWidgets import QApplication
        tag = item.data(Qt.UserRole)
        term = "!*" if tag == UNTAGGED else tag
        cur = self.filter.text().strip()
        if QApplication.keyboardModifiers() & Qt.ControlModifier and cur and term:
            self.filter.setText(f"{cur} + {term}")
        else:
            self.filter.setText(term)

    def rename_tag(self) -> None:
        item = self.tag_list.currentItem()
        old = item.data(Qt.UserRole) if item else None
        if not old or old == UNTAGGED:
            error(self, "Rename tag", "Select a tag in the list first.")
            return
        new, ok = QInputDialog.getText(self, "Rename tag", f"Rename “{old}” to (an existing tag merges):",
                                       text=old)
        new = tagmod.norm_tag(new)
        if not ok or not new or new == old:
            return
        if re.search(r"[\s,+&|!()]", new):
            error(self, "Rename tag", "Tags can't contain spaces or any of , + & | ! ( )")
            return
        if new in self.all_tags() and not confirm(self, "Merge tags",
                                                  f"“{new}” already exists. Merge “{old}” into it?", "Merge"):
            return
        n = tagmod.rename_tag(self.project.tags_file, old, new)
        self.message.emit(f"renamed {old} → {new} ({n} entr{'y' if n == 1 else 'ies'})")
        self._after_tag_change()

    def delete_tag(self) -> None:
        item = self.tag_list.currentItem()
        tag = item.data(Qt.UserRole) if item else None
        if not tag or tag == UNTAGGED:
            error(self, "Delete tag", "Select a tag in the list first.")
            return
        n = self.all_tags().get(tag, 0)
        if not confirm(self, "Delete tag", f"Remove the tag “{tag}” from {n} picture(s)?", "Delete"):
            return
        tagmod.delete_tag(self.project.tags_file, tag)
        self.message.emit(f"deleted tag {tag}")
        self._after_tag_change()

    def _change_tags(self, add: set[str], remove: set[str]) -> None:
        names = self.selected_names()
        if not names:
            return
        warnings = tagmod.edit_tags(self.project.tags_file, {n: (add, remove) for n in names})
        for w in warnings:
            self.message.emit(w)
        self._after_tag_change()

    def _add_typed_tags(self) -> None:
        typed = {tagmod.norm_tag(t) for t in re.split(r"[,\s]+", self.add_tag.text()) if t.strip()}
        bad = [t for t in typed if re.search(r"[+&|!()]", t)]
        if bad:
            error(self, "Add tags", f"Tags can't contain + & | ! ( ): {', '.join(bad)}")
            return
        if typed:
            self._change_tags(typed, set())
        self.add_tag.clear()

    def _after_tag_change(self) -> None:
        self.refresh()
        self.schedule_sync()

    # ------------------------------------------------------------------ filter
    def apply_filter(self) -> None:
        text = self.filter.text().strip()
        try:
            pred, terms = tagmod.compile_filter(text)
        except tagmod.FilterError as e:
            self.filter.setStyleSheet("QLineEdit { border: 1px solid #d04030; }")
            self.count.setText(f"<span style='color:#d04030'>{e}</span>")
            return
        self.filter.setStyleSheet("")
        shown = 0
        for name, item in self.items.items():
            ok = pred(tagmod.tags_for(name, self.tags))
            item.setHidden(not ok)
            shown += ok
        unknown = sorted(t for t in terms if "*" not in t and t not in self.all_tags())
        note = f"  ·  no picture has: {', '.join(unknown)}" if unknown else ""
        self.count.setText(f"{shown} of {len(self.items)} match{note}" if text else f"{len(self.items)} pictures")
        try:
            current = str(self.project.load_setup().get("filter") or "")
        except ProjectError:
            current = ""
        self.use_filter.setEnabled(text != current)

    def use_filter_for_setup(self) -> None:
        text = self.filter.text().strip()
        try:
            self.project.update_setup({"filter": text or None})
        except ProjectError as e:
            error(self, "Use filter", str(e))
            return
        self.message.emit(f"setup “{self.project.active_setup()}” now uses "
                          + (f"the filter {text!r}" if text else "no filter"))
        self.use_filter.setEnabled(False)
        self.schedule_sync()

    # ------------------------------------------------------------------ selection
    def selected_names(self) -> list[str]:
        return sorted(Path(it.data(Qt.UserRole)).name for it in self.grid.selectedItems())

    def _selection_changed(self) -> None:
        names = self.selected_names()
        self.chips.clear()
        self.add_tag.setEnabled(bool(names))
        self.trash_btn.setEnabled(bool(names))
        self.notes.setText("")
        if not names:
            self.title.setText("Select pictures to tag them (Ctrl/Shift+click for several).")
        elif len(names) == 1:
            self.title.setText(names[0])
        else:
            self.title.setText(f"{len(names)} pictures selected")
        from .unsplash_tab import credit_html
        self.credit.setText(credit_html(self.project, names[0]) if len(names) == 1 else "")
        self.credit.setVisible(bool(self.credit.text()))
        counts: dict[str, int] = {}
        sources: dict[str, set[str]] = {}
        for n in names:
            for t, src in tagmod.tag_sources(n, self.tags).items():
                counts[t] = counts.get(t, 0) + 1
                if src != n and src != Path(n).stem:
                    sources.setdefault(t, set()).add(src)
        for t in sorted(counts):
            locked = next(iter(sources[t])) if t in sources and counts[t] == len(names) else None
            chip = Chip(t, counts[t], len(names), locked)
            chip.remove.connect(lambda tag: self._change_tags(set(), {tag}))
            chip.add_all.connect(lambda tag: self._change_tags({tag}, set()))
            self.chips.addWidget(chip)
        if names and not counts:
            self.notes.setText("No tags yet.")
        self.chips_box.updateGeometry()
        self._show_framing(names[0] if len(names) == 1 else None)

    # ------------------------------------------------------------------ framing
    def _base_crop(self) -> CropSpec:
        """What a picture gets with no crops.yml entry (configuration + setup defaults)."""
        setup = self.project.load_setup() if self.project.setup_file.exists() else {}
        return CropSpec.from_dicts(self.cfg.crop if self.cfg else {}, setup.get("crop"))

    def _show_framing(self, name: str | None) -> None:
        self._crop_name = name
        self.frame_box.setEnabled(bool(name) and self.layout_ is not None)
        if not name or self.layout_ is None:
            self.desk.set_image(None)
            self.quality.setText("Select one picture to frame it." if self.layout_ is not None else "")
            return
        path = self.project.pictures / name
        try:
            crop = self.project.crop_for(self.cfg, ImageJob(path))
        except (ValueError, ProjectError) as e:
            self.quality.setText(f"crop settings: {e}")
            crop = CropSpec()
        self.fit.blockSignals(True)
        self.fit.setCurrentIndex(max(0, self.fit.findData(crop.fit)))
        self.fit.blockSignals(False)
        img = self.previews.get(name)
        if img is None:
            self.desk.set_image(None, crop=crop)

            def load():
                reader = QImageReader(str(path))
                reader.setAutoTransform(True)
                size = reader.size()
                if size.isValid() and max(size.width(), size.height()) > PREVIEW_PX:
                    reader.setScaledSize(size.scaled(PREVIEW_PX, PREVIEW_PX, Qt.KeepAspectRatio))
                out = reader.read()
                if out.isNull():  # formats Qt can't read
                    from ..render import load_image
                    from .layout_editor import pil_to_qimage
                    pil = load_image(path)
                    pil.thumbnail((PREVIEW_PX, PREVIEW_PX))
                    out = pil_to_qimage(pil)
                return out

            def done(qimg):
                self.previews[name] = qimg
                if len(self.previews) > 12:
                    self.previews.pop(next(iter(self.previews)))
                if self._crop_name == name:
                    self.desk.set_image(qimg, crop=self.desk.crop)
            run_async(load, done, lambda e: self.quality.setText(f"can't show {name}: {e}"))
        else:
            self.desk.set_image(img, crop=crop)
        self._update_quality()

    def _update_quality(self) -> None:
        name = self._crop_name
        if not name or self.layout_ is None or name not in self.sizes:
            return
        W, H = self.sizes[name]
        c = self.layout_.canvas
        cr = self.desk.crop
        s = (min(W / c.w, H / c.h) if cr.fit == "cover" else max(W / c.w, H / c.h)) * cr.zoom  # px per mm
        worst = max(((p.monitor.pixels[0] / p.active.w) / s, p.monitor.name) for p in self.layout_)
        if worst[0] > 1.05:
            self.quality.setText(f"⚠ {W}×{H} px: enlarged {worst[0]:.1f}× on {worst[1]}, so it will look soft "
                                 "there. A larger picture (or less zoom) would be sharper.")
        else:
            self.quality.setText(f"✓ {W}×{H} px: sharp on every screen.")

    def _crop_moved(self, crop: CropSpec) -> None:
        self._update_quality()

    def _fit_changed(self, _i) -> None:
        from dataclasses import replace
        self.desk.set_image(self.desk.image, crop=replace(self.desk.crop, fit=self.fit.currentData()))
        self._update_quality()
        self._crop_save_timer.start()

    def _reset_crop(self) -> None:
        self.desk.set_image(self.desk.image, crop=self._base_crop())
        self.fit.blockSignals(True)
        self.fit.setCurrentIndex(max(0, self.fit.findData(self.desk.crop.fit)))
        self.fit.blockSignals(False)
        self._update_quality()
        self._crop_save_timer.start()

    def _save_crop(self) -> None:
        name = self._crop_name
        if not name:
            return
        crop, base = self.desk.crop, self._base_crop()
        values = {}
        if tuple(crop.anchor) != tuple(base.anchor):
            values["anchor"] = list(crop.anchor)
        if abs(crop.zoom - base.zoom) > 1e-6:
            values["zoom"] = crop.zoom
        if crop.fit != base.fit:
            values["fit"] = crop.fit
        if values == (self.project.picture_crops().get(name) or {}):
            return
        try:
            self.project.set_picture_crop(name, values or None)
        except Exception as e:  # noqa: BLE001
            error(self, "Framing", str(e))
            return
        self.message.emit(f"framing of {name} saved" if values else f"{name} uses the default framing again")
        self.schedule_sync()

    # ------------------------------------------------------------------ files
    def import_dialog(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(IMAGE_EXTS))
        files, _ = QFileDialog.getOpenFileNames(self, "Add pictures", str(Path.home() / "Pictures"),
                                                f"Images ({exts})")
        if files:
            self.import_files(files)

    def import_files(self, files: list[str]) -> None:
        new = self.project.import_pictures(files)
        skipped = len(files) - len(new)
        self.refresh()
        self.grid.clearSelection()
        for p in new:
            if p.name in self.items:
                self.items[p.name].setSelected(True)
        self.message.emit(f"added {len(new)} picture(s)" + (f", skipped {skipped}" if skipped else "")
                          + (": tag them on the right" if new else ""))
        if new:
            self.schedule_sync()

    def trash_selected(self) -> None:
        names = self.selected_names()
        if not names or not confirm(self, "Move to trash", f"Move {len(names)} picture(s) to the trash?\n\n"
                                    + "\n".join(names[:10]) + ("\n…" if len(names) > 10 else ""), "Move to trash"):
            return
        for n in names:
            path = self.project.pictures / n
            if not QFile.moveToTrash(str(path)):
                path.unlink(missing_ok=True)
            self.project.set_picture_crop(n, None) if n in self.project.picture_crops() else None
        tagmod.edit_tags(self.project.tags_file,
                         {n: (set(), set(self.tags.get(n, set()))) for n in names if n in self.tags})
        self.refresh()
        self.message.emit(f"moved {len(names)} picture(s) to the trash")
        self.schedule_sync()

    # ------------------------------------------------------------------ keeping the desktop up to date
    def schedule_sync(self) -> None:
        if self.project.load_state():
            self._sync_timer.start()

    def _sync_now(self) -> None:
        be = self.backend()
        if be is None or not self.project.load_state() or self.project.calibrating():
            return
        if self._syncing:
            self._sync_timer.start()
            return
        self._syncing = True
        proj = self.project

        def done(_r):
            self._syncing = False
            self.message.emit("wallpaper updated")

        def failed(err):
            self._syncing = False
            self.message.emit(f"wallpaper not updated: {err}")
        self.message.emit("updating the wallpaper…")
        run_async(lambda: proj.sync(be), done, failed)
