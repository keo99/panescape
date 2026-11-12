# Configuration

Lengths are in mm unless you give a unit (`28in`, `2.5cm`). For a single row,
list the monitors **left to right**. Stacks, grids and other arrangements are
covered in [Layouts](layouts.md).

```yaml
name: desk_H
monitors:
  - name: left
    model: Samsung U28E590      # built-in specs (see [Adding a Monitor Model](development.md#adding-a-monitor-model))
    serial: ABC1234567          # pin to a physical screen (from `panescape detect`)
    rotation: portrait-right    # landscape | portrait-right | portrait-left | landscape-flipped
    bezel: {left: 24, right: 16, top: 20, bottom: 20}   # measured, as mounted

  - name: centre
    resolution: FHD             # or 1920x1080, 4K, QHD, …; always the panel's native mode
    size: 24in                  # diagonal; or active_area: [527.04, 296.46] (landscape, mm)
    bezel: 6                    # all sides; or [top, right, bottom, left]
    gap: 0                      # mm of air between the frames; bezels not included
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
| `right_of` / `left_of` / `below` / `above` | Place this monitor next to another one, by name (see [Layouts](layouts.md)) |
| `gap` | The air gap in mm between this monitor's frame and the one it's placed next to, as measured with a ruler. Bezels are **not** part of it (see below) |
| `v_pos`, `v_align`, `v_ref` | Side-by-side monitors: vertical offset (+ is up), which edges it's measured between (`center`/`top`/`bottom`), and the reference monitor |
| `h_pos`, `h_align`, `h_ref` | Stacked monitors: horizontal offset (+ is right), which edges it's measured between (`center`/`left`/`right`), and the reference monitor |
| `position` | `[x, y]` of the housing's top-left in mm (y down), for arrangements the keys above can't describe |
| `angle`, `tilt` | Only with a `viewer` section: degrees turned towards you, and leaning back (see [Angled and Tilted Screens](angled.md)) |
| `serial` / `connector` / `product` | Pin to a specific screen. Without these, screens are matched to the desktop by their arrangement |

**Gaps and bezels add up.** The distance between two screens' lit areas is one
screen's bezel, plus the `gap`, plus the other screen's bezel. So `gap: 2` means a
real 2 mm space between the frames, and `gap: 0` means the frames touch. A
negative gap is only for frames that actually overlap, with one in front of the
other; it doesn't mean the bezels are thinner than they are.

`defaults:` at the top level sets `gap`, `bezel`, `v_align` or `h_align` for every monitor.

`viewer:` at the top level is an opt-in for screens turned towards you or tilted
back: it makes the picture look right from your seat. See
[Angled and Tilted Screens](angled.md).

## Precision Tips

1. Use `model` or `active_area`. Nominal diagonals are rounded, and a "28 inch"
   panel is not exactly 28 inches.
2. Measure your bezels with a ruler. Spec-sheet bezels are estimates, because
   manufacturers rarely split top and bottom.
3. Measure `v_pos` / `h_pos` between the edges that are easiest to measure, and set `v_align` / `h_align` to match.
4. Look at `preview.jpg`. It is drawn to scale.
5. Finish with `panescape calibrate --watch` (see [Calibrating](calibrating.md)).
