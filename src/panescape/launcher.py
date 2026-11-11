"""An app-menu entry and icon for the window (pip and pipx don't add one).

Writes the usual per-user files, which every Linux desktop reads:
  ~/.local/share/applications/panescape.desktop
  ~/.local/share/icons/hicolor/scalable/apps/panescape.svg
"""

from __future__ import annotations

import os
import shlex
import shutil
import sys
from pathlib import Path

APP_ID = "panescape"  # also the Wayland app id / X11 WM class, so the dock matches the window to the entry


def icon_source() -> Path:
    return Path(__file__).resolve().parent / "gui" / "icons" / "panescape.svg"


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def desktop_file() -> Path:
    return _data_home() / "applications" / f"{APP_ID}.desktop"


def icon_file() -> Path:
    return _data_home() / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"


def launch_argv() -> list[str]:
    """This Python (e.g. pipx's) running the window, so the entry works whatever is on $PATH."""
    return [sys.executable, "-m", "panescape", "gui"]


def desktop_entry() -> str:
    return ("[Desktop Entry]\n"
            "Type=Application\n"
            "Name=panescape\n"
            "GenericName=Multi-monitor wallpaper\n"
            "Comment=True-to-scale wallpapers across monitors of different sizes\n"
            f"Exec={shlex.join(launch_argv())}\n"
            f"Icon={APP_ID}\n"
            "Terminal=false\n"
            "Categories=Settings;DesktopSettings;Graphics;Qt;\n"
            "Keywords=wallpaper;background;monitor;slideshow;bezel;\n"
            f"StartupWMClass={APP_ID}\n")


def installed() -> bool:
    return desktop_file().exists()


def install() -> Path:
    """Add (or refresh) the app-menu entry and its icon. Returns the .desktop file."""
    icon = icon_file()
    icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(icon_source(), icon)
    path = desktop_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(desktop_entry())
    tmp.replace(path)
    return path


def remove() -> list[Path]:
    """Remove the entry and icon; returns what was removed."""
    gone = []
    for p in (desktop_file(), icon_file()):
        if p.exists():
            p.unlink()
            gone.append(p)
    return gone


def refresh_if_installed() -> None:
    """Keep an existing entry pointing at this install (e.g. after moving from pip to pipx)."""
    if installed() and desktop_file().read_text() != desktop_entry():
        install()
