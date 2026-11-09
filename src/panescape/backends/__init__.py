"""Desktop backends that know how to apply wallpapers.

Only GNOME is implemented so far. A backend needs: ``available()``,
``get_layout()``, ``match_monitors()``, ``compose()`` (or per-output setting),
``set_wallpaper()`` and slideshow support. PRs for KDE/sway/Hyprland welcome.
"""

from __future__ import annotations

from . import gnome


def detect():
    if gnome.available():
        return gnome
    return None
