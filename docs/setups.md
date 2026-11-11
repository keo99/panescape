# Setups and Slideshows

`setup.yml` says what goes on the desktop: which layout, which pictures, and
whether it's one picture or a slideshow. The window's Apply tab edits it for
you; this page describes the file itself.

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

## Several Setups

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

## Keeping the Slideshow Up to Date

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
