"""The main window: setup selector on top, tabs below."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..project import Project, ProjectError
from .common import confirm, error
from .layout_editor import LayoutEditor

DOCS_URL = "https://keo99.github.io/panescape/"
ISSUES_URL = "https://github.com/keo99/panescape/issues"
_windows: list = []  # windows opened from File ▸ Open folder (kept alive here)


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
        where.setToolTip(str(project.root))
        where.setText(where.fontMetrics().elidedText(where.text(), Qt.ElideMiddle, 420))
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

        from .getting_started import GettingStarted
        self.guide = GettingStarted(project)
        self.guide.step_clicked.connect(self.go_to_step)
        root.addWidget(self.guide)

        self.tabs = QTabWidget()
        self.layout_editor = LayoutEditor(project)
        self.layout_editor.message.connect(lambda m: self.statusBar().showMessage(m, 8000))
        self.layout_editor.config_chosen.connect(self.config_chosen)
        self.tabs.addTab(self.layout_editor, "Layout")
        from .pictures_tab import PicturesTab
        self.pictures_tab = PicturesTab(project, self.layout_editor.backend)
        self.pictures_tab.message.connect(lambda m: self.statusBar().showMessage(m, 8000))
        self.tabs.addTab(self.pictures_tab, "Pictures && tags")
        from .apply_tab import ApplyTab
        self.apply_tab = ApplyTab(project, self.layout_editor.backend, self.layout_editor.drop_calibration)
        self.apply_tab.message.connect(lambda m: self.statusBar().showMessage(m, 8000))
        self.apply_tab.setup_edited.connect(self._setup_edited_here)
        self.tabs.addTab(self.apply_tab, "Apply")
        from .unsplash_tab import UnsplashTab
        self.unsplash_tab = UnsplashTab(project)
        self.unsplash_tab.message.connect(lambda m: self.statusBar().showMessage(m, 8000))
        self.unsplash_tab.downloaded.connect(self._downloaded)
        self.tabs.addTab(self.unsplash_tab, "Unsplash")
        self.tabs.currentChanged.connect(self._tab_changed)
        root.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        self.watcher = QFileSystemWatcher(self)
        self.watcher.addPath(str(project.root))
        self.watcher.directoryChanged.connect(lambda _p: self._watch_setup())
        self.watcher.fileChanged.connect(lambda _p: self._setup_changed_on_disk())
        self._watch_setup()

        self.refresh_setups()
        self.layout_editor.show_config(self._setup_config())
        self._guide_timer = QTimer(self)  # picks up pictures added or an apply made anywhere
        self._guide_timer.timeout.connect(lambda: self.guide.isVisible() and self.guide.refresh())
        self._guide_timer.start(2000)
        self._build_menus()
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
        self.pictures_tab.refresh_layout()
        self.pictures_tab.apply_filter()
        self.apply_tab.refresh()
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
        self.apply_tab.refresh(force=False)

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
        self.apply_tab.refresh(force=False)

    def _setup_edited_here(self) -> None:
        """The Apply tab changed the active setup (e.g. its layout)."""
        cfg = self._setup_config()
        if cfg and self.layout_editor.name != Path(str(cfg)).stem and self.layout_editor.maybe_save():
            self.layout_editor.show_config(cfg)
        self.layout_editor._update_used_by()
        self.pictures_tab.refresh_layout()

    def _downloaded(self, _path: str) -> None:
        self.pictures_tab.refresh()
        self.apply_tab.update_preview()

    def go_to_step(self, step: str) -> None:
        if step == "monitors":
            self.tabs.setCurrentWidget(self.layout_editor)
            if not self.project.config_names():
                self.layout_editor.new_config()
        elif step == "pictures":
            self.tabs.setCurrentWidget(self.pictures_tab)
        else:
            self.tabs.setCurrentWidget(self.apply_tab)
        self.guide.refresh()

    def _tab_changed(self, _i) -> None:
        if self.guide.isVisible():
            self.guide.refresh()
        if self.tabs.currentWidget() is self.pictures_tab:
            self.pictures_tab.refresh_layout()  # the layout may have been edited / saved
        elif self.tabs.currentWidget() is self.unsplash_tab:
            self.unsplash_tab.refresh_layout()  # for the sharpness note
        elif self.tabs.currentWidget() is self.apply_tab:
            self.apply_tab.refresh(force=False)
            self.apply_tab.update_preview()  # tags / pictures may have changed
            self.apply_tab.refresh_runner()

    # ------------------------------------------------------------------ menus
    def _build_menus(self) -> None:
        mb = self.menuBar()
        file = mb.addMenu("&File")
        file.addAction("&Open folder…", QKeySequence.Open, self.open_folder)
        file.addAction("Show folder in file &manager", self.show_folder)
        file.addSeparator()
        file.addAction("&Quit", QKeySequence("Ctrl+Q"), self.close)

        view = mb.addMenu("&View")
        for i in range(self.tabs.count()):
            act = view.addAction(self.tabs.tabText(i),
                                 QKeySequence(f"Ctrl+{i + 1}"), lambda i=i: self.tabs.setCurrentIndex(i))
            act.setShortcutContext(Qt.WindowShortcut)

        hlp = mb.addMenu("&Help")
        hlp.addAction("&Getting started", self.guide.bring_back)
        hlp.addAction("How to &calibrate", self.layout_editor.show_guide)
        hlp.addAction("&Documentation (online)", lambda: QDesktopServices.openUrl(QUrl(DOCS_URL)))
        hlp.addAction("&Report a problem (online)", lambda: QDesktopServices.openUrl(QUrl(ISSUES_URL)))
        hlp.addSeparator()
        self.launcher_action = hlp.addAction("Show panescape in the app &menu")
        self.launcher_action.setCheckable(True)
        self.launcher_action.toggled.connect(self.set_launcher)
        hlp.aboutToShow.connect(self._sync_launcher_action)
        hlp.addSeparator()
        hlp.addAction("&About panescape", self.about)
        self.menus = {"File": file, "View": view, "Help": hlp}  # keeps the Python wrappers alive

    def _sync_launcher_action(self) -> None:
        from .. import launcher
        self.launcher_action.blockSignals(True)
        self.launcher_action.setChecked(launcher.installed())
        self.launcher_action.blockSignals(False)

    def set_launcher(self, on: bool) -> None:
        from .. import launcher
        try:
            if on:
                launcher.install()
                self.statusBar().showMessage("panescape is in your app menu now (it may take a moment to appear)",
                                             8000)
            else:
                launcher.remove()
                self.statusBar().showMessage("removed panescape from the app menu", 8000)
        except OSError as e:
            error(self, "App menu", str(e))
        self._sync_launcher_action()

    def show_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.project.root)))

    def open_folder(self) -> None:
        """Switch to another panescape folder (or make a new one) in a fresh window."""
        from . import _is_project, prepare_project
        chosen = QFileDialog.getExistingDirectory(self, "Open a panescape folder", str(self.project.root.parent))
        if not chosen:
            return
        root = Path(chosen).resolve()
        if root == self.project.root:
            return
        if not _is_project(root) and not confirm(
                self, "New panescape folder",
                f"{root} isn't a panescape folder yet. Make it one?\n\n"
                "This adds pictures/, configurations/, backgrounds/ and setup.yml to it."):
            return
        if not self.layout_editor.maybe_save():
            return
        try:
            project = prepare_project(root)
        except OSError as e:
            error(self, "Open folder", str(e))
            return
        win = MainWindow(project)
        win.show()
        _windows.append(win)
        self.close()

    def about(self) -> None:
        from .. import __version__
        QMessageBox.about(
            self, "About panescape",
            f"<h3>panescape {__version__}</h3>"
            "<p>True-to-scale wallpapers across monitors of different sizes, resolutions and orientations.</p>"
            f"<p><a href='{DOCS_URL}'>{DOCS_URL}</a></p>"
            "<p>MIT licence. Unsplash photos are credited to their photographers in "
            "<code>pictures/credits.yml</code>.</p>")

    # ------------------------------------------------------------------ closing
    def closeEvent(self, event):
        if not self.layout_editor.maybe_save():
            event.ignore()
            return
        self.layout_editor.stop_calibration_now()
        QSettings().setValue("geometry", self.saveGeometry())
        event.accept()
