# panescape

[![CI](https://github.com/keo99/panescape/actions/workflows/ci.yml/badge.svg)](https://github.com/keo99/panescape/actions/workflows/ci.yml)

**True-to-scale wallpapers for multi-monitor setups on Linux.** Your monitors
become panes of one window onto a single picture, even when they differ in size,
resolution, orientation and height.

![The same photo spanned by pixels (top) and by panescape (bottom) across two portrait 28" 4K screens and a lower landscape 24" 1080p screen](docs/images/comparison.jpg)

Most spanning tools work in **pixels**. A 28" 4K panel packs about 1.7× more
pixels per millimetre than a 24" 1080p panel, so with pixel spanning the picture
changes size at every screen edge and the horizon breaks. panescape works in
**millimetres**. It uses each panel's real lit area, the bezels, the gaps and the
vertical offsets you measure. Then it resamples each monitor's slice to that
monitor's native resolution.

## Quick start

```bash
pipx install git+https://github.com/keo99/panescape
mkdir ~/panescape && cd ~/panescape
panescape init                     # pictures/, configurations/, backgrounds/, setup.yml
panescape detect --write my_desk   # config skeleton from your current displays
$EDITOR configurations/my_desk.yml # set gaps, vertical offsets, bezels (mm)
cp ~/Pictures/some-wide-photo.jpg pictures/
panescape make                     # check backgrounds/my_desk/*/preview.jpg
panescape apply
```

`preview.jpg` draws your desk to scale. The yellow outline is the part of the photo that was used:

![Preview of the example desk](docs/images/preview.jpg)

**Status:** applying wallpapers works on **GNOME** (tested on GNOME 50 / Ubuntu
26.04, Wayland, with fractional scaling). On other desktops panescape makes
native-resolution images per monitor for you to set with your own tool (see
[Other desktops](#other-desktops)). Python ≥ 3.10; the only dependencies are
Pillow and PyYAML.

### Why not HydraPaper or a "spanned" wallpaper?

Those work too. You give them one image per monitor, or one image stretched over
the desktop, and they place it in pixel space. panescape decides *what each
monitor should show* from the physical layout, and builds the images those
tools need. On GNOME it applies them directly, slideshows included, with
nothing running in the background.

## How a project folder looks

```
pictures/                     your source images (+ tags.yml)
configurations/my_desk.yml    physical description of a monitor setup
setup.yml                     what `panescape apply` puts on the desktop
backgrounds/<config>/<image>/
    1_left.png 2_centre.png … one native-resolution image per monitor
    preview.jpg               to-scale picture of the desk with bezels
    meta.json                 geometry used (mm, source pixel boxes, upscale)
    gnome-….png               the combined image GNOME displays
```

Commands run in the current folder. Point them elsewhere with `--root DIR` or
`PANESCAPE_ROOT`. Examples are in [`examples/`](examples/).

## Usage

```bash
panescape make                          # slice every image in pictures/ (cached)
panescape make sunset.jpg --anchor top  # one image, cut from the top
panescape make --apply                  # make, then apply (several images = slideshow)

panescape apply                         # apply what setup.yml describes
panescape apply sunset.jpg              # just this image
panescape apply a.jpg b.jpg "city-*" --interval 10m --transition 2s --shuffle
panescape add new.jpg                   # pin an image into the current slideshow
panescape sync                          # re-check tags / pictures / setup.yml and update
panescape tag new.jpg -a landscape      # tag an image (updates a running slideshow)
panescape tags 'landscape + !night'     # list images matching a filter
panescape status                        # configs, what's been made, what's applied
panescape-apply …                       # same as `panescape apply …`
```

Use `--dry-run` to build everything without changing the wallpaper. Use `-c NAME` to
pick a configuration, and `--force` to re-render. For the best result, use a
source image at least as many pixels wide as your desk needs. `make` warns
when a screen would be enlarged.

## Configuration (`configurations/*.yml`)

List monitors **left to right**. Lengths are in mm unless you give a unit
(`28in`, `2.5cm`).

```yaml
name: desk_H
monitors:
  - name: left
    model: Samsung U28E590      # built-in specs (see src/panescape/models.py)
    serial: ABC1234567          # pin to a physical screen (from `panescape detect`)
    rotation: portrait-right    # landscape | portrait-right | portrait-left | landscape-flipped
    bezel: {left: 24, right: 16, top: 20, bottom: 20}   # measured, as mounted

  - name: centre
    resolution: FHD             # or 1920x1080, 4K, QHD, …; always the panel's native mode
    size: 24in                  # diagonal; or active_area: [527.04, 296.46] (landscape, mm)
    bezel: 6                    # all sides; or [top, right, bottom, left]
    gap: 0                      # mm from the previous housing (negative = overlap)
    v_pos: -20                  # mm, + is up, relative to the first monitor
    v_align: center             # measure v_pos between centres | tops | bottoms

  - name: right
    model: Samsung U28E590
    rotation: portrait-left
    v_pos: 0

crop:
  anchor: center                # center, top, bottom-left, … or [x, y] in 0..1
  zoom: 1.0                     # > 1 crops tighter
  fit: cover                    # cover (fill and crop) | contain (whole image, bars)
output:
  format: png                   # png | jpg | webp
```

Every monitor key:

| key | meaning |
|---|---|
| `name` | Label, used in output file names |
| `model` | Looked up in the built-in database. Supplies resolution, exact active area and estimated bezels |
| `resolution` | Native mode, e.g. `3840x2160`, `4K` or `FHD` |
| `size` | Diagonal (default unit: inches). Used only if there's no `model` or `active_area` |
| `active_area` | `[w, h]` of the lit area in mm, landscape. This is the most precise option |
| `rotation` / `orientation` | GNOME Settings name, or `vertical` / `horizontal` |
| `bezel` | Number, `[t, r, b, l]`, or `{left, right, top, bottom, x, y}`, measured **as mounted** |
| `gap` | Horizontal distance from the previous monitor's housing |
| `v_pos`, `v_align`, `v_ref` | Vertical offset, which edges it's measured between, and the reference monitor |
| `position` | `[x, y]` of the housing's top-left in mm. Overrides the row layout, for stacked or free layouts |
| `serial` / `connector` / `product` | Pin to a specific screen. Without these, screens are matched left to right |

**Precision tips**

1. Use `model` or `active_area`. Nominal diagonals are rounded, and a "28 inch"
   panel is not exactly 28 inches.
2. Measure your bezels with a ruler. Spec-sheet bezels are estimates, because
   manufacturers rarely split top and bottom.
3. Measure `v_pos` between the edges that are easiest to measure, and set `v_align` to match.
4. Look at `preview.jpg`. It is drawn to scale.

## `setup.yml`

```yaml
configuration: desk_H
mode: slideshow           # single | slideshow
images: all               # or a list of names / globs / {file, anchor, zoom}
interval: 15m
transition: 2s
shuffle: false
```

Images that aren't made yet, or have changed, are rendered automatically.

## Keeping the slideshow up to date

`apply` saves the **selection rule** (`images`, `filter`, `mode`, …), not just the
list of files that matched it at the time:

- `panescape apply` with no images follows `setup.yml`. Each re-check reads
  `setup.yml` again, so edits to it count too. Options you give on the command line
  (e.g. `--filter`, `--interval`) override it and are remembered.
- `panescape apply a.jpg "city-*" --filter night` follows that rule instead. Globs
  pick up new matching files; file names are fixed.

The rule is re-checked by:

| command | what happens |
|---|---|
| `panescape tag …` | after saving tags, updates the running wallpaper (skip with `--no-sync`) |
| `panescape sync` | after hand-editing `tags.yml` or `setup.yml`, or adding or deleting pictures |
| `panescape add IMAGES` / `--filter F` | **pins** images: they stay even if they later stop matching the rule. Adding to a single image turns it into a slideshow |

A re-check renders new matches, drops images that stopped matching or were
deleted, and prints `+`/`-` for each change. It does nothing if the result is
unchanged. The image that is on screen stays on screen, at the same point in
its interval, so an update doesn't restart the slideshow. With `shuffle`, existing
images keep their order and new ones are inserted at random places.

A fresh `panescape apply` replaces the rule, clears the pins and starts from
the first image.

## Tags and filters

Tags live in `pictures/tags.yml`. You can edit the file by hand or use the CLI:

```yaml
lake-dawn.jpg: [landscape, water]
alps.jpg: [landscape, mountains, snow]
"city-*": [urban, night]        # globs tag every match
```

```bash
panescape tag lake-dawn.jpg "beach-*" -a landscape water   # -r to remove, --clear to reset
panescape tags                                            # list images, tags and counts
panescape tags 'landscape + water + !mountains'           # preview a filter
panescape apply --filter 'landscape + water + !mountains' # one-off, overrides setup.yml
panescape add --filter beach                              # pin everything tagged beach right now
```

In `setup.yml`, `filter:` narrows `images:`. Use `images: all` to filter the whole folder.

| syntax | meaning |
|---|---|
| `a + b`, `a & b`, `a and b`, `a b` | AND |
| `a \| b`, `a , b`, `a or b` | OR |
| `!a`, `not a` | NOT (binds tightest) |
| `( … )` | grouping |
| `city*` | glob on tag names |

Tags are case-insensitive, and untagged images match `!x`. The CLI rewrites
`tags.yml` and drops any comments you added, apart from its own header.

## How applying works on GNOME

GNOME has one wallpaper for the whole desktop. In `spanned` mode, Mutter
stretches it over the bounding box of the *logical* monitor layout. Each monitor
then samples its own part at its own scale factor. panescape builds that
combined image at the largest scale factor, so HiDPI screens get native pixels,
and places each monitor's slice in its rectangle. It then sets
`org.gnome.desktop.background` with gsettings. Slideshows use GNOME's native
background XML, so nothing runs in the background. If you rearrange your
displays in Settings, run `panescape apply` again.

panescape always calls the system `/usr/bin/gsettings`. Conda and Anaconda put
their own `gsettings` first on `PATH`. That copy has no dconf backend, so it reports
success while changing nothing. After setting the wallpaper, panescape reads the
value back from dconf to check that it was stored.

The physical layout (config) and GNOME's arrangement (Settings > Displays) are
separate. Moving the mouse between screens uses GNOME's arrangement, and the
picture uses your measured layout.

## Other desktops

`backgrounds/<config>/<image>/N_name.png` are plain per-monitor images that you
can set with any tool, for example `swaybg -o DP-1 -i 1_left.png …`, `feh`, KDE or
HydraPaper. Backends for KDE, sway and Hyprland would be welcome contributions,
see `src/panescape/backends/`.

## Adding a monitor model

Add an entry to `src/panescape/models.py`, using the spec sheet's active area,
outer size without stand, and the bezels if you've measured them. Then open a PR.

## Development

```bash
pip install -e '.[dev]'
pytest
ruff check .
python docs/make_images.py   # rebuild the README images
```

## Credits

README images use "Panorama of the valley of the Triglav mountain massif" by
otrow_photography, released under
[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) via
[Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Panorama_of_the_valley_of_the_Triglav_mountain_massif.jpg).

## License

MIT © Kieran Ralston
