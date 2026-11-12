"""Command line interface.

    panescape init                      scaffold a project folder
    panescape detect [--write NAME]     show the desktop's monitors / write a config skeleton
    panescape make [IMAGES] [--apply]   crop + slice images into backgrounds/
    panescape apply [IMAGES]            apply one image, or a slideshow (default: setup.yml)
    panescape add IMAGES                pin images into the current slideshow
    panescape sync                      re-check tags/pictures/setup.yml, update the slideshow
    panescape tag IMAGES -a T -r T      add / remove tags (pictures/tags.yml)
    panescape tags [FILTER]             list images and their tags
    panescape calibrate [--watch]       millimetre test pattern for tuning the configuration
    panescape slideshow                 sway/Hyprland/KDE: run the slideshow (autostart this)
    panescape status                    show configurations, pictures and what is applied
    panescape setup [NAME]              list the named setups in setup.yml / switch to one
    panescape gui                       open the window (layout editor, tags, setups)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from . import __version__, backends, models, yamledit
from .config import ROTATION_NAMES, ConfigError
from .project import RESTORE_FILE, Project, ProjectError
from .units import parse_duration

EXAMPLE_SETUP = """\
# panescape setup: what `panescape apply` (with no arguments) puts on the desktop.
configuration: {config}   # a file in ./configurations (without .yml)
mode: slideshow            # single | slideshow
images: all                # 'all' (everything in ./pictures) or a list, e.g.
#  - mountains.jpg
#  - "city-*.png"           # globs work
#  - file: tall-photo.jpg   # per-image crop tweaks
#    anchor: top            # center, top, bottom, left, right, top-left, ... or [x, y] in 0..1
#    zoom: 1.2              # >1 zooms in
filter: ""                 # tag filter on 'images', e.g. landscape + water + !mountains
interval: 15m              # time per image in a slideshow (s, m, h)
transition: 2s             # cross-fade length
shuffle: false
# crop:                    # defaults for every image (override per image above)
#   anchor: center
#   fit: cover             # cover (fill, crop edges) | contain (show all, black bars)
"""


def _project(args) -> Project:
    root = args.root or os.environ.get("PANESCAPE_ROOT") or "."
    return Project(root)


def _backend(required=True):
    be = backends.detect()
    if be is None and required:
        raise ProjectError("no supported desktop found (GNOME, KDE Plasma, sway and Hyprland are supported); "
                           "the slices in backgrounds/ can still be set with your own tool")
    return be


def _crop_override(args) -> dict:
    out = {}
    if getattr(args, "anchor", None):
        a = args.anchor
        out["anchor"] = [float(v) for v in a.split(",")] if "," in a else a
    if getattr(args, "zoom", None):
        out["zoom"] = args.zoom
    if getattr(args, "fit", None):
        out["fit"] = args.fit
    return out


def cmd_init(args) -> None:
    proj = _project(args)
    for d in (proj.pictures, proj.configurations, proj.backgrounds):
        d.mkdir(parents=True, exist_ok=True)
    if not proj.setup_file.exists():
        configs = sorted(proj.configurations.glob("*.y*ml"))
        proj.setup_file.write_text(EXAMPLE_SETUP.format(config=configs[0].stem if configs else "my_desk"))
        print(f"wrote {proj.setup_file}")
    print(f"project ready in {proj.root}\n  1. put images in pictures/\n"
          f"  2. run 'panescape detect --write my_desk' and edit configurations/my_desk.yml\n"
          f"  3. run 'panescape make' then 'panescape apply'")


def cmd_detect(args) -> None:
    be = _backend()
    lay = be.get_layout()
    mons = sorted(lay.monitors, key=lambda m: (m.center_x, m.y))
    print(f"Desktop layout ({'logical' if lay.logical else 'physical'} pixels), left to right:")
    for m in mons:
        known = models.lookup(m.product)
        print(f"  {m.connector:<7} {m.display_name:<32} {m.product:<14} serial {m.serial:<12} "
              f"{m.mode[0]}x{m.mode[1]} {ROTATION_NAMES[m.rotation]:<17} scale {m.scale:g} "
              f"at ({m.x},{m.y})  {'[known model]' if known else ''}")
    if not args.write:
        return
    path = _project(args).write_detected_config(args.write, lay.monitors, overwrite=args.overwrite)
    print(f"wrote {path}")


def _print_render(proj, r) -> None:
    rel = r.out_dir.relative_to(proj.root) if r.out_dir.is_relative_to(proj.root) else r.out_dir
    print(f"{'cached  ' if r.skipped else 'rendered'} {r.job.source.name} -> {rel}/")
    for w in r.warnings:
        print(f"  warning: {w}")


def cmd_make(args) -> None:
    proj = _project(args)
    cfg = proj.load_config(args.config)
    for m in cfg.monitors:
        for n in m.notes:
            print(f"note [{m.name}]: {n}")
    jobs = proj.filter_jobs(proj.resolve_images(args.images or None), args.filter)
    override = _crop_override(args)
    for job in jobs:
        job.crop.update(override)
    for r, _ in proj.render_many(cfg, jobs, force=args.force):
        _print_render(proj, r)
    if args.apply:
        rule = _cli_rule(proj, args, mode="slideshow" if args.slideshow else "auto")
        proj.run(_backend(), rule, dry_run=args.dry_run)


def _cli_specs(proj: Project, images: list[str]) -> list[str]:
    """Keep globs as globs (so new matching files join later); pin names to paths."""
    out = []
    for item in images:
        if any(c in item for c in "*?["):
            out.append(item)
        else:
            out += [str(j.source) for j in proj.resolve_images([item])]
    return out


def _cli_rule(proj: Project, args, mode: str) -> dict:
    setup = proj.load_setup()
    return {
        "source": "cli",
        "images": _cli_specs(proj, args.images) if args.images else "all",
        "filter": args.filter, "mode": mode,
        "configuration": str(proj.config_path(args.config)),
        "interval": args.interval or setup.get("interval", "10m"),
        "transition": args.transition or setup.get("transition", "2s"),
        "shuffle": bool(args.shuffle or setup.get("shuffle", False)),
        "crop": _crop_override(args) or None,
    }


def cmd_apply(args) -> None:
    proj = _project(args)
    if getattr(args, "setup", None):
        proj.use_setup(args.setup)
    mode = "slideshow" if args.slideshow else ("single" if args.single else None)
    if args.images:
        rule = _cli_rule(proj, args, mode or "auto")
    else:
        if not proj.load_setup():
            raise ProjectError(f"no images given and no {proj.setup_file.name}; run 'panescape init'")
        # follow setup.yml; anything given on the command line overrides it
        rule = {"source": "setup", "configuration": args.config, "filter": args.filter, "mode": mode,
                "interval": args.interval, "transition": args.transition,
                "shuffle": True if args.shuffle else None, "crop": _crop_override(args) or None}
        rule = {k: v for k, v in rule.items() if v is not None}
    proj.run(_backend(), rule, force=args.force, dry_run=args.dry_run)


def cmd_setup(args) -> None:
    proj = _project(args)
    if args.name:
        proj.use_setup(args.name)
        print(f"active setup: {args.name}")
        if args.apply:
            proj.run(_backend(), {"source": "setup"})
        return
    names = proj.setup_names()
    if not names:
        raise ProjectError(f"no {proj.setup_file.name}; run 'panescape init'")
    active = proj.active_setup()
    for n in names:
        try:
            cfg = proj.load_setup(n).get("configuration", "(none)")
        except ProjectError as e:
            cfg = f"error: {e}"
        print(f"{'*' if n == active else ' '} {n:<20} configuration: {cfg}")


def cmd_gui(args) -> None:
    if args.install_launcher or args.remove_launcher:
        from . import launcher
        if args.install_launcher:
            print(f"added panescape to the app menu: {launcher.install()}")
        else:
            gone = launcher.remove()
            print("removed " + ", ".join(map(str, gone)) if gone else "no app-menu entry to remove")
        return
    try:
        from .gui import main as gui_main
    except ImportError as e:
        raise ProjectError(f"the GUI needs PySide6: pip install 'panescape[gui]' ({e})") from e
    sys.exit(gui_main(args.root or os.environ.get("PANESCAPE_ROOT")))


def cmd_add(args) -> None:
    proj = _project(args)
    if not args.images and not args.filter:
        raise ProjectError("give images and/or --filter")
    jobs = proj.filter_jobs(proj.resolve_images(args.images or None), args.filter)
    proj.sync(_backend(), add=[str(j.source) for j in jobs], interval=args.interval,
              transition=args.transition, dry_run=args.dry_run)


def cmd_sync(args) -> None:
    proj = _project(args)
    proj.sync(_backend(), force=args.force, dry_run=args.dry_run)


def cmd_tag(args) -> None:
    from .tags import edit_tags, load_tags, norm_tag, tags_for
    proj = _project(args)
    if not (args.add or args.remove or args.clear):
        raise ProjectError("nothing to do: use -a TAG, -r TAG or --clear")
    tags = load_tags(proj.tags_file)
    changes = {}
    for job in proj.resolve_images(args.images):
        name = job.source.name
        if job.source.parent.resolve() != proj.pictures.resolve():
            raise ProjectError(f"{name}: only images in {proj.pictures} can be tagged")
        remove = set(tags.get(name, set())) if args.clear else set()
        changes[name] = ({norm_tag(t) for t in args.add or []}, remove | {norm_tag(t) for t in args.remove or []})
    proj.pictures.mkdir(parents=True, exist_ok=True)
    for w in edit_tags(proj.tags_file, changes):
        print(f"note: {w}")
    tags = load_tags(proj.tags_file)
    for name in changes:
        print(f"{name}: {', '.join(sorted(tags_for(name, tags))) or '(no tags)'}")
    be = backends.detect()
    if args.no_sync or not proj.load_state() or be is None:
        return
    print("updating the current wallpaper:")
    try:
        proj.sync(be)
    except ProjectError as e:
        print(f"  not updated: {e}")


def cmd_tags(args) -> None:
    from .tags import compile_filter, load_tags, tags_for
    proj = _project(args)
    tags = load_tags(proj.tags_file)
    pred = compile_filter(args.filter)[0] if args.filter else (lambda s: True)
    counts: dict[str, int] = {}
    shown = 0
    for p in proj.all_pictures():
        ts = tags_for(p.name, tags)
        for t in ts:
            counts[t] = counts.get(t, 0) + 1
        if pred(ts):
            shown += 1
            print(f"  {p.name:<40} {', '.join(sorted(ts)) or '-'}")
    total = len(proj.all_pictures())
    print(f"{shown} of {total} images" + (f" match {args.filter!r}" if args.filter else ""))
    if counts and not args.filter:
        print("tags: " + ", ".join(f"{t} ({n})" for t, n in sorted(counts.items())))


PHOTO_GUIDE = """\
Each screen now shows a white panel on black. Sit where you normally sit, hold
your phone where your eyes are, and take a photo with the main (1x) camera,
every screen fully in the frame. Then copy the photo to this computer and run:
    panescape calibrate --from-photo PHOTO.jpg            (shows what would change)
    panescape calibrate --from-photo PHOTO.jpg --write    (saves it to the layout)
