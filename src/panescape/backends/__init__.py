"""Desktop backends that know how to apply wallpapers.

Two kinds:

* ``KIND = "spanned"`` (GNOME): one combined image for the whole desktop,
  built with ``compose()`` and set with ``set_wallpaper()``; native slideshows.
* ``KIND = "per_output"`` (sway, Hyprland): one image per output, set with
  ``set_outputs()``; panescape runs the slideshow itself.

Both provide ``available()``, ``get_layout()`` and ``match_monitors()``.
PRs for more desktops (KDE, niri, ...) welcome.
"""

from __future__ import annotations

from . import gnome, wlroots


def detect():
    # compositor sockets are more specific than XDG_CURRENT_DESKTOP
    if wlroots.available():
        return wlroots
    if gnome.available():
        return gnome
    return None
