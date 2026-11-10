"""Thumbnails, a wrapping layout and tag chips."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QLayout, QPushButton, QSizePolicy

THUMB = 256


# --------------------------------------------------------------------------- thumbnails

def thumb_cache_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "panescape" / "thumbs"


def image_size(path: Path) -> tuple[int, int]:
    """Pixel size as displayed (EXIF rotation applied), read from the header only."""
    from PIL import Image
    with Image.open(path) as im:
        w, h = im.size
        try:
            orient = im.getexif().get(274, 1)
        except Exception:  # noqa: BLE001
            orient = 1
    return (h, w) if orient in (5, 6, 7, 8) else (w, h)


def make_thumbnail(path: Path, size: int = THUMB) -> tuple[QImage, tuple[int, int]]:
    """(thumbnail, full pixel size). Runs on a worker thread; cached on disk."""
    st = path.stat()
    key = hashlib.sha1(f"{path.resolve()}|{st.st_mtime_ns}|{st.st_size}|{size}".encode()).hexdigest()
    cache = thumb_cache_dir() / f"{key}.jpg"
    full = image_size(path)
    if cache.exists():
        img = QImage(str(cache))
        if not img.isNull():
            return img, full
    from PIL import Image, ImageOps
    with Image.open(path) as im:
        im.draft("RGB", (size * 2, size * 2))  # JPEG: decode at a fraction of full size
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((size, size), Image.Resampling.LANCZOS)
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".tmp")
        im.save(tmp, "JPEG", quality=85)
        os.replace(tmp, cache)
    return QImage(str(cache)), full


# --------------------------------------------------------------------------- flow layout

class FlowLayout(QLayout):
    """Lays widgets out left to right, wrapping onto new lines (Qt's classic example)."""

    def __init__(self, parent=None, spacing: int = 4):
        super().__init__(parent)
        self._items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout(rect, apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        return size

    def clear(self) -> None:
        while self._items:
            it = self._items.pop()
            if it.widget():
                it.widget().deleteLater()

    def _layout(self, rect, apply: bool) -> int:
        x, y, line = rect.x(), rect.y(), 0
        gap = self.spacing()
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > rect.right() and line > 0:
                x, y, line = rect.x(), y + line + gap, 0
            if apply:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + gap
            line = max(line, hint.height())
        return y + line - rect.y()


# --------------------------------------------------------------------------- tag chips

class Chip(QPushButton):
    """A tag. Full chips (every selected picture has it) remove on click; partial
    chips (some have it) offer add-to-all / remove-from-all."""

    remove = Signal(str)
    add_all = Signal(str)

    def __init__(self, tag: str, have: int, total: int, locked_from: str | None = None, parent=None):
        super().__init__(parent)
        self.tag = tag
        partial = have < total
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        if locked_from:
            self.setText(f"{tag}  🔒")
            self.setToolTip(f"Given by the entry “{locked_from}” in tags.yml; edit that entry to change it.")
            self.setEnabled(False)
        elif partial:
            self.setText(f"{tag}  {have}/{total}")
            self.setToolTip(f"{have} of the {total} selected pictures have this tag. Click to add it to all "
                            "of them; right-click to remove it from all.")
            self.clicked.connect(lambda: self.add_all.emit(tag))
        else:
            self.setText(f"{tag}  ✕")
            self.setToolTip("Click to remove this tag")
            self.clicked.connect(lambda: self.remove.emit(tag))
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda _p: self.remove.emit(tag))
        style = ("QPushButton { border-radius: 10px; padding: 2px 10px; border: 1px solid palette(mid); "
                 "background: %s; } QPushButton:hover { border-color: palette(highlight); }")
        self.setStyleSheet(style % ("palette(base)" if partial else "palette(alternate-base)"))