and 'panescape calibrate --restore' to put your wallpaper back."""


def _calibrate_from_photo(path: Path, photo: Path, write: bool, overlay: str | None) -> None:
    from . import photocal
    from .project import ProjectError
    hint = photocal.missing()
    if hint:
        raise ProjectError(hint)
    doc = yamledit.load(path)
    raw = yamledit.plain(doc)
    try:
        result = photocal.analyse(photo, raw)
    except photocal.PhotoError as e:
        raise ProjectError(f"{photo.name}: {e}") from None
    print(f"matched the photo to within {result.rms_mm:.1f} mm on average "
          f"(worst: {result.worst[0]}, {result.worst[1]:.1f} mm)")
    if overlay:
        photocal.overlay(result, result.photo).save(overlay)
        print(f"wrote {overlay} (green: what was found, pink: where the fitted layout puts the corners)")
    found = photocal.changes(result, raw)
    if result.flat and "viewer" not in raw:
        print("your screens came out flat (no angle or tilt over a degree)")
    if not found:
        print("the layout already matches the photo")
        return
    for name, key, old, new in found:
        where = f"{name}.{key}" if name else f"viewer.{key}"
        print(f"  {where:<20} {'(none)' if old is None else old} -> {new}")
    if write:
        photocal.apply_changes(doc, found)
        yamledit.save(path, doc)
        print(f"saved to {path}")
    else:
        print("run again with --write to save these to the layout")


def cmd_calibrate(args) -> None:
    from . import calibrate
    proj = _project(args)
    be = backends.detect()

    def restore() -> None:
        msg = proj.end_calibration(be)
        if msg:
            print(msg)

    if args.restore:
        if not (proj.backgrounds / RESTORE_FILE).exists():
            raise ProjectError("nothing to restore (no calibration pattern is applied)")
        restore()
        return

    path = proj.config_path(args.config)
    if args.from_photo:
        _calibrate_from_photo(path, Path(args.from_photo), args.write, args.overlay)
        return

    def build() -> None:
        print(proj.apply_calibration(be, proj.load_config(str(path)), photo=args.photo_pattern))

    from .backends.base import competing_tools
    for w in competing_tools():
        print(f"warning: {w}")
    proj.begin_calibration(be)
    try:
        build()
    except Exception:
        restore()
        raise
    if args.photo_pattern:
        print("\n" + PHOTO_GUIDE)
        return
    print("\n" + calibrate.GUIDE)
    if not args.watch:
        if be is not None:
            print("\nWhen you are done: 'panescape calibrate --restore' (or 'panescape apply').")
        return

    print(f"\nwatching {path} for changes. Ctrl+C to stop"
          + ("" if args.keep else " and restore your wallpaper") + ".")
    last = path.stat().st_mtime_ns
    try:
        while True:
            time.sleep(0.5)
            try:
                now = path.stat().st_mtime_ns
            except FileNotFoundError:  # editors that replace the file
                continue
            if now == last:
                continue
            last = now
            try:
                build()
            except (ConfigError, ProjectError, ValueError, yamledit.YAMLError) as e:
                print(f"  config error, not applied: {e}")
    except KeyboardInterrupt:
        print()
        if not args.keep:
            restore()


def cmd_slideshow(args) -> None:
    proj = _project(args)
    be = _backend()
    if getattr(be, "KIND", "spanned") != "per_output":
        print("this desktop plays panescape slideshows itself; nothing to run")
        return
    if args.autostart or args.no_autostart:
        from .project import autostart_file, install_autostart
        if args.no_autostart:
            autostart_file().unlink(missing_ok=True)
            print(f"removed {autostart_file()}")
        else:
            print(f"wrote {install_autostart(proj.root)} (starts the runner when you log in)")
        return
    if args.stop:
        pid = proj.runner_pid()
        if pid:
            os.kill(pid, 15)
            print(f"stopped slideshow runner (pid {pid})")
        else:
            print("no slideshow runner is running")
        return
    other = proj.runner_pid()
    if not args.once and other and other != os.getpid():  # (whoever started us may have written our pid)
        print(f"a slideshow runner is already running (pid {other})")
        return
    if not args.once:
        print(f"slideshow runner for {proj.root} (pid {os.getpid()}); Ctrl+C to stop")
    proj.run_slideshow(be, once=args.once)


def cmd_status(args) -> None:
    proj = _project(args)
    print(f"project:        {proj.root}")
    configs = sorted(proj.configurations.glob("*.y*ml"))
    print(f"configurations: {', '.join(c.stem for c in configs) or '(none)'}")
    names = proj.setup_names()
    if len(names) > 1:
        print(f"setups:         {', '.join(names)} (active: {proj.active_setup()})")
    print(f"configuration:  {proj.load_setup().get('configuration', '(none)')} (from setup.yml)")
    pics = proj.all_pictures()
    print(f"pictures:       {len(pics)}")
    for p in pics:
        done = [c.stem for c in configs if (proj.backgrounds / c.stem / p.stem / "meta.json").exists()]
        print(f"  {p.name:<40} {'made for: ' + ', '.join(done) if done else 'not made yet'}")
    state = proj.load_state()
    if state:
        print(f"applied:        {state['mode']} of {len(state.get('order', state.get('images', [])))} image(s), "
              f"config {Path(state['configuration']).stem}, at {state['applied']}")
    be = backends.detect()
    if be:
        try:
            if hasattr(be, "current_uri"):
                print(f"desktop uri:    {be.current_uri()}")
            if getattr(be, "KIND", "") == "per_output":
                pid = proj.runner_pid()
                print(f"slideshow runner: {f'running (pid {pid})' if pid else 'not running'}")
        except Exception:  # noqa: BLE001 - status is best effort
            pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="panescape", description="Physically accurate wallpapers spanning "
                                "monitors of different sizes, resolutions and orientations.")
    p.add_argument("--version", action="version", version=f"panescape {__version__}")
    p.add_argument("--root", help="project folder (default: $PANESCAPE_ROOT or current directory)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="create pictures/, configurations/, backgrounds/ and setup.yml")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("detect", help="show the desktop's monitors; optionally write a config skeleton")
    s.add_argument("--write", metavar="NAME", help="write configurations/NAME.yml")
    s.add_argument("--overwrite", action="store_true")
    s.set_defaults(func=cmd_detect)

    def crop_args(s):
        s.add_argument("--anchor", help="crop anchor: center, top, bottom, left, right, top-left, ... or X,Y (0..1)")
        s.add_argument("--zoom", type=float, help="zoom factor (>1 crops tighter)")
        s.add_argument("--fit", choices=["cover", "contain"])

    def apply_args(s):
        s.add_argument("--interval", help="time per slideshow image, e.g. 30s, 10m, 1h")
        s.add_argument("--transition", help="cross-fade time, e.g. 2s (0 for none)")
        s.add_argument("--shuffle", action="store_true")
        s.add_argument("--dry-run", action="store_true", help="build everything but don't change the wallpaper")
        s.add_argument("--filter", help="tag filter, e.g. 'landscape + water + !mountains' (overrides setup.yml)")

    s = sub.add_parser("make", help="crop and slice images (default: everything in pictures/)")
    s.add_argument("images", nargs="*")
    s.add_argument("-c", "--config", help="configuration name or path")
    s.add_argument("-f", "--force", action="store_true", help="re-render even if up to date")
    s.add_argument("--apply", action="store_true", help="apply afterwards (several images = slideshow)")
    s.add_argument("--slideshow", action="store_true", help="with --apply: slideshow even for one image")
    crop_args(s)
    apply_args(s)
    s.set_defaults(func=cmd_make)

    s = sub.add_parser("apply", help="apply images (default: what setup.yml says)")
    s.add_argument("images", nargs="*")
    s.add_argument("-c", "--config")
    s.add_argument("-f", "--force", action="store_true")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--slideshow", action="store_true")
    g.add_argument("--single", action="store_true", help="only the first image")
    s.add_argument("-s", "--setup", help="make this named setup (in setup.yml) the active one first")
    crop_args(s)
    apply_args(s)
    s.set_defaults(func=cmd_apply)

    s = sub.add_parser("add", help="pin images into the current slideshow (kept even if they stop matching; "
                           "turns a single image into a slideshow)")
    s.add_argument("images", nargs="*")
    s.add_argument("--filter", help="pin every picture matching this tag filter now")
    s.add_argument("-c", "--config")
    s.add_argument("--interval")
    s.add_argument("--transition")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("setup", help="list the setups in setup.yml, or switch to one")
    s.add_argument("name", nargs="?", help="setup to make active")
    s.add_argument("--apply", action="store_true", help="apply it straight away")
    s.set_defaults(func=cmd_setup)

    s = sub.add_parser("gui", help="open the panescape window (needs: pip install 'panescape[gui]')")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--install-launcher", action="store_true", help="add panescape to your app menu, with its icon")
    g.add_argument("--remove-launcher", action="store_true", help="remove the app-menu entry and icon")
    s.set_defaults(func=cmd_gui)

    s = sub.add_parser("sync", help="re-check the applied selection (tags, pictures, setup.yml) and update")
    s.add_argument("-f", "--force", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("tag", help="add/remove tags on images (stored in pictures/tags.yml)")
    s.add_argument("images", nargs="+", help="names, paths or globs in pictures/")
    s.add_argument("-a", "--add", nargs="+", metavar="TAG")
    s.add_argument("-r", "--remove", nargs="+", metavar="TAG")
    s.add_argument("--clear", action="store_true", help="remove all tags first")
    s.add_argument("--no-sync", action="store_true", help="don't update the running slideshow")
    s.set_defaults(func=cmd_tag)

    s = sub.add_parser("tags", help="list images and tags, optionally filtered")
    s.add_argument("filter", nargs="?", help="e.g. 'landscape + water + !mountains'")
    s.set_defaults(func=cmd_tags)

    s = sub.add_parser("calibrate", help="apply a millimetre test pattern to check and tune the configuration")
    s.add_argument("-c", "--config", help="configuration name or path")
    s.add_argument("-w", "--watch", action="store_true",
                   help="re-apply whenever the configuration file is saved; Ctrl+C restores your wallpaper")
    s.add_argument("--keep", action="store_true", help="with --watch: leave the pattern up when stopping")
    s.add_argument("--restore", action="store_true", help="put back the wallpaper from before calibrating")
    s.add_argument("--photo-pattern", action="store_true",
                   help="show the pattern for calibrating from a photo (white panels on black)")
    s.add_argument("--from-photo", metavar="PHOTO",
                   help="measure the layout from a photo of the photo pattern, taken from your seat "
                        "(needs the 'photo' extra)")
    s.add_argument("--write", action="store_true", help="with --from-photo: save the results to the layout")
    s.add_argument("--overlay", metavar="FILE", help="with --from-photo: save the photo marked with what was found")
    s.set_defaults(func=cmd_calibrate)

    s = sub.add_parser("slideshow", help="sway/Hyprland/KDE: switch slideshow images on schedule "
                       "(started automatically by apply; put it in your compositor's autostart)")
    s.add_argument("--once", action="store_true", help="show the image due now and exit")
    s.add_argument("--stop", action="store_true", help="stop a running slideshow runner")
    s.add_argument("--autostart", action="store_true",
                   help="start the runner at login via ~/.config/autostart (KDE and most desktops)")
    s.add_argument("--no-autostart", action="store_true", help="remove that autostart entry")
    s.set_defaults(func=cmd_slideshow)

    s = sub.add_parser("status", help="show what is configured, made and applied")
    s.set_defaults(func=cmd_status)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if getattr(args, "interval", None):
            parse_duration(args.interval)
        args.func(args)
    except (ProjectError, ConfigError, ValueError, FileNotFoundError) as e:
        print(f"panescape: error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # backend errors etc.
        if type(e).__name__ == "BackendError":
            print(f"panescape: error: {e}", file=sys.stderr)
            return 1
        raise
    return 0


def main_apply() -> int:
    """Entry point for the separate ``panescape-apply`` command."""
    return main(["apply", *sys.argv[1:]])
