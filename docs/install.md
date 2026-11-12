# Installing

panescape needs Linux and Python 3.10 or newer. The easiest way to install it
is with [pipx](https://pipx.pypa.io/), which keeps it in its own environment:

```bash
pipx install 'panescape[gui]'    # the window and the command line
pipx install panescape           # the command line only
```

`pip install 'panescape[gui]'` works too, inside a virtual environment.

The dependencies are Pillow and ruamel.yaml, plus PySide6 and keyring for
the window. Nothing else is needed on GNOME. Other desktops use
their usual tools; see [Desktops](desktops.md).

## Calibrating from a Photo

[Calibrating from a photo](calibrating.md#from-a-photo) needs two more packages,
numpy and pillow-heif (for iPhone photos). They're in the `photo` extra:

```bash
pipx install 'panescape[gui,photo]'        # a new install
pipx inject panescape numpy pillow-heif    # add them to an existing one
```

## Add It to Your App Menu

pipx doesn't add an app-menu entry. Add one, with panescape's icon, from the
window (**Help ▸ Show panescape in the app menu**) or the command line:

```bash
panescape gui --install-launcher    # --remove-launcher takes it out again
```

## Updating and Removing

```bash
pipx upgrade panescape
pipx uninstall panescape     # run `panescape gui --remove-launcher` first if you added the menu entry
```

Your project folder (pictures, layouts, `setup.yml`) is never touched by
installing or removing panescape.

## The Latest Development Version

```bash
pipx install 'panescape[gui] @ git+https://github.com/keo99/panescape'
```
