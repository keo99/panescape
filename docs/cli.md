# Command line

Every command works on a project folder: the current folder, or the one given
with `--root DIR` or `PANESCAPE_ROOT`. `panescape COMMAND --help` lists every
option.

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

## The project folder

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
