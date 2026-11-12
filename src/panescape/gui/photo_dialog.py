"""Calibrate a layout from phone photos of the photo pattern (the ``photo`` extra).

The dialog doesn't block the window: showing the pattern minimises panescape so
nothing covers the screens, and the dialog comes back with the window.
"""

from __future__ import annotations

import copy

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from .. import photocal, yamledit
from .common import run_async

STEPS = (
    "<ol style='margin-left:-20px'>"
    "<li><b>Show the photo pattern</b>: each screen shows a white panel with dots on black, and "
    "panescape minimises itself. Move any other windows off the screens.</li>"
    "<li>Sit where you normally sit. Hold your phone <b>where your eyes are</b> and take a photo with "
    "every screen fully in the frame. If they don't all fit, take two or more photos from the same "
    "seat, turning the phone: each screen fully in one of them, and each photo sharing a screen with "
    "another. Dim the room lights if they reflect in the screens.</li>"
    "<li>Bring panescape back (click it in the dock), copy the photos to this computer and "
    "<b>drop them here</b> (or add them).</li>"
    "</ol>"
)


class _Restored(QObject):
    """Shows the dialog again when the main window comes back from being minimised."""

    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog

    def eventFilter(self, obj, event):
        if event.type() == QEvent.WindowStateChange and not obj.isMinimized() and self.dialog.waiting:
            self.dialog.waiting = False
            self.dialog.show()
            self.dialog.raise_()
        return False


