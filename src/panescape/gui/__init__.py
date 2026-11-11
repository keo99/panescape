"""panescape's window (PySide6). Start with ``panescape gui`` or ``panescape-gui``.

Everything here edits the same files the command line uses
(configurations/*.yml, setup.yml), so the two can be mixed freely.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _is_project(p: Path) -> bool:
    return (p / "setup.yml").exists() or (p / "configurations").is_dir()


def _find_root(given: str | None):
    """--root / $PANESCAPE_ROOT / the current folder / the last one used / ask."""
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    for cand in (given, os.environ.get("PANESCAPE_ROOT")):
        if cand:
            return Path(cand).expanduser().resolve()
    if _is_project(Path.cwd()):
        return Path.cwd()
    last = QSettings().value("root")
    if last and _is_project(Path(last)):
        return Path(last)
    QMessageBox.information(None, "panescape", "Choose a folder for panescape: your pictures, layouts and "
                            "setups live there (pictures/, configurations/, setup.yml).")
    chosen = QFileDialog.getExistingDirectory(None, "panescape folder", str(Path.home()))
    return Path(chosen) if chosen else None


def prepare_project(root: Path):
    """The folders and a starter setup.yml, so a new folder opens ready to use."""
    from ..cli import EXAMPLE_SETUP
    from ..project import Project
    project = Project(root)
    for d in (project.pictures, project.configurations, project.backgrounds):
        d.mkdir(parents=True, exist_ok=True)
    if not project.setup_file.exists():
        names = project.config_names()
        project.setup_file.write_text(EXAMPLE_SETUP.format(config=names[0] if names else "my_desk"))
    from PySide6.QtCore import QSettings
    QSettings().setValue("root", str(project.root))
    return project


def main(root: str | Path | None = None) -> int:
    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("panescape")
    app.setOrganizationName("panescape")
    app.setDesktopFileName("panescape")
    from .. import launcher
    from .common import app_icon
    app.setWindowIcon(app_icon())
    try:
        launcher.refresh_if_installed()
    except OSError:
        pass
    root = Path(root) if root else _find_root(None)
    if root is None:
        return 0
    win = MainWindow(prepare_project(root))
    win.show()
    return app.exec()


def run() -> None:
    """Entry point for ``panescape-gui``."""
    ap = argparse.ArgumentParser(prog="panescape-gui")
    ap.add_argument("--root", help="project folder")
    args = ap.parse_args()
    if args.root:
        os.environ["PANESCAPE_ROOT"] = args.root
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    app.setApplicationName("panescape")
    app.setOrganizationName("panescape")
    sys.exit(main(_find_root(args.root)))
