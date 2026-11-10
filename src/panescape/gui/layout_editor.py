"""Layout tab: pick / create configurations and edit their monitors.

The editor works on the configuration file's round-trip YAML document, so
saving keeps the user's comments. Every change re-runs the same loader and
layout solver the command line uses; the desk view, the problems line and
(when switched on) the calibration pattern on the real screens follow live.
"""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import QFile, QFileSystemWatcher, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QImageReader, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from .. import backends, models, yamledit
from ..config import ROTATION_NAMES, ConfigError, configuration_from_dict
from ..layout import compute_layout
from ..project import Project, ProjectError
from ..units import parse_length, parse_resolution
from .common import confirm, error, mm_spin, number, run_async
from .desk_view import DeskView

ATTACH = ("right_of", "left_of", "below", "above")
PLACEMENT = [("Right of", "right_of"), ("Left of", "left_of"), ("Below", "below"), ("Above", "above"),
             ("At a fixed position", "position")]
ROTATION_LABELS = [("Landscape", "landscape"), ("Portrait, turned right (clockwise)", "portrait-right"),
                   ("Portrait, turned left (anticlockwise)", "portrait-left"),
                   ("Landscape, upside down", "landscape-flipped")]
V_ALIGN = [("centres", "center"), ("top edges", "top"), ("bottom edges", "bottom")]
H_ALIGN = [("centres", "center"), ("left edges", "left"), ("right edges", "right")]
RESOLUTIONS = ["3840x2160", "2560x1440", "1920x1080", "1920x1200", "2560x1600", "3440x1440", "2560x1080",
               "5120x1440", "5120x2880", "3840x1600", "2880x1800", "1366x768"]
# where keys go in a monitor entry when the editor adds them
KEY_ORDER = ["name", "model", "size", "active_area", "resolution", "rotation", "orientation", "bezel",
             *ATTACH, "position", "gap", "v_align", "v_pos", "v_ref", "h_align", "h_pos", "h_ref",
             "connector", "serial", "product", "vendor"]
REFERENCE_KEYS = (*ATTACH, "v_ref", "h_ref")


def put(entry: CommentedMap, key: str, value) -> None:
    """Set a key, placing a new one where a person would expect it."""
    if key in entry:
        entry[key] = value
        return
    rank = KEY_ORDER.index(key) if key in KEY_ORDER else len(KEY_ORDER)
    keys = list(entry.keys())
    pos = next((i for i, k in enumerate(keys) if k in KEY_ORDER and KEY_ORDER.index(k) > rank), len(keys))
    entry.insert(pos, key, value)


def pil_to_qimage(img) -> QImage:
    img = img.convert("RGBA")
    return QImage(img.tobytes("raw", "RGBA"), img.width, img.height, QImage.Format_RGBA8888).copy()


def _combo(items) -> QComboBox:
    c = QComboBox()
    for label, data in items:
        c.addItem(label, data)
    return c


def _select(combo: QComboBox, data) -> None:
    i = combo.findData(data)
    combo.setCurrentIndex(max(i, 0))


# =========================================================================== monitor form

