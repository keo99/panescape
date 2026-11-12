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
tilted, and where your eyes are, all from one phone photo. It needs the `photo`
extra ([Installing](install.md#calibrating-from-a-photo)).

1. On the Layout tab, press **From a photo…**, then **Show the photo pattern**.
   Each screen shows a white panel on black.
2. Sit where you normally sit. Hold your phone **where your eyes are** and take a
   photo with the main (1×) camera, with every screen fully in the frame. If room
   lights reflect in the screens, dim them.
3. Copy the photo to the computer and drop it onto the window.

panescape finds the four corners of every white panel, then adjusts the layout
until, seen from the camera, each corner lands where the photo shows it. You see
the photo marked with what it found (green) and where the measured layout puts
the corners (pink), the average error in millimetres, and a list of the numbers
it would change. **Use these numbers** puts them in the layout; press **Save** to
keep them.

From the command line:

```bash
panescape calibrate --photo-pattern                       # show the pattern
panescape calibrate --from-photo IMG_1234.jpg             # what would change
panescape calibrate --from-photo IMG_1234.jpg --write     # save it to the layout
panescape calibrate --from-photo IMG_1234.jpg --overlay found.jpg   # the marked photo
panescape calibrate --restore                             # put your wallpaper back
```

Good to know:

- **Your sizes must be right.** The photo measures where screens are, but not how
  big they are: that comes from each monitor's model or lit area. The bezels
  don't need to be exact, since what counts is the space between lit areas,
  which goes into `gap`.
- **Flat or angled:** if every screen comes out within a degree of flat, only the
  gaps and heights are changed. Otherwise the angles and your eye position are
  added as well, which switches on [Angled and Tilted Screens](angled.md).
- **On a flat desk,** one photo can't tell how far away your eyes were. That's fine:
  a flat desk doesn't need to know.
- **The main camera, not the wide one.** Wide-angle lenses bend straight lines
  near the edges of the photo.
- **Check the result** with the test pattern by eye afterwards.
