# panescape

[![CI](https://github.com/keo99/panescape/actions/workflows/ci.yml/badge.svg)](https://github.com/keo99/panescape/actions/workflows/ci.yml)

**True-to-scale wallpapers for multi-monitor setups on Linux.** Your monitors
become panes of one window onto a single picture, even when they differ in size,
resolution, orientation and height.

![The same photo spanned by pixels (top) and by panescape (bottom) across two portrait 28" 4K screens and a lower landscape 24" 1080p screen](https://raw.githubusercontent.com/keo99/panescape/main/docs/images/comparison.jpg)

Most spanning tools work in **pixels**. A 28" 4K panel packs about 1.7× more
pixels per millimetre than a 24" 1080p panel, so with pixel spanning the picture
changes size at every screen edge and the horizon breaks. panescape works in
**millimetres**. It uses each panel's real lit area, the bezels, the gaps and the
vertical offsets you measure. Then it resamples each monitor's slice to that
monitor's native resolution.

## Quick start

```bash
pipx install panescape
mkdir ~/panescape && cd ~/panescape
panescape init                     # pictures/, configurations/, backgrounds/, setup.yml
panescape detect --write my_desk   # config skeleton from your current displays
$EDITOR configurations/my_desk.yml # set gaps, offsets, bezels (mm)
panescape calibrate --watch        # test pattern; re-applies on every save
cp ~/Pictures/some-wide-photo.jpg pictures/
panescape make                     # check backgrounds/my_desk/*/preview.jpg
panescape apply
```

Prefer a window? `pipx install 'panescape[gui]'`,
then run `panescape-gui` (see [The window](#the-window)).

`preview.jpg` draws your desk to scale. The yellow outline is the part of the photo that was used:

![Preview of the example desk](https://raw.githubusercontent.com/keo99/panescape/main/docs/images/preview.jpg)

**Status:** applies wallpapers and slideshows on **GNOME** (tested on GNOME 50 /
Ubuntu 26.04, Wayland, with fractional scaling), **KDE Plasma 6** (tested with Plasma 6.6; see
[KDE Plasma](#kde-plasma)), and **sway** and **Hyprland** (tested with sway
1.11, and Hyprland 0.53 with hyprpaper 0.8; see [sway and Hyprland](#sway-and-hyprland)). On other desktops panescape makes
native-resolution images per monitor for you to set with your own tool (see
[Other desktops](#other-desktops)). Python ≥ 3.10; the dependencies are
Pillow, PyYAML and ruamel.yaml, plus PySide6 for the optional window.

### Why not HydraPaper or a "spanned" wallpaper?

Those work too. You give them one image per monitor, or one image stretched over
the desktop, and they place it in pixel space. panescape decides *what each
monitor should show* from the physical layout, and builds the images those
tools need. On GNOME it applies them directly, slideshows included, with
nothing running in the background.

If you used HydraPaper before, switch off its daemon in HydraPaper's
preferences. Otherwise it re-applies its own wallpaper whenever the displays
change, for example when the monitors wake up. panescape warns when it sees
HydraPaper's daemon or Variety running.

## The window

```bash
pipx install 'panescape[gui]'
panescape-gui            # or: panescape gui
```

![The layout editor](https://raw.githubusercontent.com/keo99/panescape/main/docs/images/gui-layout.png)

It edits the same files as the command line, keeping your comments, so you
can switch between the two whenever you like.

- **Setup** (top bar): choose which [setup](#setupyml) is active, or create,
  rename and delete setups.
- **Layout:** pick the configuration the active setup uses, or make a new one
  from the monitors connected now, as a copy, or from scratch. Each monitor has
  a form for its model or size, resolution, rotation, bezels, position and gap.
  Mistakes show up as you type, not when you apply.
- **Drag monitors** on the to-scale desk, or select one and use the arrow keys
  (0.5 mm steps; Shift ×10, Alt ×0.2). This changes the `gap` and the
  `v_pos`/`h_pos` offsets.
- **Show** a picture or the calibration pattern across the desk.
- **Calibrate on my screens** puts the [test pattern](#calibrating) on your
  real monitors and redraws it as you drag or type. Your wallpaper comes back
  when you switch it off or close the window.

![The pictures and tags tab](https://raw.githubusercontent.com/keo99/panescape/main/docs/images/gui-pictures.png)

The **Pictures & tags** tab:

- **Add pictures** with the button, or drop files from your file manager onto
  the grid. They're copied into `pictures/`.
- **Tag** the selected pictures: type tags and press Enter. Click a tag's ✕ to
  remove it. With several pictures selected, a tag that only some of them have
  shows as e.g. `sea 2/5`; click it to give it to all of them.
- **Manage tags** in the sidebar, which shows how many pictures have each one.
  Click a tag to filter by it, or Ctrl+click to add it to the filter. Rename a
  tag to an existing one to merge them.
- **Filter** with the same syntax as `setup.yml` (`landscape + water +
  !mountains`). **Use for setup** saves the filter to the active setup.
- **Frame** a picture: drag it across the desk preview and scroll to zoom. The
  parts that will be cut off show faintly, and a note warns when a screen would
  show the picture enlarged (soft). The framing is saved in
  `pictures/crops.yml` and used by every setup.

If a panescape wallpaper is on the desktop, it updates a couple of seconds
after a change.

![The apply tab](https://raw.githubusercontent.com/keo99/panescape/main/docs/images/gui-apply.png)

The **Apply** tab:

- **Edit the active setup:** its layout, all pictures or a chosen list
  (names or patterns like `city-*`), the tag filter, one picture or a
  slideshow, how long each picture stays, the cross-fade and shuffling. Each
  change is saved to `setup.yml` straight away, with your comments kept.
- **Check the selection:** the tab shows which pictures the setup picks and how
  long a full round of the slideshow takes.
- **Apply to the desktop** puts it on your screens. **Details** shows the log.
- **On the desktop now:** the picture showing, what comes next and when, and
  a note when the setup has changed since it was applied.
- **Slideshow runner** (sway, Hyprland, KDE): see whether it's running, start
  or stop it, and have it start when you log in. On sway and Hyprland the tab
  gives you the line to add to your config, with a Copy button, and notices
  when your config already has it. It also lets you choose the wallpaper tool
  (`setter`).

![The Unsplash tab (sample results)](https://raw.githubusercontent.com/keo99/panescape/main/docs/images/gui-unsplash.png)

The **Unsplash** tab finds new pictures (the screenshot shows made-up sample
results):

- **Search** Unsplash (landscape photos by default, since they suit a wide
  desk), or press **Suggest for me** for photos like the ones you have, based
  on your most used tags. Photos you already have are left out of the
  suggestions.
- **Choose a photo** to see it larger, with its photographer and a note on
  whether it's sharp enough for your desk.
- **Download to my pictures** saves it into `pictures/` (named like
  `ann-lee-Zx3abcDEF12-unsplash.jpg`). The photo's Unsplash tags are added,
  minus generic ones like "hd wallpapers", and the credit goes in
  `pictures/credits.yml`. The Pictures tab then shows "Photo by … on Unsplash".

### Your Unsplash access key

Searching needs a free key of your own: create an application at
[unsplash.com/oauth/applications](https://unsplash.com/oauth/applications) and
paste its **Access Key** (not the Secret key) into the tab. A new key allows 50
requests an hour, and search results are cached for an hour to save them.
Showing pictures doesn't count, and neither does the image file itself.
A download uses one request for the download report Unsplash requires,
plus one more with **Add Unsplash's tags** ticked, because search results
don't include tags. Unticked, the picture is tagged from your search words
and the photo's description instead.

How panescape looks after the key:

- **Checked before it's saved:** panescape makes one request with the key
  first, and stores it only if Unsplash accepts it.
- **Where it's stored:** in your system keyring (GNOME Keyring, KWallet, …)
  if you have one; otherwise in `~/.config/panescape/unsplash-key`, which
  only you can read. The environment variable `PANESCAPE_UNSPLASH_KEY`, if
  set, takes precedence.
- **Where it never goes:** your project folder, `setup.yml`, the window's
  settings or the cache. The window never shows it again after you save it.
- **Who receives it:** only `https://api.unsplash.com`, in a request header,
  never in a web address. panescape refuses redirects to anywhere else and
  removes the key from any error message.
- **Removing it:** **Remove key** deletes it from the keyring and from the
  file.

Following Unsplash's API guidelines, pictures are shown straight from
Unsplash's own links (never copied into a cache), every download is reported
to Unsplash so the photographer gets credit, and photographers and Unsplash
are credited with links.

## How a project folder looks

```
pictures/                     your source images (+ tags.yml, crops.yml, credits.yml)
configurations/my_desk.yml    physical description of a monitor setup
setup.yml                     what `panescape apply` puts on the desktop
backgrounds/<config>/<image>/
    1_left.png 2_centre.png … one native-resolution image per monitor
    preview.jpg               to-scale picture of the desk with bezels
    meta.json                 geometry used (mm, source pixel boxes, upscale)
    gnome-….png               the combined image GNOME displays
```

Commands run in the current folder. Point them elsewhere with `--root DIR` or
`PANESCAPE_ROOT`. Examples are in [`examples/`](https://github.com/keo99/panescape/tree/main/examples/).

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
panescape calibrate --watch             # millimetre test pattern while you tune the config
panescape status                        # configs, what's been made, what's applied
panescape setup                         # list the setups in setup.yml (* = active)
panescape setup evening --apply         # switch to another setup and apply it
panescape gui                           # open the window
panescape-apply …                       # same as `panescape apply …`
```

Use `--dry-run` to build everything without changing the wallpaper. Use `-c NAME` to
pick a configuration, and `--force` to re-render. For the best result, use a
source image at least as many pixels wide as your desk needs. `make` warns
when a screen would be enlarged.

## Configuration (`configurations/*.yml`)

Lengths are in mm unless you give a unit (`28in`, `2.5cm`). For a single row,
list the monitors **left to right**. Stacks, grids and other arrangements are
covered in [Layouts](#layouts-rows-stacks-and-grids).

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
  # png_compression: 1          # 0-9: higher = smaller files, much slower (same pixels)
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
| `right_of` / `left_of` / `below` / `above` | Place this monitor next to another one, by name (see [Layouts](#layouts-rows-stacks-and-grids)) |
| `gap` | Distance between this housing and the one it's placed next to (negative = overlap) |
| `v_pos`, `v_align`, `v_ref` | Side-by-side monitors: vertical offset (+ is up), which edges it's measured between (`center`/`top`/`bottom`), and the reference monitor |
| `h_pos`, `h_align`, `h_ref` | Stacked monitors: horizontal offset (+ is right), which edges it's measured between (`center`/`left`/`right`), and the reference monitor |
| `position` | `[x, y]` of the housing's top-left in mm (y down), for arrangements the keys above can't describe |
| `serial` / `connector` / `product` | Pin to a specific screen. Without these, screens are matched to the desktop by their arrangement |

`defaults:` at the top level sets `gap`, `bezel`, `v_align` or `h_align` for every monitor.

**Precision tips**

1. Use `model` or `active_area`. Nominal diagonals are rounded, and a "28 inch"
   panel is not exactly 28 inches.
2. Measure your bezels with a ruler. Spec-sheet bezels are estimates, because
   manufacturers rarely split top and bottom.
3. Measure `v_pos` / `h_pos` between the edges that are easiest to measure, and set `v_align` / `h_align` to match.
4. Look at `preview.jpg`. It is drawn to scale.
5. Finish with `panescape calibrate --watch` (see [Calibrating](#calibrating)).

## Layouts: rows, stacks and grids

The first monitor is the anchor. Every other monitor is placed relative to
another one. The order in the file doesn't matter, as long as the references
don't go round in a circle.

| arrangement | how to write it |
|---|---|
| A row | List the monitors left to right with no placement key. Each sits right of the previous one, and `v_pos` is measured from the **first** monitor |
| Beside a monitor | `right_of: NAME` or `left_of: NAME`, plus `gap`. `v_pos` is measured from NAME, unless `v_ref` says otherwise |
| Above or below | `below: NAME` or `above: NAME`, plus `gap` (vertical) and `h_pos` / `h_align` (horizontal offset from NAME, or `h_ref`) |
| Anything else | `position: [x, y]`, the housing's top-left in mm |

A 2×2 grid ([full example](https://github.com/keo99/panescape/blob/main/examples/configurations/grid-2x2.yml)):

```yaml
monitors:
  - name: top-left
    model: …
  - name: top-right
    right_of: top-left
    gap: 1
    v_align: top            # tops level
  - name: bottom-left
    below: top-left
    gap: 2
    h_align: left           # left edges level
  - name: bottom-right
    right_of: bottom-left
    gap: 1
    v_align: top
```

Other examples: [a laptop under a monitor](https://github.com/keo99/panescape/blob/main/examples/configurations/laptop-under-monitor.yml),
[portrait-landscape-portrait](https://github.com/keo99/panescape/blob/main/examples/configurations/portrait-landscape-portrait.yml).

`panescape detect --write NAME` writes this structure for you from the desktop's
arrangement (rows, stacks and grids), with the measured numbers left at 0. When
applying, config monitors are matched to the desktop's screens by **arrangement**,
so a grid pairs up correctly. Use `serial:` to pin a monitor if two arrangements
could be confused.

## Calibrating

In the window, tick **Calibrate on my screens** on the Layout tab and drag
the monitors until the lines meet. From the command line:

```bash
panescape calibrate --watch      # Ctrl+C puts your wallpaper back
panescape calibrate              # apply once; undo with --restore (or panescape apply)
```

This puts a test pattern across your screens, drawn in physical millimetres
from your configuration:

- **Cyan horizontal and yellow vertical lines** every 50 mm.
- **Orange and green 45° lines** every 100 mm.
- **A magenta circle.**
- **Rulers** along every screen edge, in desk millimetres. On screens dense
  enough, they have 1 mm ticks.
- **Each screen's settings**, plus the configured distance to its neighbours' lit
  areas ("lit gap").

With `--watch`, editing and saving the configuration re-applies the pattern in
about a second. When the config matches reality, every line runs straight
across the bezels. To fix it:

1. **Spacing.** Measure the lit gaps with a ruler and adjust `gap` (or `bezel`)
   until they match the numbers shown.
2. **Side-by-side screens.** Read both edge rulers at the same physical height,
   then: `v_pos += this screen's reading − the other screen's reading`.
3. **Stacked screens.** Use the yellow lines and the top/bottom rulers, then:
   `h_pos += the other screen's reading − this screen's reading`.
4. **Check.** The diagonals and the circle should now be straight and continuous.

**Angled screens:** if your outer screens are turned or tilted towards you,
they don't lie in one plane, so the ruler readings don't line up exactly.
Instead, tune from your seat, matching lines near eye level. Lines far above or
below that height will drift slightly on the angled screens.

Without a supported desktop, the pattern is written as per-monitor images in
`backgrounds/<config>/calibration/`.

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

### Several setups

`setup.yml` can hold several named setups, for example a calm one for work
and a colourful one for the evening:

```yaml
active: work              # what apply / sync / the slideshow follow
setter: swww              # keys out here are shared by every setup
setups:
  work:
    configuration: desk_H
    filter: minimal + !people
    interval: 1h
  evening:
    configuration: desk_H
    filter: landscape + sunset
    interval: 10m
```

Switch with `panescape setup evening` (add `--apply` to apply straight away),
`panescape apply --setup evening`, or the Setup menu in the window. A
`setup.yml` without `setups:`, like the one above, is a single setup called
`default`. Creating a second setup in the window converts the file for you,
and comments are kept.

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

Tags are case-insensitive, untagged images match `!x`, and `!*` matches
pictures with no tags at all. Comments you add to `tags.yml` are kept when the
command line or the window edits it.

## Framing a picture

By default each picture is centred and scaled to fill the desk. To frame one
differently, drag it in the window's Pictures tab, or write it in
`pictures/crops.yml`:

```yaml
tall-photo.jpg: {anchor: [0.5, 0.2], zoom: 1.2}    # higher up, a little closer
panorama.jpg: {fit: contain}                       # whole picture, black bars
```

Later settings win: the configuration's `crop:`, then `setup.yml`'s `crop:`,
then `crops.yml`, then an image entry in `setup.yml`'s `images:` list, then
`--anchor`/`--zoom`/`--fit` on the command line.

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

## sway and Hyprland

On these compositors every output gets its own native-resolution slice, so no
combined image is needed. panescape reads the layout from `swaymsg -t get_outputs`
or `hyprctl monitors -j`. It sets the wallpaper with the first of these tools
that works:

| setter | how |
|---|---|
| `sway` | sway's own `output NAME bg FILE fill` (sway runs swaybg itself) |
| `hyprpaper` | `hyprctl hyprpaper wallpaper` (start hyprpaper in your Hyprland autostart) |
| `swww` | `swww img --outputs NAME`, with a fade for slideshow transitions (needs `swww-daemon` running) |
| `swaybg` | a `swaybg` process that panescape starts and replaces |

To force one, set `setter: swww` in `setup.yml` or `PANESCAPE_SETTER=swww` in
the environment.

**Slideshows:** these desktops have no synced multi-monitor slideshow, so
`panescape slideshow` changes every output together on schedule. `apply`
starts it in the background when you apply a slideshow. Add it to your
compositor's autostart so it survives logging out:

```
# sway (~/.config/sway/config)
exec panescape --root ~/panescape slideshow
# Hyprland
exec-once = panescape --root ~/panescape slideshow
```

It picks up `apply`, `add`, `sync` and `tag` changes by itself and pauses while
`calibrate` is running. `panescape slideshow --stop` stops it, and
`panescape status` shows whether it's running. Everything else (`calibrate`,
`detect`, tags, rules) works the same as on GNOME.

## KDE Plasma

Plasma has a wallpaper per screen, so each monitor gets its native slice.
panescape reads the layout with `kscreen-doctor -j` and sets the wallpapers
through Plasma's scripting interface (`org.kde.PlasmaShell.evaluateScript` on
D-Bus), for every activity. Plasma numbers screens instead of naming them, so
outputs are matched to Plasma's screens by position.

**Slideshows:** Plasma's own slideshow runs each screen separately, so it can't
keep them in step. As on sway and Hyprland, `panescape slideshow` switches all
screens together. `apply` starts it, and
`panescape slideshow --autostart` adds a login entry in `~/.config/autostart`
(`--no-autostart` removes it).

## Other desktops

`backgrounds/<config>/<image>/N_name.png` are plain per-monitor images that you
can set with any tool, for example `feh`, Cinnamon or HydraPaper. Backends
for more desktops are welcome; see `src/panescape/backends/`.

## Adding a monitor model

Add an entry to `src/panescape/models.py`, using the spec sheet's active area,
outer size without stand, and the bezels if you've measured them. Then open a PR.

## Development

```bash
pip install -e '.[dev,gui]'
pytest                       # GUI tests run headless (QT_QPA_PLATFORM=offscreen)
ruff check .
python docs/make_images.py   # rebuild the README images and the window screenshots
```

The latest unreleased version: `pipx install 'panescape[gui] @ git+https://github.com/keo99/panescape'`.

**Releasing:** set `__version__` in `src/panescape/__init__.py`, commit, then
`git tag v0.1.0 && git push origin main v0.1.0`. The release workflow checks
that the tag matches the version, runs the tests, and publishes to PyPI.

## Credits

README images use "Panorama of the valley of the Triglav mountain massif" by
otrow_photography, released under
[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) via
[Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Panorama_of_the_valley_of_the_Triglav_mountain_massif.jpg).

## License

MIT © Kieran Ralston
