"""The main window: setup selector on top, tabs below."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QSettings
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..project import Project, ProjectError
from .common import confirm, error
from .layout_editor import LayoutEditor


class MainWindow(QMainWindow):
    def __init__(self, project: Project):
        super().__init__()
        self.project = project
        self.setWindowTitle(f"panescape — {project.root.name}")
        central = QWidget()
        root = QVBoxLayout(central)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Setup:"))
        self.setups = QComboBox()
        self.setups.setMinimumWidth(180)
        self.setups.setToolTip("A setup is what gets applied: a layout, which pictures, and how they change.\n"
                               "Keep several (e.g. work, evening) in setup.yml and switch between them.")
        self.setups.activated.connect(lambda _i: self.switch_setup(self.setups.currentText()))
        bar.addWidget(self.setups)
        for text, slot in (("New…", self.new_setup), ("Rename…", self.rename_setup),
                           ("Delete…", self.delete_setup)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch(1)
        home = Path.home()
        where = QLabel("~/" + str(project.root.relative_to(home)) if project.root.is_relative_to(home)
                       else str(project.root))
        where.setObjectName("project_path")
        where.setStyleSheet("color: palette(placeholder-text)")
        bar.addWidget(where)
        root.addLayout(bar)

        from ..backends.base import competing_tools
        warnings = competing_tools()
        self.warning = QLabel("⚠ " + "<br>⚠ ".join(warnings))
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("background: #fff3cd; color: #664d03; padding: 6px; border-radius: 4px")
        self.warning.setVisible(bool(warnings))
        root.addWidget(self.warning)

        self.tabs = QTabWidget()
        self.layout_editor = LayoutEditor(project)
        self.layout_editor.message.connect(lambda m: self.statusBar().showMessage(m, 8000))
        self.layout_editor.config_chosen.connect(self.config_chosen)
        self.tabs.addTab(self.layout_editor, "Layout")
        root.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        self.watcher = QFileSystemWatcher(self)
        self.watcher.addPath(str(project.root))
        self.watcher.directoryChanged.connect(lambda _p: self._watch_setup())
        self.watcher.fileChanged.connect(lambda _p: self._setup_changed_on_disk())
        self._watch_setup()

        self.refresh_setups()
        self.layout_editor.show_config(self._setup_config())
        s = QSettings()
        if s.value("geometry"):
            self.restoreGeometry(s.value("geometry"))
        else:
            self.resize(1400, 820)

    # ------------------------------------------------------------------ setups
    def _setup_config(self) -> str | None:
        try:
            return self.project.load_setup().get("configuration")
        except ProjectError:
            return None

    def refresh_setups(self) -> None:
        try:
            names, active = self.project.setup_names(), self.project.active_setup()
        except ProjectError as e:
            self.statusBar().showMessage(str(e))
            names, active = [], None
        self.setups.blockSignals(True)
        self.setups.clear()
        self.setups.addItems(names or ["default"])
        self.setups.setCurrentText(active or "default")
        self.setups.blockSignals(False)

    def switch_setup(self, name: str) -> None:
        try:
            if name != self.project.active_setup():
                self.project.use_setup(name)
        except ProjectError as e:
            error(self, "Setup", str(e))
        self.refresh_setups()
        self.layout_editor.show_config(self._setup_config())
        self.layout_editor._update_used_by()
        self.statusBar().showMessage(f"active setup: {name} (applied the next time you press Apply "
                                     f"or run 'panescape apply')", 8000)

    def config_chosen(self, name: str) -> None:
        """The layout tab picked a configuration: the active setup uses it from now on."""
        try:
            if self._setup_config() != name:
                self.project.update_setup({"configuration": name})
                self.statusBar().showMessage(
                    f"setup “{self.project.active_setup()}” now uses the layout {name}", 8000)
        except ProjectError as e:
            error(self, "Setup", str(e))
        self.refresh_setups()
        self.layout_editor._update_used_by()

    def new_setup(self) -> None:
        name, ok = QInputDialog.getText(self, "New setup",
                                        "Name for the new setup (it starts as a copy of the current one):")
        if not ok or not name.strip():
            return
        try:
            self.project.create_setup(name.strip())
        except ProjectError as e:
            error(self, "New setup", str(e))
            return
        self.refresh_setups()
        self.statusBar().showMessage(f"created setup {name.strip()}", 8000)

    def rename_setup(self) -> None:
        old = self.project.active_setup()
        if not old:
            return
        new, ok = QInputDialog.getText(self, "Rename setup", "New name:", text=old)
        if not ok or not new.strip():
            return
        try:
            self.project.rename_setup(old, new.strip())
        except ProjectError as e:
            error(self, "Rename setup", str(e))
        self.refresh_setups()

    def delete_setup(self) -> None:
        name = self.project.active_setup()
        if not name or len(self.project.setup_names()) <= 1:
            error(self, "Delete setup", "This is the only setup.")
            return
        if not confirm(self, "Delete setup", f"Delete the setup “{name}”? (Its layout and pictures stay.)",
                       "Delete"):
            return
        try:
            self.project.delete_setup(name)
        except ProjectError as e:
            error(self, "Delete setup", str(e))
        self.switch_setup(self.project.active_setup())

    def _watch_setup(self) -> None:
        p = str(self.project.setup_file)
        if self.project.setup_file.exists() and p not in self.watcher.files():
            self.watcher.addPath(p)

    def _setup_changed_on_disk(self) -> None:
        self._watch_setup()  # re-arm after an atomic replace
        self.refresh_setups()
        self.layout_editor._update_used_by()

    # ------------------------------------------------------------------ closing
    def closeEvent(self, event):
        if not self.layout_editor.maybe_save():
            event.ignore()
            return
        self.layout_editor.stop_calibration_now()
        QSettings().setValue("geometry", self.saveGeometry())
        event.accept()
