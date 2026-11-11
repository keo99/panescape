# Desktops

panescape detects your desktop and applies the wallpaper in the way that suits
it. This page explains how, for each one.

## GNOME

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

## Other desktops

`backgrounds/<config>/<image>/N_name.png` are plain per-monitor images that you
can set with any tool, for example `feh`, Cinnamon or HydraPaper. Backends
for more desktops are welcome; see `src/panescape/backends/`.
