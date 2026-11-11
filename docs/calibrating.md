# Calibrating

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