class PhotoDialog(QDialog):
    """Show the pattern, read photos, and offer the measured numbers."""

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setWindowTitle("Calibrate from a photo")
        self.setModal(False)
        self.setAcceptDrops(True)
        self.resize(820, 660)
        self._shown = False
        self.waiting = False
        self.paths: list[str] = []
        self._found: list = []
        self._restored = _Restored(self)
        editor.window().installEventFilter(self._restored)
        v = QVBoxLayout(self)
        steps = QLabel(STEPS)
        steps.setWordWrap(True)
        steps.setTextFormat(Qt.RichText)
        v.addWidget(steps)
        row = QHBoxLayout()
        self.show_btn = QPushButton("Show the photo pattern")
        self.show_btn.clicked.connect(self.show_pattern)
        self.show_btn.setEnabled(editor.backend() is not None)
        if editor.backend() is None:
            self.show_btn.setToolTip("No supported desktop found: use 'panescape calibrate --photo-pattern' "
                                     "or put the images up yourself")
        row.addWidget(self.show_btn)
        add = QPushButton("Add photos…")
        add.clicked.connect(self.choose)
        row.addWidget(add)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear)
        self.clear_btn.setEnabled(False)
        row.addWidget(self.clear_btn)
        row.addStretch(1)
        v.addLayout(row)
        self.picture = QLabel("Drop photos here")
        self.picture.setAlignment(Qt.AlignCenter)
        self.picture.setMinimumHeight(240)
        self.picture.setStyleSheet("border: 1px dashed palette(mid); color: palette(placeholder-text)")
        v.addWidget(self.picture, 1)
        self.result = QLabel()
        self.result.setWordWrap(True)
        self.result.setTextFormat(Qt.RichText)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.result)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.use_btn = buttons.addButton("Use these numbers", QDialogButtonBox.AcceptRole)
        self.use_btn.setEnabled(False)
        self.use_btn.clicked.connect(self.use)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

    # ------------------------------------------------------------------ the pattern
    def show_pattern(self) -> None:
        ed = self.editor
        if ed.cfg is None:
            self.result.setText("The layout isn't valid yet: fix it first.")
            return
        be, cfg, proj = ed.backend(), copy.deepcopy(ed.cfg), ed.project

        def work():
            proj.begin_calibration(be)
            return proj.apply_calibration(be, cfg, photo=True)

        self._shown = True
        self.show_btn.setEnabled(False)
        run_async(work, self._pattern_up, self._pattern_failed)

    def _pattern_up(self, _msg) -> None:
        self.show_btn.setEnabled(True)
        self.waiting = True  # back with the main window
        self.hide()
        self.editor.window().showMinimized()

    def _pattern_failed(self, err: str) -> None:
        self.result.setText(f"Couldn't show the pattern: {err}")
        self.show_btn.setEnabled(True)

    def done(self, r) -> None:
        self.editor.window().removeEventFilter(self._restored)
        if self._shown:  # put back what was there: the millimetre pattern, or the wallpaper
            ed = self.editor
            if ed.calib.isChecked():
                ed._calibrate_now()
            else:
                ed._end_calibration()
        super().done(r)

    # ------------------------------------------------------------------ the photos
    def choose(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Photos of your screens", "",
                                                "Photos (*.jpg *.jpeg *.png *.heic *.heif *.webp);;All files (*)")
        self.add(paths)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def add(self, paths) -> None:
        new = [p for p in paths if p and p not in self.paths]
        if new:
            self.paths += new
            self.analyse()

    def clear(self) -> None:
        self.paths = []
        self._found = []
        self.use_btn.setEnabled(False)
        self.clear_btn.setEnabled(False)
        self.picture.clear()
        self.picture.setText("Drop photos here")
        self.result.setText("")

    def analyse(self) -> None:
        ed = self.editor
        if ed.doc is None or not self.paths:
            return
        raw = yamledit.plain(ed.doc)
        paths = list(self.paths)
        self.clear_btn.setEnabled(True)
        self.result.setText(f"Measuring {len(paths)} photo{'s' * (len(paths) > 1)}…")
        self.use_btn.setEnabled(False)

        def work():
            try:
                res = photocal.analyse(paths, raw)
            except photocal.PhotoError as e:
                return None, str(e), e.marked  # show what was found, so you can see what went wrong
            return res, photocal.changes(res, raw), [photocal.overlay(res, i) for i in range(len(paths))]

        run_async(work, self._measured, self._failed)

    def _failed(self, err: str) -> None:
        self.use_btn.setEnabled(False)
        self.result.setText("<b>Couldn't measure:</b> " + err.replace("\n", "<br>"))

    def _measured(self, out) -> None:
        res, found, marked = out
        if marked:
            self._show_photos(marked)
        if res is None:
            self._failed(found + "<br>Green: screens found; orange: screens cut off at the photo's edge.")
            return
        lines = [f"Matched to within <b>{res.rms_mm:.1f} mm</b> on average "
                 f"(worst: {res.worst[0]}, {res.worst[1]:.1f} mm). "
                 "Green: what was found; pink: where the measured layout puts the corners."]
        if res.flat and "viewer" not in yamledit.plain(self.editor.doc):
            lines.append("Your screens came out flat (no angle or tilt over a degree).")
        if res.worst[1] > 5:
            lines.append("<span style='color:#e06040'>That's a large error: check that the layout's screen "
                         "sizes are right, and that the photos were taken from the same seat.</span>")
        if found:
            rows = "".join(f"<tr><td>{name or 'your eyes'}</td><td>{_label(key)}</td>"
                           f"<td align=right>{'–' if old is None else old}</td><td>→</td>"
                           f"<td align=right><b>{new}</b></td></tr>" for name, key, old, new in found)
            lines.append(f"<table cellspacing=6>{rows}</table>")
        else:
            lines.append("The layout already matches the photos.")
        self.result.setText("<br>".join(lines))
        self._found = found
        self.use_btn.setEnabled(bool(found))

    def _show_photos(self, images) -> None:
        """The marked photos side by side."""
        from PIL import Image
        h = 480
        scaled = [im.convert("RGB").resize((max(1, round(im.width * h / im.height)), h)) for im in images]
        strip = Image.new("RGB", (sum(i.width for i in scaled) + 8 * (len(scaled) - 1), h), (40, 40, 40))
        x = 0
        for im in scaled:
            strip.paste(im, (x, 0))
            x += im.width + 8
        q = QImage(strip.tobytes(), strip.width, strip.height, strip.width * 3, QImage.Format_RGB888).copy()
        self.picture.setStyleSheet("")
        self.picture.setPixmap(QPixmap.fromImage(q).scaled(self.picture.size(), Qt.KeepAspectRatio,
                                                           Qt.SmoothTransformation))

    def use(self) -> None:
        from .layout_editor import put
        ed = self.editor
        photocal.apply_changes(ed.doc, self._found, put=put)
        ed._changed(True)
        ed.message.emit("layout updated from the photos: press Save to keep it")
        self.accept()


def _label(key: str) -> str:
    return {"gap": "gap", "v_pos": "raised by", "h_pos": "shifted right by", "angle": "turned towards you",
            "tilt": "leaning back", "position": "top-left", "distance": "distance to the first screen",
            "height": "raised by", "offset": "moved right by"}.get(key, key)
