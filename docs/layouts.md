# Layouts: Rows, Stacks and Grids

The first monitor is the anchor. Every other monitor is placed relative to
another one. The order in the file doesn't matter, as long as the references
don't go round in a circle.

| arrangement | how to write it |
|---|---|
| A row | List the monitors left to right with no placement key. Each sits right of the previous one, and `v_pos` is measured from the **first** monitor |
| Beside a monitor | `right_of: NAME` or `left_of: NAME`, plus `gap`. `v_pos` is measured from NAME, unless `v_ref` says otherwise |
| Above or below | `below: NAME` or `above: NAME`, plus `gap` (vertical) and `h_pos` / `h_align` (horizontal offset from NAME, or `h_ref`) |
| Anything else | `position: [x, y]`, the housing's top-left in mm |

`gap` is the air between the two frames, as measured with a ruler. Each
monitor's bezels are added on top of it, so `gap: 0` means the frames touch.

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
