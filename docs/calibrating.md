# Calibrating

There are two ways. By eye: put a test pattern on your screens and adjust the
layout until its lines meet. Or [from a photo](#from-a-photo): take one picture
from your seat and let panescape measure everything.

## By Eye

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
they don't lie in one plane. Lines then only meet at eye level, and drift apart
above and below it. Tune the gaps and heights using the lines near eye level.
To make them meet at every height, switch on
[Angled and Tilted Screens](angled.md) and calibrate from your seat.

Without a supported desktop, the pattern is written as per-monitor images in
`backgrounds/<config>/calibration/`.

## From a Photo

panescape can measure the gaps, the heights, how far each screen is turned or
tilted, and where your eyes are, from phone photos taken from your seat. It
needs the `photo` extra ([Installing](install.md#calibrating-from-a-photo)).

1. On the Layout tab, press **From a photo…**, then **Show the photo pattern**.
   Each screen shows a white panel with dots on black: one dot for the first
   monitor, two for the second, and so on. panescape minimises itself so nothing
   covers the screens; move any other windows off them too.
2. Sit where you normally sit. Hold your phone **where your eyes are** and take a
   photo with every screen fully in the frame. If they don't all fit, take two or
   more photos from the same seat, turning the phone between them. Each screen
   must be fully in at least one photo, and each photo must share a screen with
   another. If room lights reflect in the screens, dim them.
3. Bring panescape back from the dock. The dialog comes back with it. Copy the
   photos to the computer and drop them onto the dialog.

panescape finds the four corners of every white panel and reads its dots to know
which monitor it is. Then it adjusts the layout until, seen from the phone, each
corner lands where the photos show it. You see the photos marked with what it
found (green) and where the measured layout puts the corners (pink), the average
error in millimetres, and a list of the numbers it would change. **Use these
numbers** puts them in the layout; press **Save** to keep them.

From the command line:

```bash
panescape calibrate --photo-pattern                         # show the pattern
panescape calibrate --from-photo IMG_1.jpg IMG_2.jpg        # what would change
panescape calibrate --from-photo IMG_1.jpg IMG_2.jpg --write          # save it to the layout
panescape calibrate --from-photo IMG_1.jpg --overlay found.jpg       # the marked photo
panescape calibrate --restore                               # put your wallpaper back
```

Good to know:

- **Top bars, docks, icons, glare and lamps** don't get in the way. The white
  panel stays well inside each screen, each edge is measured from the parts of it
  that are clear, and bright things that aren't panels are ignored. The row of dots
  in the middle of each panel must be visible.
- **If it can't measure,** it says which screens it found in each photo and shows
  them: green for screens found, orange for screens cut off at the photo's edge.
  The most common cause is the shared screen running off the edge of one photo.
- **Your sizes must be right.** The photos measure where screens are, not how big
  they are: that comes from each monitor's model or lit area. The bezels don't
  need to be exact, since what counts is the space between lit areas, which goes
  into `gap`.
- **Flat or angled:** if every screen comes out within a degree of flat, only the
  gaps and heights are changed. Otherwise the angles and your eye position are
  added as well, which switches on [Angled and Tilted Screens](angled.md).
- **On a flat desk,** the photos can't tell how far away your eyes were. That's
  fine: a flat desk doesn't need to know.
- **Prefer two photos to the wide-angle lens.** Wide-angle lenses bend straight
  lines, which throws the measurements off; phones only partly correct for it.
- **Check the result** with the test pattern by eye afterwards.