class MonitorForm(QWidget):
    """Fields for one monitor. Emits ``edit({key: value or None}, structural)``."""

    edit = Signal(dict, bool)
    rename = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self.index = -1
        self.entry: dict = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # --- the screen itself
        g = QGroupBox("Screen")
        f = QFormLayout(g)
        self.name = QLineEdit()
        self.name.editingFinished.connect(self._name_done)
        f.addRow("Name", self.name)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItem("")
        for key in models.MODELS:
            self.model.addItem(key.title())
        self.model.lineEdit().setPlaceholderText("not in the database")
        self.model.activated.connect(lambda _i: self._model_done())
        self.model.lineEdit().editingFinished.connect(self._model_done)
        f.addRow("Model", self.model)
        self.size = QLineEdit()
        self.size.setToolTip("Diagonal, e.g. 27in or 686mm. A known model or a measured active area is more precise.")
        self.size.editingFinished.connect(self._size_done)
        f.addRow("Diagonal", self.size)
        aa = QHBoxLayout()
        self.aa_w = mm_spin(0, 3000, 0.1, 2, special="auto")
        self.aa_h = mm_spin(0, 3000, 0.1, 2, special="auto")
        for s in (self.aa_w, self.aa_h):
            s.valueChanged.connect(self._aa_changed)
        aa.addWidget(self.aa_w)
        aa.addWidget(QLabel("×"))
        aa.addWidget(self.aa_h)
        lab = QLabel("Lit area")
        lab.setToolTip("Measured width × height of the lit picture area (landscape), mm. Leave on auto to use "
                       "the model or diagonal.")
        f.addRow(lab, aa)
        self.res = QComboBox()
        self.res.setEditable(True)
        self.res.addItems([""] + RESOLUTIONS)
        self.res.lineEdit().editingFinished.connect(self._res_done)
        self.res.activated.connect(lambda _i: self._res_done())
        f.addRow("Resolution", self.res)
        self.rotation = _combo(ROTATION_LABELS)
        self.rotation.currentIndexChanged.connect(
            lambda _i: self._emit({"rotation": self.rotation.currentData(), "orientation": None}, True))
        f.addRow("Rotation", self.rotation)
        outer.addWidget(g)

        # --- bezels
        g = QGroupBox("Bezels (border around the lit area, as mounted)")
        grid = QGridLayout(g)
        self.bezel_own = QCheckBox("Use my measurements")
        self.bezel_own.toggled.connect(self._bezel_toggled)
        grid.addWidget(self.bezel_own, 0, 0, 1, 4)
        self.bz = {}
        for i, side in enumerate(("top", "right", "bottom", "left")):
            s = mm_spin(0, 200, 0.5, 1)
            s.valueChanged.connect(self._bezel_changed)
            self.bz[side] = s
            grid.addWidget(QLabel(side.title()), 1 + i // 2, (i % 2) * 2)
            grid.addWidget(s, 1 + i // 2, (i % 2) * 2 + 1)
        self.bezel_note = QLabel()
        self.bezel_note.setWordWrap(True)
        self.bezel_note.setStyleSheet("color: palette(placeholder-text)")
        grid.addWidget(self.bezel_note, 3, 0, 1, 4)
        outer.addWidget(g)

        # --- placement
        self.place_box = QGroupBox("Position")
        pl = QVBoxLayout(self.place_box)
        self.anchor_note = QLabel("This is the anchor: the other monitors are placed around it. "
                                  "Reorder the list to choose another anchor.")
        self.anchor_note.setWordWrap(True)
        pl.addWidget(self.anchor_note)
        self.place_form = QWidget()
        f = QFormLayout(self.place_form)
        f.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.mode = _combo(PLACEMENT)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.target = QComboBox()
        self.target.currentIndexChanged.connect(self._target_changed)
        row.addWidget(self.mode)
        row.addWidget(self.target, 1)
        f.addRow("Placed", row)
        self.gap = mm_spin(-200, 2000)
        self.gap.setToolTip("Space between the two housings (negative if they overlap)")
        self.gap.valueChanged.connect(lambda v: self._emit({"gap": number(v)}))
        self.gap_label = QLabel("Gap")
        f.addRow(self.gap_label, self.gap)
        # side by side: vertical offset
        self.v_row = QWidget()
        vr = QHBoxLayout(self.v_row)
        vr.setContentsMargins(0, 0, 0, 0)
        self.v_align = _combo(V_ALIGN)
        self.v_align.currentIndexChanged.connect(lambda _i: self._emit({"v_align": self.v_align.currentData()}))
        self.v_pos = mm_spin()
        self.v_pos.setToolTip("How far this monitor's aligned edge is above the other's (+ up, − down)")
        self.v_pos.valueChanged.connect(lambda v: self._emit({"v_pos": number(v)}))
        vr.addWidget(self.v_align)
        vr.addWidget(QLabel("raised by"))
        vr.addWidget(self.v_pos)
        self.v_label = QLabel("Line up")
        f.addRow(self.v_label, self.v_row)
        self.v_ref = QComboBox()
        self.v_ref.currentIndexChanged.connect(lambda _i: self._emit({"v_ref": self.v_ref.currentData()}))
        self.v_ref_label = QLabel("…measured from")
        f.addRow(self.v_ref_label, self.v_ref)
        # stacked: horizontal offset
        self.h_row = QWidget()
        hr = QHBoxLayout(self.h_row)
        hr.setContentsMargins(0, 0, 0, 0)
        self.h_align = _combo(H_ALIGN)
        self.h_align.currentIndexChanged.connect(lambda _i: self._emit({"h_align": self.h_align.currentData()}))
        self.h_pos = mm_spin()
        self.h_pos.setToolTip("How far this monitor's aligned edge is right of the other's (+ right, − left)")
        self.h_pos.valueChanged.connect(lambda v: self._emit({"h_pos": number(v)}))
        hr.addWidget(self.h_align)
        hr.addWidget(QLabel("shifted right by"))
        hr.addWidget(self.h_pos)
        self.h_label = QLabel("Line up")
        f.addRow(self.h_label, self.h_row)
        self.h_ref = QComboBox()
        self.h_ref.currentIndexChanged.connect(lambda _i: self._emit({"h_ref": self.h_ref.currentData()}))
        self.h_ref_label = QLabel("…measured from")
        f.addRow(self.h_ref_label, self.h_ref)
        # fixed position
        self.pos_row = QWidget()
        pr = QHBoxLayout(self.pos_row)
        pr.setContentsMargins(0, 0, 0, 0)
        self.pos_x = mm_spin(-10000, 10000)
        self.pos_y = mm_spin(-10000, 10000)
        for s in (self.pos_x, self.pos_y):
            s.valueChanged.connect(lambda _v: self._emit(
                {"position": [number(self.pos_x.value()), number(self.pos_y.value())]}))
        pr.addWidget(QLabel("x"))
        pr.addWidget(self.pos_x)
        pr.addWidget(QLabel("y"))
        pr.addWidget(self.pos_y)
        self.pos_label = QLabel("Top-left")
        f.addRow(self.pos_label, self.pos_row)
        pl.addWidget(self.place_form)
        outer.addWidget(self.place_box)

        # --- matching to the desktop's outputs
        g = QGroupBox("Which desktop output is this? (optional)")
        f = QFormLayout(g)
        self.connector = QLineEdit()
        self.connector.setPlaceholderText("e.g. DP-2 (normally found automatically)")
        self.connector.editingFinished.connect(lambda: self._emit({"connector": self.connector.text().strip() or None}))
        f.addRow("Connector", self.connector)
        self.serial = QLineEdit()
        self.serial.setPlaceholderText("pins this entry to one physical monitor")
        self.serial.editingFinished.connect(lambda: self._emit({"serial": self.serial.text().strip() or None}))
        f.addRow("Serial", self.serial)
        outer.addWidget(g)

        self.notes = QLabel()
        self.notes.setWordWrap(True)
        self.notes.setStyleSheet("color: palette(placeholder-text)")
        outer.addWidget(self.notes)
        outer.addStretch(1)

    # ------------------------------------------------------------------ populate
    def populate(self, index: int, entry: dict, mon, names: list[str]) -> None:
        """Show ``entry`` (plain dict); ``mon`` is the solved Monitor (or None if invalid)."""
        self._loading = True
        try:
            self.index, self.entry = index, entry
            self.name.setText(str(entry.get("name", "")))
            known = models.lookup(entry.get("model"))
            self.model.setEditText(known["model"].title() if known else str(entry.get("model") or ""))
            self.size.setText("" if entry.get("size") is None else str(entry["size"]))
            self.size.setPlaceholderText("from the model" if known else "e.g. 27in")
            aa = entry.get("active_area")
            try:
                w, h = sorted((parse_length(aa[0]), parse_length(aa[1])), reverse=True) if aa else (0, 0)
            except (ValueError, TypeError, IndexError):
                w, h = 0, 0
            self.aa_w.setValue(w)
            self.aa_h.setValue(h)
            self.res.setEditText("" if entry.get("resolution") is None else str(entry["resolution"]))
            self.res.lineEdit().setPlaceholderText(
                "x".join(map(str, known["resolution"])) + " (from the model)" if known else "e.g. 2560x1440")
            rot = mon.rotation if mon else 0
            _select(self.rotation, ROTATION_NAMES[rot])
            self.bezel_own.setChecked(entry.get("bezel") is not None)
            self.refresh_derived(mon)

            first = index == 0
            self.anchor_note.setVisible(first)
            self.place_form.setVisible(not first)
            others = [n for n in names if n != entry.get("name")]
            mode = next((k for k in ATTACH if entry.get(k) is not None), None)
            if mode is None and entry.get("position") is not None:
                mode = "position"
            target = str(entry.get(mode)) if mode in ATTACH else None
            if mode is None:  # default: right of the previous monitor
                mode, target = "right_of", names[index - 1] if index > 0 else None
            _select(self.mode, mode)
            self.target.clear()
            for n in others:
                self.target.addItem(n, n)
            _select(self.target, target)
            for combo, key in ((self.v_ref, "v_ref"), (self.h_ref, "h_ref")):
                combo.clear()
                combo.addItem("the one it's next to" if key == "v_ref" else "the one it's above/below", None)
                for n in others:
                    combo.addItem(n, n)
                _select(combo, entry.get(key))
            self.gap.setValue(mon.gap if mon else _num(entry.get("gap")))
            _select(self.v_align, mon.v_align if mon else "center")
            _select(self.h_align, mon.h_align if mon else "center")
            self.v_pos.setValue(mon.v_pos if mon else _num(entry.get("v_pos")))
            self.h_pos.setValue(mon.h_pos if mon else _num(entry.get("h_pos")))
            pos = entry.get("position") or (0, 0)
            self.pos_x.setValue(_num(pos[0]))
            self.pos_y.setValue(_num(pos[1]))
            self._show_mode(mode)
            self.connector.setText(str(entry.get("connector") or ""))
            self.serial.setText(str(entry.get("serial") or ""))
        finally:
            self._loading = False

    def refresh_derived(self, mon) -> None:
        """Values that follow from other fields (bezels from the model and rotation, notes)."""
        was = self._loading
        self._loading = True
        try:
            own = self.bezel_own.isChecked()
            for side, s in self.bz.items():
                if mon is not None and not (own and s.hasFocus()):
                    s.setValue(mon.bezel[side])
                s.setEnabled(own)
            known = models.lookup(self.entry.get("model"))
            if own:
                self.bezel_note.setText("")
            elif known:
                est = " (estimated from the spec sheet: measure them for the best result)" \
                    if known.get("bezel_estimated") else ""
                self.bezel_note.setText(f"From the {known['model'].title()} spec{est}.")
            else:
                self.bezel_note.setText("No model selected: bezels count as 0 unless you measure them.")
            self.notes.setText("\n".join(f"• {n}" for n in (mon.notes if mon else []) if "bezel" not in n))
        finally:
            self._loading = was

    def _show_mode(self, mode: str) -> None:
        side = mode in ("right_of", "left_of")
        stacked = mode in ("below", "above")
        self.target.setVisible(mode in ATTACH)
        for w in (self.gap, self.gap_label):
            w.setVisible(mode in ATTACH)
        for w in (self.v_row, self.v_label, self.v_ref, self.v_ref_label):
            w.setVisible(side)
        for w in (self.h_row, self.h_label, self.h_ref, self.h_ref_label):
            w.setVisible(stacked)
        for w in (self.pos_row, self.pos_label):
            w.setVisible(mode == "position")

    # ------------------------------------------------------------------ edits
    def _emit(self, values: dict, structural: bool = False) -> None:
        if not self._loading and self.index >= 0:
            self.edit.emit(values, structural)

    def _name_done(self):
        new = self.name.text().strip()
        if not self._loading and new and new != self.entry.get("name"):
            self.rename.emit(new)

    def _model_done(self):
        text = self.model.currentText().strip()
        old = str(self.entry.get("model") or "")
        if self._loading or text == old or (models.lookup(text) and models.lookup(text) == models.lookup(old)):
            return
        self._emit({"model": text or None}, True)

    def _size_done(self):
        text = self.size.text().strip()
        if self._loading or text == str(self.entry.get("size") or ""):
            return
        if text:
            try:
                parse_length(text, default_unit="in")
            except ValueError as e:
                error(self, "Diagonal", str(e))
                return
        self._emit({"size": (number(float(text)) if _is_number(text) else text) or None}, True)

    def _aa_changed(self, _v):
        w, h = self.aa_w.value(), self.aa_h.value()
        if w > 0 and h > 0:
            self._emit({"active_area": [number(w), number(h)]})
        elif self.entry.get("active_area") is not None and (w == 0 and h == 0):
            self._emit({"active_area": None})

    def _res_done(self):
        text = self.res.currentText().strip()
        if self._loading or text == str(self.entry.get("resolution") or ""):
            return
        if text:
            try:
                parse_resolution(text)
            except ValueError as e:
                error(self, "Resolution", str(e))
                return
        self._emit({"resolution": text or None}, True)

    def _bezel_toggled(self, on: bool):
        if on:
            self._bezel_changed()
        else:
            self._emit({"bezel": None}, True)

    def _bezel_changed(self, _v=None):
        if self.bezel_own.isChecked():
            self._emit({"bezel": {s: number(self.bz[s].value()) for s in ("top", "right", "bottom", "left")}})

    def _mode_changed(self, _i):
        if self._loading:
            return
        mode = self.mode.currentData()
        self._show_mode(mode)
        values = {k: None for k in (*ATTACH, "position")}
        if mode == "position":
            values["position"] = [number(self.pos_x.value()), number(self.pos_y.value())]
        else:
            values[mode] = self.target.currentData() or (self.target.itemData(0) if self.target.count() else None)
            # offsets along the other axis don't carry over
            if mode in ("below", "above"):
                values.update(v_pos=None, v_align=None, v_ref=None)
            else:
                values.update(h_pos=None, h_align=None, h_ref=None)
        self._emit(values, True)

    def _target_changed(self, _i):
        mode = self.mode.currentData()
        if mode in ATTACH and self.target.currentData():
            self._emit({k: None for k in (*ATTACH, "position")} | {mode: self.target.currentData()})


def _num(v, default: float = 0.0) -> float:
    try:
        return parse_length(v) if v is not None else default
    except (ValueError, TypeError):
        return default


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


# =========================================================================== dialogs

class NewConfigDialog(QDialog):
    def __init__(self, parent, existing: list[str], current: str | None, can_detect: bool):
        super().__init__(parent)
        self.setWindowTitle("New layout")
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. home_desk")
        f.addRow("Name", self.name)
        lay.addLayout(f)
        self.group = QButtonGroup(self)
        self.detect = QRadioButton("Start from the monitors connected now (recommended)")
        self.detect.setEnabled(can_detect)
        if not can_detect:
            self.detect.setToolTip("No supported desktop detected")
        self.copy = QRadioButton(f"Copy of “{current}”" if current else "Copy of the current layout")
        self.copy.setEnabled(bool(current))
        self.blank = QRadioButton("One monitor, fill in the rest by hand")
        for i, b in enumerate((self.detect, self.copy, self.blank)):
            self.group.addButton(b, i)
            lay.addWidget(b)
        (self.detect if can_detect else self.copy if current else self.blank).setChecked(True)
        self.existing = existing
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _ok(self):
        if not self.name.text().strip():
            error(self, "New layout", "Give the layout a name.")
            return
        if self.name.text().strip() in self.existing:
            error(self, "New layout", "A layout with that name already exists.")
            return
        self.accept()

    @property
    def source(self) -> str:
        return ("detect", "copy", "blank")[self.group.checkedId()]


# =========================================================================== editor

class LayoutEditor(QWidget):
    message = Signal(str)            # for the status bar
    config_chosen = Signal(str)      # the user picked / created a configuration

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.name: str | None = None
        self.path: Path | None = None
        self.doc: CommentedMap | None = None
        self.dirty = False
        self.cfg = None
        self.layout_ = None
        self.sel = 0
        self._backend = None
        self._backend_checked = False
        self._own_mtime = None
        self._pictures: dict[str, QImage] = {}
        self._calib_busy = False
        self._calib_pending = False

        root = QVBoxLayout(self)

        # --- configuration bar
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Layout:"))
        self.combo = QComboBox()
        self.combo.setMinimumWidth(220)
        self.combo.activated.connect(lambda _i: self.choose(self.combo.currentText()))
        bar.addWidget(self.combo)
        for text, slot in (("New…", self.new_config), ("Rename…", self.rename_config),
                           ("Delete…", self.delete_config)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            bar.addWidget(b)
        self.used_by = QLabel()
        self.used_by.setStyleSheet("color: palette(placeholder-text)")
        bar.addWidget(self.used_by, 1)
        root.addLayout(bar)

        # --- monitors | desk | form
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("Monitors"))
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self.select)
        ll.addWidget(self.list, 1)
        btns = QGridLayout()
        for i, (text, tip, slot) in enumerate((
                ("Add", "Add a monitor to the right of the selected one", self.add_monitor),
                ("Remove", "Remove the selected monitor", self.remove_monitor),
                ("Move up", "Earlier in the list (the first one is the anchor)", lambda: self.move_monitor(-1)),
                ("Move down", "Later in the list", lambda: self.move_monitor(1)))):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            btns.addWidget(b, i // 2, i % 2)
        ll.addLayout(btns)
        split.addWidget(left)

        mid = QWidget()
        ml = QVBoxLayout(mid)
        ml.setContentsMargins(0, 0, 0, 0)
        self.desk = DeskView()
        self.desk.selected.connect(lambda i: self.list.setCurrentRow(i))
        self.desk.nudged.connect(self.nudge)
        self.desk.resized.connect(lambda: self._recompute_timer.start())
        ml.addWidget(self.desk, 1)
        prow = QHBoxLayout()
        prow.addWidget(QLabel("Show:"))
        self.preview = QComboBox()
        self.preview.setMinimumWidth(200)
        self.preview.currentIndexChanged.connect(lambda _i: self.update_preview())
        prow.addWidget(self.preview)
        prow.addStretch(1)
        self.calib = QCheckBox("Calibrate on my screens")
        self.calib.setToolTip("Put a millimetre test pattern on your real monitors and update it as you edit.\n"
                              "Lines should run straight across the bezels; drag monitors or adjust the "
                              "numbers until they do. Your wallpaper comes back when you switch this off.")
        self.calib.toggled.connect(self.toggle_calibration)
        prow.addWidget(self.calib)
        ml.addLayout(prow)
        split.addWidget(mid)

        self.form = MonitorForm()
        self.form.edit.connect(self.apply_edit)
        self.form.rename.connect(self.rename_monitor)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(self.form)
        scroll.setMinimumWidth(380)
        split.addWidget(scroll)
        split.setStretchFactor(1, 1)
        split.setSizes([170, 700, 400])
        root.addWidget(split, 1)

        # --- problems + save
        bottom = QHBoxLayout()
        self.problems = QLabel()
        self.problems.setWordWrap(True)
        self.problems.setTextFormat(Qt.RichText)
        self.problems.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bottom.addWidget(self.problems, 1)
        self.guide = QPushButton("How to calibrate")
        self.guide.clicked.connect(self.show_guide)
        bottom.addWidget(self.guide)
        self.revert_btn = QPushButton("Revert")
        self.revert_btn.clicked.connect(self.revert)
        bottom.addWidget(self.revert_btn)
        self.save_btn = QPushButton("Save")
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self.save)
        bottom.addWidget(self.save_btn)
        root.addLayout(bottom)
        QShortcut(QKeySequence.Save, self, activated=self.save)

        self._recompute_timer = QTimer(self, singleShot=True, interval=120, timeout=self._refresh_pattern)
        self._calib_timer = QTimer(self, singleShot=True, interval=350, timeout=self._calibrate_now)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self._file_changed)
        self.watcher.directoryChanged.connect(lambda _p: self.refresh_names())
        if self.project.configurations.is_dir():
            self.watcher.addPath(str(self.project.configurations))
        if self.project.pictures.is_dir():
            self.watcher.addPath(str(self.project.pictures))
        self.refresh_names()
        self._fill_preview_choices()
        self._update_buttons()

    # ------------------------------------------------------------------ backend
    def backend(self):
        if not self._backend_checked:
            self._backend_checked = True
            try:
                self._backend = backends.detect()
            except Exception:  # noqa: BLE001
                self._backend = None
        return self._backend

    # ------------------------------------------------------------------ configurations
    def refresh_names(self) -> None:
        names = self.project.config_names()
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItems(names)
        if self.name in names:
            self.combo.setCurrentText(self.name)
        self.combo.blockSignals(False)
        self._fill_preview_choices()

    def show_config(self, name: str | None) -> None:
        """Load a configuration without telling the setup (used when the setup changes)."""
        if name:
            name = Path(str(name)).stem
        names = self.project.config_names()
        if not name or name not in names:
            name = names[0] if names else None
        if name == self.name and self.doc is not None:
            return
        if not self.maybe_save():
            self.combo.setCurrentText(self.name or "")
            return
        self.load(name)

    def choose(self, name: str) -> None:
        if name == self.name:
            return
        if not self.maybe_save():
            self.combo.setCurrentText(self.name or "")
            return
        self.load(name)
        self.config_chosen.emit(name)

    def load(self, name: str | None) -> None:
        if self.path and str(self.path) in self.watcher.files():
            self.watcher.removePath(str(self.path))
        self.name, self.path, self.doc = name, None, None
        if name:
            try:
                self.path = self.project.config_path(name)
                self.doc = yamledit.load(self.path)
            except Exception as e:  # noqa: BLE001
                error(self, "Layout", f"Could not read {name}: {e}")
                self.doc = None
            else:
                self._normalise_doc()
                self.watcher.addPath(str(self.path))
                self._own_mtime = self.path.stat().st_mtime_ns
        self.dirty = False
        self.refresh_names()
        self.sel = 0
        self._refill_list()
        self.recompute(structural=True)
        self._update_used_by()
        self._update_buttons()

    def _normalise_doc(self) -> None:
        mons = self.doc.get("monitors")
        if isinstance(mons, dict):  # legacy numbered form -> list
            numbered = {k: v for k, v in mons.items() if isinstance(v, dict)}
            self.doc["monitors"] = CommentedSeq([numbered[k] for k in sorted(numbered, key=lambda x: int(x))])
            self.dirty = True
        elif mons is None:
            self.doc["monitors"] = CommentedSeq()
        for i, e in enumerate(self.doc["monitors"]):
            if e is None:
                self.doc["monitors"][i] = e = CommentedMap()
            if not e.get("name"):
                put(e, "name", f"monitor{i + 1}")

    def _update_used_by(self) -> None:
        if not self.name:
            self.used_by.setText("")
            return
        used = self.project.setups_using(self.name)
        if not used:
            self.used_by.setText("  not used by any setup")
        else:
            self.used_by.setText("  used by setup" + ("s " if len(used) > 1 else " ") + ", ".join(used))

    def new_config(self) -> None:
        if not self.maybe_save():
            return
        be = self.backend()
        dlg = NewConfigDialog(self, self.project.config_names(), self.name, be is not None)
        if dlg.exec() != QDialog.Accepted:
            return
        name = dlg.name.text().strip()
        try:
            path = self.project.new_config_path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            if dlg.source == "detect":
                self.project.write_detected_config(name, be.get_layout().monitors)
            elif dlg.source == "copy" and self.path:
                path.write_text(self.path.read_text())
            else:
                doc = CommentedMap(monitors=CommentedSeq([CommentedMap(
                    name="main", resolution="1920x1080", size="24in")]))
                yamledit.save(path, doc)
        except Exception as e:  # noqa: BLE001
            error(self, "New layout", str(e))
            return
        self.refresh_names()
        self.load(name)
        self.config_chosen.emit(name)
        self.message.emit(f"created layout {name}")

    def rename_config(self) -> None:
        if not self.name or not self.maybe_save():
            return
        new, ok = QInputDialog.getText(self, "Rename layout", "New name:", text=self.name)
        if not ok or not new.strip() or new.strip() == self.name:
            return
        try:
            self.project.rename_config(self.name, new.strip())
        except ProjectError as e:
            error(self, "Rename layout", str(e))
            return
        self.load(new.strip())
        self.config_chosen.emit(new.strip())
        self.message.emit(f"renamed layout to {new.strip()} (setups updated)")

    def delete_config(self) -> None:
        if not self.name or not self.path:
            return
        used = self.project.setups_using(self.name)
        text = f"Move the layout “{self.name}” to the trash?"
        if used:
            text += f"\n\nIt is used by: {', '.join(used)}. They will need another layout."
        if not confirm(self, "Delete layout", text, "Move to trash"):
            return
        if not QFile.moveToTrash(str(self.path)):
            self.path.unlink()
        self.dirty = False
        rest = self.project.config_names()
        self.load(rest[0] if rest else None)
        if self.name:
            self.config_chosen.emit(self.name)
        self.message.emit("layout moved to the trash")

    def maybe_save(self) -> bool:
        """Ask about unsaved changes. False = cancel."""
        if not self.dirty:
            return True
        box = QMessageBox(QMessageBox.Question, "Unsaved changes",
                          f"Save the changes to “{self.name}”?", parent=self)
        box.setStandardButtons(QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        r = box.exec()
        if r == QMessageBox.Save:
            return self.save()
        if r == QMessageBox.Discard:
            self.dirty = False
            return True
        return False

    def save(self) -> bool:
        if self.doc is None or not self.path:
            return False
        try:
            yamledit.save(self.path, self.doc)
        except OSError as e:
            error(self, "Save", str(e))
            return False
        self._own_mtime = self.path.stat().st_mtime_ns
        if str(self.path) not in self.watcher.files():
            self.watcher.addPath(str(self.path))
        self.dirty = False
        self._update_buttons()
        self._show_problems()
        self.message.emit(f"saved {self.path.name}")
        return True

    def revert(self) -> None:
        if self.dirty and not confirm(self, "Revert", "Throw away the unsaved changes?", "Revert"):
            return
        self.dirty = False
        self.load(self.name)

    def _file_changed(self, path: str) -> None:
        p = Path(path)
        if p.exists() and str(p) not in self.watcher.files():
            self.watcher.addPath(path)  # saved by replacing the file
        if not self.path or p != self.path or not p.exists():
            return
        if p.stat().st_mtime_ns == self._own_mtime:
            return
        if self.dirty:
            self.message.emit(f"{p.name} changed on disk; your unsaved edits are kept (Revert to load it)")
            return
        sel = self.sel
        self.load(self.name)
        self.list.setCurrentRow(min(sel, self.list.count() - 1))
        self.message.emit(f"reloaded {p.name} (changed on disk)")

    # ------------------------------------------------------------------ monitors
    def monitors(self) -> CommentedSeq:
        return self.doc["monitors"] if self.doc is not None else CommentedSeq()

    def names(self) -> list[str]:
        return [str(e.get("name")) for e in self.monitors()]

    def _refill_list(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for i, n in enumerate(self.names()):
            self.list.addItem(f"⚓ {n}" if i == 0 else n)
        self.sel = min(self.sel, self.list.count() - 1)
        if self.sel >= 0:
            self.list.setCurrentRow(self.sel)
        self.list.blockSignals(False)

    def select(self, i: int) -> None:
        if i < 0:
            return
        self.sel = i
        self.desk.set_selected(i)
        self._populate_form()

    def _populate_form(self) -> None:
        mons = self.monitors()
        if not (0 <= self.sel < len(mons)):
            self.form.setEnabled(False)
            return
        self.form.setEnabled(True)
        mon = self.cfg.monitors[self.sel] if self.cfg and self.sel < len(self.cfg.monitors) else None
        self.form.populate(self.sel, yamledit.plain(mons[self.sel]), mon, self.names())

    def apply_edit(self, values: dict, structural: bool = False) -> None:
        e = self.monitors()[self.sel]
        for k, v in values.items():
            if v is None:
                e.pop(k, None)
            else:
                put(e, k, v)
        self.form.entry = yamledit.plain(e)
        self._changed(structural)

    def _changed(self, structural: bool = False) -> None:
        self.dirty = True
        self.recompute(structural)
        self._update_buttons()

    def rename_monitor(self, new: str) -> None:
        old = str(self.monitors()[self.sel].get("name"))
        if new in self.names():
            error(self, "Rename monitor", f"There is already a monitor called “{new}”.")
            self.form.name.setText(old)
            return
        for e in self.monitors():
            for k in REFERENCE_KEYS:
                if str(e.get(k)) == old:
                    e[k] = new
        self.monitors()[self.sel]["name"] = new
        self._refill_list()
        self._changed(True)

    def add_monitor(self) -> None:
        if self.doc is None:
            return
        names = self.names()
        n = len(names) + 1
        while f"monitor{n}" in names:
            n += 1
        e = CommentedMap(name=f"monitor{n}", resolution="1920x1080", size="24in")
        if names:
            e["right_of"] = names[self.sel] if 0 <= self.sel < len(names) else names[-1]
            e["gap"] = 0
        self.monitors().append(e)
        self.sel = len(self.monitors()) - 1
        self._refill_list()
        self._changed(True)
        self.message.emit("added a monitor: set its model or size and resolution")

    def remove_monitor(self) -> None:
        mons = self.monitors()
        if not (0 <= self.sel < len(mons)) or len(mons) <= 1:
            return
        gone = mons[self.sel]
        name = str(gone.get("name"))
        if not confirm(self, "Remove monitor", f"Remove “{name}” from this layout?", "Remove"):
            return
        # monitors placed relative to it are re-attached to what it was attached to
        parent = next(((k, gone[k]) for k in ATTACH if gone.get(k) is not None), None)
        del mons[self.sel]
        for e in mons:
            for k in REFERENCE_KEYS:
                if str(e.get(k)) == name:
                    if k in ATTACH and parent:
                        e.pop(k)
                        put(e, parent[0] if k == parent[0] else k, parent[1])
                    else:
                        e.pop(k)
        self._fix_anchor()
        self.sel = min(self.sel, len(mons) - 1)
        self._refill_list()
        self._changed(True)

    def move_monitor(self, step: int) -> None:
        mons = self.monitors()
        i, j = self.sel, self.sel + step
        if not (0 <= i < len(mons) and 0 <= j < len(mons)):
            return
        mons[i], mons[j] = mons[j], mons[i]
        self._fix_anchor()
        self.sel = j
        self._refill_list()
        self._changed(True)

    def _fix_anchor(self) -> None:
        """The first monitor can't be attached to another: hand its placement over."""
        mons = self.monitors()
        if not mons:
            return
        first = mons[0]
        att = next((k for k in ATTACH if first.get(k) is not None), None)
        if att is None:
            return
        target = str(first.pop(att))
        flip = {"right_of": "left_of", "left_of": "right_of", "below": "above", "above": "below"}
        for e in mons[1:]:
            if str(e.get("name")) == target and not any(e.get(k) is not None for k in ATTACH):
                put(e, flip[att], str(first.get("name")))
                if "gap" in first:
                    put(e, "gap", first["gap"])
                break
        for k in ("gap", "v_pos", "v_align", "v_ref", "h_pos", "h_align", "h_ref"):
            first.pop(k, None)
        self.message.emit(f"{first.get('name')} is now the anchor; check the placement of the others")

    def nudge(self, i: int, dx: float, dy: float) -> None:
        """A drag / arrow key moved monitor i by (dx, dy) mm (y down)."""
        if self.cfg is None or not (0 < i < len(self.monitors())):
            return
        e = self.monitors()[i]
        mode = next((k for k in ATTACH if e.get(k) is not None), None)
        if mode is None and e.get("position") is not None:
            x, y = e["position"]
            put(e, "position", CommentedSeq([number(_num(x) + dx), number(_num(y) + dy)]))
        else:
            mode = mode or "right_of"
            gap = _num(e.get("gap"), self.cfg.monitors[i].gap)
            if mode in ("right_of", "left_of"):
                put(e, "gap", number(gap + (dx if mode == "right_of" else -dx)))
                put(e, "v_pos", number(_num(e.get("v_pos")) - dy))
            else:
                put(e, "gap", number(gap + (dy if mode == "below" else -dy)))
                put(e, "h_pos", number(_num(e.get("h_pos")) + dx))
        if self.sel != i:
            self.list.setCurrentRow(i)
        self._changed(False)
        self._populate_form()

    # ------------------------------------------------------------------ solve + show
    def recompute(self, structural: bool = False) -> None:
        self.cfg, self.layout_, err = None, None, ""
        if self.doc is not None and len(self.monitors()):
            try:
                cfg = configuration_from_dict(yamledit.plain(self.doc), name=self.name or "config", path=self.path)
                cfg.name = self.name or "config"
                self.layout_ = compute_layout(cfg)
                self.cfg = cfg
            except (ConfigError, ProjectError, ValueError, KeyError, TypeError) as e:
                err = str(e)
        if self.layout_ is not None:
            self.desk.set_layout(self.layout_)
            self._last_good = self.layout_
        else:
            self.desk.set_layout(getattr(self, "_last_good", None), valid=False,
                                 error=err or "no monitors")
        self.desk.set_selected(self.sel)
        self._error = err
        self._show_problems()
        if structural:
            self._populate_form()
        elif self.cfg is not None and self.sel < len(self.cfg.monitors):
            self.form.refresh_derived(self.cfg.monitors[self.sel])
        if self.preview.currentData() == "pattern":
            self._recompute_timer.start()
        if self.calib.isChecked() and self.cfg is not None:
            self._calib_timer.start()

    def _show_problems(self) -> None:
        if self.doc is None:
            self.problems.setText("No layout yet: press New… to make one from your monitors.")
            return
        if self._error:
            self.problems.setText(f"<span style='color:#e06040'>⚠ {self._error}</span>")
            return
        notes, estimated = [], []
        for m in self.cfg.monitors if self.cfg else []:
            for n in m.notes:
                if "bezels are estimated" in n:
                    estimated.append(m.name)
                else:
                    notes.append(f"{m.name}: {n}")
        if estimated:
            notes.append(f"bezels of {', '.join(estimated)} are estimated from spec sheets: "
                         "measure them, or calibrate")
        text = "✓ Valid" + (" · unsaved changes" if self.dirty else "")
        if notes:
            text += "<br><span style='color:gray'>" + "<br>".join(notes[:4]) + "</span>"
        self.problems.setText(text)

    def _update_buttons(self) -> None:
        self.save_btn.setEnabled(self.dirty)
        self.revert_btn.setEnabled(self.dirty)
        has = self.doc is not None
        for w in (self.list, self.form, self.calib):
            w.setEnabled(has)

    # ------------------------------------------------------------------ preview picture
    def _fill_preview_choices(self) -> None:
        current = self.preview.currentData()
        self.preview.blockSignals(True)
        self.preview.clear()
        self.preview.addItem("Monitors only", None)
        self.preview.addItem("Calibration pattern", "pattern")
        for p in self.project.all_pictures():
            self.preview.addItem(p.name, str(p))
        i = self.preview.findData(current)
        self.preview.setCurrentIndex(max(i, 0))
        self.preview.blockSignals(False)

    def update_preview(self) -> None:
        data = self.preview.currentData()
        if data is None:
            self.desk.set_image(None)
        elif data == "pattern":
            self._refresh_pattern()
        else:
            img = self._pictures.get(data)
            if img is None:
                reader = QImageReader(data)
                reader.setAutoTransform(True)  # EXIF rotation
                size = reader.size()
                if size.isValid() and size.width() > 2400:  # decode small (fast; avoids Qt's size limit)
                    reader.setScaledSize(size.scaled(2400, 2400, Qt.KeepAspectRatio))
                img = reader.read()
                if img.isNull():  # formats Qt can't read: go through Pillow
                    try:
                        from ..render import load_image
                        pil = load_image(Path(data))
                        pil.thumbnail((2400, 2400))
                        img = pil_to_qimage(pil)
                    except Exception:  # noqa: BLE001
                        img = QImage()
                elif img.width() > 2400:
                    img = img.scaledToWidth(2400, Qt.SmoothTransformation)
                self._pictures[data] = img
            crop = None
            if self.cfg is not None:
                try:
                    from ..project import ImageJob
                    crop = self.project.crop_for(self.cfg, ImageJob(Path(data)))
                except (ValueError, ProjectError):
                    crop = None
            self.desk.set_image(img, crop=crop)

    def _refresh_pattern(self) -> None:
        if self.preview.currentData() != "pattern" or self.layout_ is None:
            return
        from .. import calibrate
        c = self.layout_.canvas
        k = max(0.3, min(self.desk.px_per_mm(), 4000 / max(c.w, 1)))
        self.desk.set_image(pil_to_qimage(calibrate.preview(self.layout_, px_per_mm=k)), pattern=True)

    # ------------------------------------------------------------------ calibration
    def toggle_calibration(self, on: bool) -> None:
        if on:
            if self.backend() is None:
                self.calib.blockSignals(True)
                self.calib.setChecked(False)
                self.calib.blockSignals(False)
                error(self, "Calibrate", "No supported desktop found (GNOME, KDE Plasma, sway, Hyprland).")
                return
            if self.cfg is None:
                self.message.emit("calibration starts once the layout is valid")
            self._calibrate_now()
        elif not self._calib_busy:
            self._end_calibration()

    def _calibrate_now(self) -> None:
        if not self.calib.isChecked() or self.cfg is None:
            return
        if self._calib_busy:
            self._calib_pending = True
            return
        self._calib_busy, self._calib_pending = True, False
        be, cfg, proj = self.backend(), copy.deepcopy(self.cfg), self.project

        def work():
            proj.begin_calibration(be)
            return proj.apply_calibration(be, cfg)

        run_async(work, self._calibrated, self._calibration_failed)
        self.message.emit("updating the pattern on your screens…")

    def _calibrated(self, msg: str) -> None:
        self._calib_busy = False
        self.message.emit(msg.split(" ", 1)[1] if msg[:2].isdigit() else msg)
        if not self.calib.isChecked():
            self._end_calibration()
        elif self._calib_pending:
            self._calibrate_now()

    def _calibration_failed(self, err: str) -> None:
        self._calib_busy = False
        self.calib.setChecked(False)
        error(self, "Calibrate", err)

    def _end_calibration(self) -> None:
        be, proj = self.backend(), self.project
        run_async(lambda: proj.end_calibration(be), lambda m: self.message.emit(m or "calibration off"),
                  lambda e: error(self, "Calibrate", e))

    def stop_calibration_now(self) -> None:
        """On quit: put the wallpaper back before the window goes."""
        if self.project.calibrating() and self.backend() is not None:
            try:
                self.project.end_calibration(self.backend())
            except Exception:  # noqa: BLE001
                pass

    def show_guide(self) -> None:
        from .. import calibrate
        text = calibrate.GUIDE.replace("Re-run after editing, or use --watch to re-apply on every save.",
                                       "In this window: switch on “Calibrate on my screens”, then drag the "
                                       "monitors or change the numbers; the pattern follows.")
        QMessageBox.information(self, "How to calibrate", text)
