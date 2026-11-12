"""Calibrate a layout from a phone photo of the photo pattern (the ``photo`` extra)."""

from __future__ import annotations

import copy

from PySide6.QtCore import Qt
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
    "<li><b>Show the photo pattern</b>: each screen shows a white panel on black.</li>"
    "<li>Sit where you normally sit. Hold your phone <b>where your eyes are</b> and take a photo with "
    "the main (1×) camera, every screen fully in the frame. Dim the room lights if they reflect in "
    "the screens.</li>"
    "<li>Copy the photo to this computer and <b>drop it here</b> (or choose it).</li>"
    "</ol>"
)


class PhotoDialog(QDialog):
    """Show the pattern, read a photo, and offer the measured numbers."""

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setWindowTitle("Calibrate from a photo")
        self.setAcceptDrops(True)
        self.resize(760, 640)
        self._shown = False
        self._found: list = []
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
        choose = QPushButton("Choose photo…")
        choose.clicked.connect(self.choose)
        row.addWidget(choose)
        row.addStretch(1)
        v.addLayout(row)
        self.picture = QLabel("Drop a photo here")
        self.picture.setAlignment(Qt.AlignCenter)
        self.picture.setMinimumHeight(260)
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
        run_async(work, lambda _m: self.show_btn.setEnabled(True), self._pattern_failed)

    def _pattern_failed(self, err: str) -> None:
        self.result.setText(f"Couldn't show the pattern: {err}")
        self.show_btn.setEnabled(True)

    def done(self, r) -> None:
        if self._shown:  # put back what was there: the millimetre pattern, or the wallpaper
            ed = self.editor
            if ed.calib.isChecked():
                ed._calibrate_now()
            else:
                ed._end_calibration()
        super().done(r)

    # ------------------------------------------------------------------ the photo
    def choose(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Photo of your screens", "",
                                              "Photos (*.jpg *.jpeg *.png *.heic *.heif *.webp);;All files (*)")
        if path:
            self.analyse(path)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.analyse(urls[0])

    def analyse(self, path: str) -> None:
        ed = self.editor
        if ed.doc is None:
            return
        raw = yamledit.plain(ed.doc)
        self.result.setText("Measuring…")
        self.use_btn.setEnabled(False)

        def work():
            res = photocal.analyse(path, raw)
            return res, photocal.changes(res, raw), photocal.overlay(res, res.photo)

        run_async(work, self._measured, lambda err: self.result.setText(f"<b>Couldn't measure:</b> {err}"))

    def _measured(self, out) -> None:
        res, found, marked = out
        img = marked.convert("RGB")
        q = QImage(img.tobytes(), img.width, img.height, img.width * 3, QImage.Format_RGB888).copy()
        self.picture.setPixmap(QPixmap.fromImage(q).scaled(self.picture.size(), Qt.KeepAspectRatio,
                                                           Qt.SmoothTransformation))
        self.picture.setStyleSheet("")
        lines = [f"Matched to within <b>{res.rms_mm:.1f} mm</b> on average "
                 f"(worst: {res.worst[0]}, {res.worst[1]:.1f} mm). "
                 "Green: what was found; pink: where the measured layout puts the corners."]
        if res.flat and "viewer" not in yamledit.plain(self.editor.doc):
            lines.append("Your screens came out flat (no angle or tilt over a degree).")
        if res.worst[1] > 5:
            lines.append("<span style='color:#e06040'>That's a large error: check that the right "
                         "monitors were found, and that the layout's sizes are right.</span>")
        if found:
            rows = "".join(f"<tr><td>{name or 'your eyes'}</td><td>{_label(key)}</td>"
                           f"<td align=right>{'–' if old is None else old}</td><td>→</td>"
                           f"<td align=right><b>{new}</b></td></tr>" for name, key, old, new in found)
            lines.append(f"<table cellspacing=6>{rows}</table>")
        else:
            lines.append("The layout already matches the photo.")
        self.result.setText("<br>".join(lines))
        self._found = found
        self.use_btn.setEnabled(bool(found))

    def use(self) -> None:
        from .layout_editor import put
        ed = self.editor
        photocal.apply_changes(ed.doc, self._found, put=put)
        ed._changed(True)
        ed.message.emit("layout updated from the photo: press Save to keep it")
        self.accept()


def _label(key: str) -> str:
    return {"gap": "gap", "v_pos": "raised by", "h_pos": "shifted right by", "angle": "turned towards you",
            "tilt": "leaning back", "position": "top-left", "distance": "distance to the first screen",
            "height": "raised by", "offset": "moved right by"}.get(key, key)
