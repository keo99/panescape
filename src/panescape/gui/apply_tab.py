"""Apply tab: the active setup's settings, what is on the desktop, the slideshow runner.

Every control writes straight to setup.yml (comments kept); nothing reaches the
desktop until Apply is pressed (or the Pictures tab updates a running wallpaper).
"""

from __future__ import annotations

import os
import shlex
import signal
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import tags as tagmod
from .. import yamledit
from ..project import Project, ProjectError, autostart_file, capture_log, frame_index, install_autostart, runner_argv
from ..units import parse_duration
from .common import error, number, run_async
from .widgets import make_thumbnail

STRIP = QSize(120, 80)
CURRENT = QSize(320, 200)
RED = "#d04030"
SETTERS = ("sway", "hyprpaper", "swww", "swaybg")


def nice_duration(secs: float) -> str:
    secs = int(round(secs))
    if secs < 60:
        return f"{secs} s"
    h, rest = divmod(secs, 3600)
    m, s = divmod(rest, 60)
    parts = ([f"{h} h"] if h else []) + ([f"{m} min"] if m else []) + ([f"{s} s"] if s and not h else [])
    return " ".join(parts)


def clock(secs: float) -> str:
    secs = max(0, int(secs))
    h, rest = divmod(secs, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _entry_name(entry) -> str:
    return str(entry.get("file") or entry.get("image") or "?") if isinstance(entry, dict) else str(entry)


def _entry_label(entry) -> str:
    name = _entry_name(entry)
    if isinstance(entry, dict):
        own = ", ".join(f"{k} {v}" for k, v in entry.items() if k not in ("file", "image"))
        return f"{name}   ({own})" if own else name
    if any(c in name for c in "*?["):
        return f"{name}   (pattern: every matching picture, including ones added later)"
    return name


class DurationEdit(QWidget):
    """A number and a unit; reads and writes setup.yml durations like 15m or 1h."""

    changed = Signal()
    UNITS = (("seconds", 1, "s"), ("minutes", 60, "m"), ("hours", 3600, "h"))

    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.num = QSpinBox()
        self.num.setRange(1, 9999)
        self.num.setKeyboardTracking(False)
        self.unit = QComboBox()
        for label, _f, _s in self.UNITS:
            self.unit.addItem(label)
        row.addWidget(self.num)
        row.addWidget(self.unit)
        row.addStretch(1)
        self.num.valueChanged.connect(lambda _v: self.changed.emit())
        self.unit.currentIndexChanged.connect(lambda _i: self.changed.emit())

    def set_value(self, value) -> None:
        try:
            secs = max(1, int(round(parse_duration(value))))
        except ValueError:
            secs = 600
        idx = 2 if secs % 3600 == 0 else 1 if secs % 60 == 0 else 0
        self.blockSignals(True)
        for w in (self.num, self.unit):
            w.blockSignals(True)
        self.unit.setCurrentIndex(idx)
        self.num.setValue(secs // self.UNITS[idx][1])
        for w in (self.num, self.unit):
            w.blockSignals(False)
        self.blockSignals(False)

    def value(self) -> str:
        return f"{self.num.value()}{self.UNITS[self.unit.currentIndex()][2]}"

    def seconds(self) -> int:
        return self.num.value() * self.UNITS[self.unit.currentIndex()][1]


class PickPictures(QDialog):
    """Choose pictures (or type a pattern) to add to a setup's list."""

    def __init__(self, names: list[str], taken: set[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add pictures")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Pick pictures (Ctrl/Shift for several):"))
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        for n in names:
            if n not in taken:
                self.list.addItem(n)
        lay.addWidget(self.list, 1)
        lay.addWidget(QLabel("…or a pattern, e.g. <code>city-*.jpg</code> (pictures added later that "
                             "match join too):"))
        self.pattern = QLineEdit()
        self.pattern.setPlaceholderText("pattern (optional)")
        lay.addWidget(self.pattern)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(420, 480)

    def chosen(self) -> list[str]:
        out = [it.text() for it in self.list.selectedItems()]
        if self.pattern.text().strip():
            out.append(self.pattern.text().strip())
        return out


class ApplyTab(QWidget):
    message = Signal(str)
    setup_edited = Signal()  # setup.yml changed from here (the window refreshes the other tabs)

    def __init__(self, project: Project, backend_getter, stop_calibration, parent=None):
        super().__init__(parent)
        self.project = project
        self.backend = backend_getter
        self.stop_calibration = stop_calibration  # () -> bool: False if it can't stop right now
        self._loaded: dict | None = None  # setup values the form shows
        self._busy = False
        self._thumb_gen = 0
        self._current_key = None
        self._state_mtime = None

        outer = QHBoxLayout(self)

        # ---------------------------------------------------------- left: the setup
        left = QVBoxLayout()
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 15px; font-weight: 600")
        left.addWidget(self.title)
        hint = QLabel("Changes are saved to setup.yml straight away and reach the desktop when you press Apply.")
        hint.setStyleSheet("color: palette(placeholder-text)")
        hint.setWordWrap(True)
        left.addWidget(hint)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.config = QComboBox()
        self.config.setToolTip("The layout (configuration) these pictures are cut for; edit it on the Layout tab")
        self.config.activated.connect(lambda _i: self._write({"configuration": self.config.currentText()}))
        form.addRow("Layout:", self.config)

        pics = QVBoxLayout()
        self.all_pics = QRadioButton("All pictures in pictures/")
        self.some_pics = QRadioButton("Only these:")
        self._pics_group = QButtonGroup(self)
        for b in (self.all_pics, self.some_pics):
            self._pics_group.addButton(b)
            pics.addWidget(b)
        self.all_pics.toggled.connect(self._pictures_mode_changed)
        self.entries = QListWidget()
        self.entries.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.entries.setMaximumHeight(130)
        self.entries.setDragDropMode(QAbstractItemView.InternalMove)
        self.entries.model().rowsMoved.connect(lambda *_a: self._write_entries())
        pics.addWidget(self.entries)
        erow = QHBoxLayout()
        self.add_entry = QPushButton("Add…")
        self.add_entry.clicked.connect(self.add_entries)
        self.remove_entry = QPushButton("Remove")
        self.remove_entry.clicked.connect(self.remove_entries)
        erow.addWidget(self.add_entry)
        erow.addWidget(self.remove_entry)
        erow.addStretch(1)
        pics.addLayout(erow)
        form.addRow("Pictures:", pics)

        frow = QVBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("no filter (e.g. landscape + water + !night)")
        self.filter.setToolTip("Only pictures whose tags match: + means and, , means or, ! means not, "
                               "* matches any tag (so !* = untagged). Tags are set on the Pictures tab.")
        self._filter_timer = QTimer(self, singleShot=True, interval=600)
        self._filter_timer.timeout.connect(self._filter_done)
        self.filter.textEdited.connect(lambda _t: (self._check_filter(), self._filter_timer.start()))
        self.filter.editingFinished.connect(self._filter_done)
        self.filter_note = QLabel()
        self.filter_note.setWordWrap(True)
        frow.addWidget(self.filter)
        frow.addWidget(self.filter_note)
        form.addRow("Tag filter:", frow)

        mrow = QHBoxLayout()
        self.single = QRadioButton("One picture")
        self.slideshow = QRadioButton("Slideshow")
        self._mode_group = QButtonGroup(self)
        for b in (self.single, self.slideshow):
            self._mode_group.addButton(b)
            mrow.addWidget(b)
        mrow.addStretch(1)
        self.slideshow.toggled.connect(self._mode_changed)
        form.addRow("Show:", mrow)

        self.interval = DurationEdit()
        self.interval.changed.connect(lambda: self._write({"interval": self.interval.value()}))
        form.addRow("Each picture for:", self.interval)
        self.transition = QDoubleSpinBox()
        self.transition.setRange(0, 60)
        self.transition.setSingleStep(0.5)
        self.transition.setDecimals(1)
        self.transition.setSuffix(" s")
        self.transition.setSpecialValueText("none")
        self.transition.setKeyboardTracking(False)
        self.transition.setMaximumWidth(120)
        self.transition.valueChanged.connect(lambda v: self._write({"transition": f"{number(v)}s"}))
        form.addRow("Cross-fade:", self.transition)
        self.shuffle = QCheckBox("Shuffle the order")
        self.shuffle.toggled.connect(lambda on: self._write({"shuffle": on}))
        form.addRow("", self.shuffle)
        left.addLayout(form)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        left.addWidget(self.summary)
        self.strip = QListWidget()
        self.strip.setViewMode(QListWidget.IconMode)
        self.strip.setFlow(QListWidget.LeftToRight)
        self.strip.setWrapping(False)
        self.strip.setIconSize(STRIP)
        self.strip.setFixedHeight(STRIP.height() + 46)
        self.strip.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.strip.setMovement(QListWidget.Static)
        self.strip.setSelectionMode(QAbstractItemView.NoSelection)
        self.strip.setFocusPolicy(Qt.NoFocus)
        left.addWidget(self.strip)

        arow = QHBoxLayout()
        self.apply_btn = QPushButton("Apply to the desktop")
        self.apply_btn.setDefault(True)
        self.apply_btn.setMinimumHeight(34)
        self.apply_btn.setStyleSheet("QPushButton { font-weight: 600; padding: 4px 18px; }")
        self.apply_btn.clicked.connect(self.apply)
        arow.addWidget(self.apply_btn)
        self.show_log = QPushButton("Details")
        self.show_log.setCheckable(True)
        self.show_log.setEnabled(False)
        arow.addWidget(self.show_log)
        arow.addStretch(1)
        left.addLayout(arow)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(160)
        self.log.setVisible(False)
        self.show_log.toggled.connect(self.log.setVisible)
        left.addWidget(self.log)
        left.addStretch(1)

        lw = QWidget()
        lw.setLayout(left)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(lw)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.viewport().setAutoFillBackground(False)
        lw.setAutoFillBackground(False)
        outer.addWidget(scroll, 3)

        # ---------------------------------------------------------- right: the desktop
        right = QVBoxLayout()
        now = QGroupBox("On the desktop now")
        nl = QVBoxLayout(now)
        self.pending = QLabel()
        self.pending.setWordWrap(True)
        self.pending.setStyleSheet("background: #fff3cd; color: #664d03; padding: 6px; border-radius: 4px")
        nl.addWidget(self.pending)
        self.current = QLabel()
        self.current.setFixedSize(CURRENT)
        self.current.setAlignment(Qt.AlignCenter)
        self.current.setStyleSheet("background: palette(alternate-base); border-radius: 4px")
        nl.addWidget(self.current, 0, Qt.AlignHCenter)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.RichText)
        nl.addWidget(self.status)
        self.countdown = QLabel()
        nl.addWidget(self.countdown)
        right.addWidget(now)

        self.runner_box = QGroupBox("Slideshow runner")
        rl = QVBoxLayout(self.runner_box)
        self.runner_text = QLabel()
        self.runner_text.setWordWrap(True)
        rl.addWidget(self.runner_text)
        rrow = QHBoxLayout()
        self.start_runner = QPushButton("Start")
        self.start_runner.clicked.connect(self.start)
        self.stop_runner = QPushButton("Stop")
        self.stop_runner.clicked.connect(self.stop)
        self.open_log = QPushButton("Open log")
        self.open_log.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(self.project.backgrounds / "slideshow.log"))))
        for b in (self.start_runner, self.stop_runner, self.open_log):
            rrow.addWidget(b)
        rrow.addStretch(1)
        rl.addLayout(rrow)
        self.autostart = QCheckBox("Start it when I log in")
        self.autostart.toggled.connect(self.set_autostart)
        rl.addWidget(self.autostart)
        self.exec_note = QLabel()
        self.exec_note.setWordWrap(True)
        rl.addWidget(self.exec_note)
        xrow = QHBoxLayout()
        self.exec_line = QLineEdit()
        self.exec_line.setReadOnly(True)
        self.copy_exec = QPushButton("Copy")
        self.copy_exec.clicked.connect(lambda: (QGuiApplication.clipboard().setText(self.exec_line.text()),
                                                self.message.emit("copied")))
        xrow.addWidget(self.exec_line, 1)
        xrow.addWidget(self.copy_exec)
        self.exec_row = QWidget()
        self.exec_row.setLayout(xrow)
        xrow.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.exec_row)
        srow = QHBoxLayout()
        srow.addWidget(QLabel("Wallpaper tool:"))
        self.setter = QComboBox()
        self.setter.setToolTip("How panescape sets the wallpaper on sway / Hyprland "
                               "(saved as 'setter' in setup.yml, for every setup)")
        self.setter.addItem("Automatic", None)
        for s in SETTERS:
            self.setter.addItem(s, s)
        self.setter.activated.connect(self._setter_chosen)
        srow.addWidget(self.setter)
        srow.addStretch(1)
        self.setter_row = QWidget()
        self.setter_row.setLayout(srow)
        srow.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.setter_row)
        right.addWidget(self.runner_box)
        right.addStretch(1)
        outer.addLayout(right, 2)

        self._tick = QTimer(self, interval=1000)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()
        self.refresh()

    # ------------------------------------------------------------------ form
    def refresh(self, force: bool = True) -> None:
        """Re-read setup.yml into the form (skipped if nothing changed and not forced)."""
        try:
            setup = yamledit.plain(self.project.load_setup())
            name = self.project.active_setup() or "default"
        except ProjectError as e:
            self.title.setText(f"<span style='color:{RED}'>{e}</span>")
            return
        if not force and setup == self._loaded:
            return
        self._loaded = setup
        self.title.setText(f"Setup “{name}”")
        widgets = (self.config, self.all_pics, self.some_pics, self.entries, self.filter, self.single,
                   self.slideshow, self.interval, self.transition, self.shuffle, self.setter)
        for w in widgets:
            w.blockSignals(True)
        self.config.clear()
        names = self.project.config_names()
        cfg = Path(str(setup.get("configuration") or "")).stem
        self.config.addItems(names if cfg in names or not cfg else [*names, cfg])
        self.config.setCurrentText(cfg)
        images = setup.get("images", "all")
        listed = isinstance(images, (list, dict)) or (isinstance(images, str) and images not in ("all", "*"))
        if isinstance(images, (str, dict)) and listed:
            images = [images]
        self.entries.clear()
        for e in images if listed else []:
            it = QListWidgetItem(_entry_label(e))
            it.setData(Qt.UserRole, e)
            if isinstance(e, dict):
                it.setToolTip("Has its own framing in setup.yml (edit it there)")
            self.entries.addItem(it)
        (self.some_pics if listed and images else self.all_pics).setChecked(True)
        self.filter.setText(str(setup.get("filter") or ""))
        mode = str(setup.get("mode", "auto")).lower()
        (self.single if mode == "single" else self.slideshow).setChecked(True)
        self.interval.set_value(setup.get("interval", "10m"))
        try:
            self.transition.setValue(parse_duration(setup.get("transition", "2s")))
        except ValueError:
            self.transition.setValue(2)
        self.shuffle.setChecked(bool(setup.get("shuffle", False)))
        idx = self.setter.findData(setup.get("setter"))
        self.setter.setCurrentIndex(max(idx, 0))
        for w in widgets:
            w.blockSignals(False)
        self._enable()
        self._check_filter()
        self.update_preview()
        self.refresh_status()

    def _enable(self) -> None:
        some = self.some_pics.isChecked()
        for w in (self.entries, self.add_entry, self.remove_entry):
            w.setVisible(some)
        show = self.slideshow.isChecked()
        for w in (self.interval, self.transition, self.shuffle):
            w.setEnabled(show)

    def _write(self, values: dict) -> None:
        try:
            self.project.update_setup(values)
            self._loaded = yamledit.plain(self.project.load_setup())
        except ProjectError as e:
            error(self, "Setup", str(e))
            self.refresh()
            return
        self.setup_edited.emit()
        self.update_preview()
        self.refresh_status()

    def _pictures_mode_changed(self, _on=None) -> None:
        self._enable()
        if self.all_pics.isChecked():
            self._write({"images": "all"})
        elif self.entries.count():
            self._write_entries()
        else:
            self.add_entries()

    def _entry_values(self) -> list:
        return [self.entries.item(i).data(Qt.UserRole) for i in range(self.entries.count())]

    def _write_entries(self) -> None:
        values = self._entry_values()
        # an empty list would mean "all pictures"; say so rather than write it
        self._write({"images": values or "all"})
        if not values:
            self.all_pics.blockSignals(True)
            self.all_pics.setChecked(True)
            self.all_pics.blockSignals(False)
            self._enable()

    def add_entries(self) -> None:
        names = [p.name for p in self.project.all_pictures()]
        taken = {_entry_name(e) for e in self._entry_values()}
        dlg = PickPictures(names, taken, self)
        if dlg.exec() != QDialog.Accepted or not dlg.chosen():
            if not self.entries.count():  # chose "Only these" but added nothing
                self.all_pics.blockSignals(True)
                self.all_pics.setChecked(True)
                self.all_pics.blockSignals(False)
                self._enable()
            return
        for n in dlg.chosen():
            it = QListWidgetItem(_entry_label(n))
            it.setData(Qt.UserRole, n)
            self.entries.addItem(it)
        self._write_entries()

    def remove_entries(self) -> None:
        for it in self.entries.selectedItems():
            self.entries.takeItem(self.entries.row(it))
        self._write_entries()

    def _check_filter(self) -> bool:
        text = self.filter.text().strip()
        try:
            _pred, terms = tagmod.compile_filter(text)
        except tagmod.FilterError as e:
            self.filter.setStyleSheet(f"QLineEdit {{ border: 1px solid {RED}; }}")
            self.filter_note.setText(f"<span style='color:{RED}'>{e}</span>")
            return False
        self.filter.setStyleSheet("")
        known = set().union(*tagmod.load_tags(self.project.tags_file).values())
        unknown = sorted(t for t in terms if "*" not in t and t not in known)
        self.filter_note.setText(f"no picture has: {', '.join(unknown)}" if unknown else "")
        return True

    def _filter_done(self) -> None:
        self._filter_timer.stop()
        text = self.filter.text().strip()
        if self._check_filter() and text != str((self._loaded or {}).get("filter") or ""):
            self._write({"filter": text or None})

    def _mode_changed(self, _on=None) -> None:
        self._enable()
        self._write({"mode": "slideshow" if self.slideshow.isChecked() else "single"})

    def _setter_chosen(self, _i) -> None:
        try:
            self.project.update_shared({"setter": self.setter.currentData()})
            self._loaded = yamledit.plain(self.project.load_setup())
        except ProjectError as e:
            error(self, "Wallpaper tool", str(e))
        self.refresh_runner()

    # ------------------------------------------------------------------ preview of the selection
    def update_preview(self) -> None:
        try:
            ev = self.project.evaluate_rule({"source": "setup"}, strict=False)
        except (ProjectError, ValueError, OSError) as e:
            self.summary.setText(f"<span style='color:{RED}'>{e}</span>")
            self.strip.clear()
            self.apply_btn.setEnabled(False)
            return
        jobs = ev["jobs"]
        n = len(jobs)
        self.apply_btn.setEnabled(n > 0 and not self._busy)
        if n == 0:
            text = f"<span style='color:{RED}'>No picture matches, so Apply would change nothing.</span>"
        elif ev["mode"] == "single" or n == 1:
            text = f"Shows <b>{jobs[0].source.name}</b>" + (f" (the first of {n} that match)" if n > 1 else "")
            jobs = jobs[:1]
        else:
            secs = parse_duration(ev["interval"])
            text = (f"<b>{n} pictures</b>{', shuffled' if ev['shuffle'] else ''}, each for {nice_duration(secs)}; "
                    f"they all go round once every {nice_duration(secs * n)}.")
        self.summary.setText(text)
        self._fill_strip([j.source for j in jobs])

    def _fill_strip(self, paths: list[Path]) -> None:
        self._thumb_gen += 1
        gen = self._thumb_gen
        self.strip.clear()
        items = {}
        for p in paths[:80]:
            it = QListWidgetItem(p.name)
            it.setToolTip(p.name)
            it.setSizeHint(QSize(STRIP.width() + 10, STRIP.height() + 40))
            self.strip.addItem(it)
            items[p] = it
        for p, it in items.items():
            def done(res, it=it):
                if gen == self._thumb_gen:
                    it.setIcon(QIcon(QPixmap.fromImage(res[0]).scaled(STRIP, Qt.KeepAspectRatio,
                                                                      Qt.SmoothTransformation)))
            run_async(lambda p=p: make_thumbnail(p), done)

    # ------------------------------------------------------------------ applying
    def apply(self) -> None:
        be = self.backend()
        if be is None:
            error(self, "Apply", "No supported desktop found (GNOME, KDE Plasma, sway, Hyprland).")
            return
        if self._busy:
            return
        if not self.stop_calibration():
            self.message.emit("the calibration pattern is still updating; try again in a moment")
            return
        self._filter_done()
        self._busy = True
        self.apply_btn.setEnabled(False)
        self.apply_btn.setText("Applying…")
        self.log.clear()
        lines: list[str] = []
        proj = self.project

        def work():
            with capture_log(lines.append):
                proj.run(be, {"source": "setup"})
            return lines

        def finish(ok: bool, info) -> None:
            self._busy = False
            self.apply_btn.setText("Apply to the desktop")
            self.log.setPlainText("\n".join(lines + ([] if ok else [f"error: {info}"])))
            self.show_log.setEnabled(True)
            self.update_preview()
            self.refresh_status()
            self.refresh_runner()
            if ok:
                # enlargement notes are already shown per picture on the Pictures tab
                warn = [ln.strip() for ln in lines if ln.strip().startswith("warning:") and "enlarged" not in ln]
                done = next((ln for ln in reversed(lines) if ln.startswith(("wallpaper set", "wallpaper already"))),
                            "applied")
                self.message.emit(done.split(":")[0] + (f"; {len(warn)} warning(s), see Details" if warn else ""))
                if warn:
                    self.show_log.setChecked(True)
            else:
                self.show_log.setChecked(True)
                error(self, "Apply", str(info))

        run_async(work, lambda r: finish(True, r), lambda e: finish(False, e))

    # ------------------------------------------------------------------ status
    def _on_tick(self) -> None:
        try:
            mtime = self.project.state_path().stat().st_mtime_ns
        except OSError:
            mtime = None
        if mtime != self._state_mtime:
            self.refresh_status()
        else:
            self._update_countdown()
        if self.isVisible():
            self.refresh_runner(quiet=True)

    def refresh_status(self) -> None:
        proj = self.project
        try:
            self._state_mtime = proj.state_path().stat().st_mtime_ns
        except OSError:
            self._state_mtime = None
        state = proj.load_state()
        self._state = state
        self.pending.setVisible(False)
        if not state:
            self.status.setText("Nothing has been applied by panescape yet.")
            self.countdown.setText("")
            self._show_current(None)
            return
        order = state.get("order") or []
        cfg = Path(state.get("configuration", "")).stem
        what = (f"Slideshow of <b>{len(order)}</b> pictures" if state.get("mode") == "slideshow"
                else "One picture")
        setup, values = proj.applied_setup(state)
        rule = state.get("rule") or {}
        source = (f"setup “{setup}”" if setup else "setup.yml" if rule.get("source") == "setup"
                  else "the command line (panescape apply …)")
        lines = [f"{what}, layout <b>{cfg}</b>", f"from {source}, applied {state.get('applied', '?')}"]
        if state.get("pinned"):
            lines.append(f"plus {len(state['pinned'])} pinned with 'panescape add'")
        self.status.setText("<br>".join(lines))
        note = None
        try:
            active, now = proj.active_setup(), yamledit.plain(proj.load_setup())
        except ProjectError:
            active, now = None, None
        if proj.calibrating():
            note = "A calibration pattern is on the screens (switch it off on the Layout tab, or press Apply)."
        elif setup and active and setup != active:
            note = f"The desktop shows setup “{setup}”; press Apply to switch to “{active}”."
        elif setup and values is not None and now is not None and values != now:
            note = "This setup has changed since it was applied; press Apply to update the desktop."
        self.pending.setText(note or "")
        self.pending.setVisible(bool(note))
        self._current_key = None
        self._update_countdown()

    def _update_countdown(self) -> None:
        state = getattr(self, "_state", None)
        if not state:
            return
        order = state.get("order") or []
        idx, wait = frame_index(state)
        if order and (state.get("applied"), idx) != self._current_key:
            self._current_key = (state.get("applied"), idx)
            self._show_current(Path(order[idx]))
        if state.get("mode") == "slideshow" and len(order) > 1:
            nxt = Path(order[(idx + 1) % len(order)]).name
            runner_needed = state.get("kind") == "per_output"
            stalled = runner_needed and not self.project.runner_pid()
            self.countdown.setText(
                f"<span style='color:{RED}'>Stuck on this picture: the slideshow runner isn't running.</span>"
                if stalled else f"Next: {nxt} in {clock(wait)}")
        else:
            self.countdown.setText("")

    def _show_current(self, path: Path | None) -> None:
        if path is None or not path.exists():
            self.current.setPixmap(QPixmap())
            self.current.setText("" if path is None else f"{path.name}\n(file missing)")
            return
        self.current.setText(path.name)
        gen = self._current_key

        def done(res):
            if gen == self._current_key:
                self.current.setPixmap(QPixmap.fromImage(res[0]).scaled(
                    CURRENT, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self.current.setToolTip(path.name)
        run_async(lambda: make_thumbnail(path), done)

    # ------------------------------------------------------------------ slideshow runner
    def refresh_runner(self, quiet: bool = False) -> None:
        be = self.backend()
        name = getattr(be, "__name__", "").rsplit(".", 1)[-1] if be else None
        per_output = getattr(be, "KIND", "") == "per_output"
        pid = self.project.runner_pid() if per_output else None
        key = (name, pid, autostart_file().exists())
        if quiet and key == getattr(self, "_runner_key", None):
            return
        self._runner_key = key
        state = self.project.load_state()
        slideshow = state.get("kind") == "per_output" and state.get("mode") == "slideshow"
        for w in (self.start_runner, self.stop_runner, self.open_log, self.autostart,
                  self.exec_note, self.exec_row, self.setter_row):
            w.setVisible(per_output)
        if be is None:
            self.runner_text.setText("No supported desktop found (GNOME, KDE Plasma, sway, Hyprland).")
            return
        if not per_output:
            self.runner_text.setText("GNOME plays panescape slideshows itself, so nothing needs to keep running.")
            return
        if pid:
            self.runner_text.setText(f"Running (pid {pid}). It changes the picture on each screen on time "
                                     "and stops by itself when no slideshow is applied.")
        elif slideshow:
            self.runner_text.setText(f"<span style='color:{RED}'>Not running</span>, so the slideshow is "
                                     "stuck on one picture. Press Start (Apply starts it too).")
        else:
            self.runner_text.setText("Not running (only needed while a slideshow is applied).")
        self.start_runner.setEnabled(not pid and slideshow)
        self.start_runner.setToolTip("" if slideshow else "Apply a slideshow first")
        self.stop_runner.setEnabled(bool(pid))
        self.open_log.setEnabled((self.project.backgrounds / "slideshow.log").exists())
        self.setter_row.setVisible(name == "wlroots")

        from ..backends import wlroots
        comp = wlroots.compositor() if name == "wlroots" else None
        self.autostart.setVisible(comp is None)
        self.exec_note.setVisible(comp is not None)
        self.exec_row.setVisible(comp is not None)
        if comp is None:
            self.autostart.blockSignals(True)
            self.autostart.setChecked(autostart_file().exists())
            self.autostart.blockSignals(False)
        else:
            cmd = shlex.join(runner_argv(self.project.root))
            line = f"exec {cmd}" if comp == "sway" else f"exec-once = {cmd}"
            conf = _compositor_config(comp)
            found = conf is not None and _mentions_runner(conf)
            where = f"~/{conf.relative_to(Path.home())}" if conf and conf.is_relative_to(Path.home()) else conf
            self.exec_note.setText(
                f"✓ Your {comp} config ({where}) already starts the runner." if found else
                f"To start it when you log in, add this line to your {comp} config"
                + (f" ({where})" if where else "") + ":")
            self.exec_line.setText(line)
            self.exec_row.setVisible(not found)

    def start(self) -> None:
        pid = self.project.ensure_slideshow_runner()
        self.message.emit(f"started the slideshow runner (pid {pid})" if pid else "the runner is already running")
        QTimer.singleShot(500, self.refresh_runner)

    def stop(self) -> None:
        pid = self.project.runner_pid()
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                self.message.emit(f"stopped the slideshow runner (pid {pid})")
            except OSError as e:
                error(self, "Stop", str(e))
        QTimer.singleShot(500, self.refresh_runner)

    def set_autostart(self, on: bool) -> None:
        try:
            if on:
                path = install_autostart(self.project.root)
                self.message.emit(f"wrote {path}")
            else:
                autostart_file().unlink(missing_ok=True)
                self.message.emit("the runner no longer starts when you log in")
        except OSError as e:
            error(self, "Autostart", str(e))
        self.refresh_runner()


def _compositor_config(comp: str) -> Path | None:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    for p in ((base / "sway" / "config", Path.home() / ".sway" / "config") if comp == "sway"
              else (base / "hypr" / "hyprland.conf",)):
        if p.exists():
            return p
    return None


def _mentions_runner(conf: Path) -> bool:
    """Does the compositor config (or a file it includes nearby) start 'panescape … slideshow'?"""
    files = [conf, *sorted(conf.parent.glob("config.d/*")), *sorted(conf.parent.glob("*.conf"))]
    for f in files:
        try:
            for line in f.read_text(errors="replace").splitlines():
                s = line.strip()
                if not s.startswith("#") and "panescape" in s and "slideshow" in s and "exec" in s:
                    return True
        except OSError:
            continue
    return False
