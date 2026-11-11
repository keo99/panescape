# Pictures, tags and framing

Put your pictures in the project's `pictures/` folder (the window's **Add
pictures…** copies them there). panescape never changes them: it writes its
results to `backgrounds/`.

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
