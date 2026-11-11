"""The app-menu entry and icon (written under a temporary XDG_DATA_HOME)."""

import shlex
import sys

from panescape import cli, launcher


def test_install_and_remove(capsys):
    assert not launcher.installed()
    cli.main(["gui", "--install-launcher"])
    entry = launcher.desktop_file().read_text()
    assert "Icon=panescape" in entry and "StartupWMClass=panescape" in entry
    exec_line = next(line for line in entry.splitlines() if line.startswith("Exec="))
    assert shlex.split(exec_line[5:]) == [sys.executable, "-m", "panescape", "gui"]
    assert launcher.icon_file().read_bytes() == launcher.icon_source().read_bytes()
    assert "app menu" in capsys.readouterr().out

    cli.main(["gui", "--remove-launcher"])
    assert not launcher.installed() and not launcher.icon_file().exists()
    cli.main(["gui", "--remove-launcher"])
    assert "no app-menu entry" in capsys.readouterr().out


def test_an_old_entry_is_refreshed():
    launcher.install()
    launcher.desktop_file().write_text("[Desktop Entry]\nExec=/old/venv/bin/python -m panescape gui\n")
    launcher.refresh_if_installed()
    assert sys.executable in launcher.desktop_file().read_text()


def test_nothing_is_added_unless_asked():
    launcher.refresh_if_installed()
    assert not launcher.installed()
