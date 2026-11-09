"""A panescape project folder: pictures/, configurations/, backgrounds/, setup.yml."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from PIL import Image

from . import __version__
from .config import Configuration, load_configuration
from .layout import Layout, compute_layout
from .render import UPSCALE_WARN, CropSpec, Mapping, load_image, render_preview, save_image
from .units import parse_duration

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".avif", ".heic"}
STATE_FILE = ".panescape-state.json"
MAX_WORKERS = 4  # images rendered in parallel (each holds a decoded source + desktop image)
RESTORE_FILE = ".calibrate-restore.json"  # wallpaper saved by `panescape calibrate`


class ProjectError(RuntimeError):
    pass


@dataclass
class ImageJob:
    source: Path
    crop: dict = field(default_factory=dict)

    @property
    def stem(self) -> str:
        return self.source.stem


@dataclass
class Rendered:
    job: ImageJob
    out_dir: Path
    slices: dict           # monitor name -> Path
    key: str               # cache key of this render
    warnings: list
    skipped: bool
    crop: CropSpec | None = None
    layout: Layout | None = None
    mapping: Mapping | None = None   # decoded source, kept only while rendering


def _log(msg: str) -> None:
    print(msg, flush=True)


class Project:
    def __init__(self, root: str | Path = "."):
        self.root = Path(root).resolve()
        self.pictures = self.root / "pictures"
        self.backgrounds = self.root / "backgrounds"
        self.configurations = self.root / "configurations"
        self.setup_file = self.root / "setup.yml"

    # ------------------------------------------------------------------ files
    def load_setup(self) -> dict:
        if not self.setup_file.exists():
            return {}
        with open(self.setup_file) as f:
            return yaml.safe_load(f) or {}

    def config_path(self, name: str | None) -> Path:
        if name:
            p = Path(name)
            for cand in (p, self.configurations / p, self.configurations / f"{p}.yml",
                         self.configurations / f"{p}.yaml"):
                if cand.is_file():
                    return cand
            raise ProjectError(f"configuration {name!r} not found in {self.configurations}")
        setup_name = self.load_setup().get("configuration")
        if setup_name:
            return self.config_path(str(setup_name))
        found = sorted(self.configurations.glob("*.y*ml"))
        if len(found) == 1:
            return found[0]
        if not found:
            raise ProjectError(f"no configurations in {self.configurations} (try 'panescape detect --write NAME')")
        raise ProjectError("several configurations exist; pick one with -c NAME or set "
                           "'configuration:' in setup.yml")

    def load_config(self, name: str | None) -> Configuration:
        path = self.config_path(name)
        cfg = load_configuration(path)
        cfg.name = path.stem  # folder name follows the file name
        return cfg

    def all_pictures(self) -> list[Path]:
        if not self.pictures.is_dir():
            return []
        return sorted(p for p in self.pictures.iterdir() if p.suffix.lower() in IMAGE_EXTS and p.is_file())

    def resolve_images(self, items, strict: bool = True) -> list[ImageJob]:
        """Images by name (relative to pictures/), path, glob, or {file: ..., anchor: ...}.

        With ``strict=False`` missing files and empty globs are skipped (used when
        re-evaluating a saved slideshow rule after pictures were deleted).
        """
        if items in (None, "all", "*", []):
            jobs = [ImageJob(p) for p in self.all_pictures()]
            if not jobs and strict:
                raise ProjectError(f"no images in {self.pictures}")
            return jobs
        if isinstance(items, (str, dict)):
            items = [items]
        jobs: list[ImageJob] = []
        for item in items:
            crop = {}
            if isinstance(item, dict):
                crop = {k: v for k, v in item.items() if k in ("anchor", "zoom", "fit", "background")}
                item = item.get("file") or item.get("image")
                if not item:
                    raise ProjectError("image entries need a 'file' key")
            item = str(item)
            if any(c in item for c in "*?["):
                hits = [p for p in self.all_pictures() if fnmatch.fnmatch(p.name, item)]
                if not hits and strict:
                    raise ProjectError(f"no pictures match {item!r}")
                jobs += [ImageJob(p, dict(crop)) for p in hits]
                continue
            p = Path(item).expanduser()
            for cand in (p, self.pictures / p):
                if cand.is_file():
                    jobs.append(ImageJob(cand.resolve(), crop))
                    break
            else:
                stem_hits = [q for q in self.all_pictures() if q.stem == item]
                if len(stem_hits) == 1:
                    jobs.append(ImageJob(stem_hits[0], crop))
                elif not strict:
                    _log(f"  skipping {item}: file no longer exists")
                else:
                    raise ProjectError(f"image {item!r} not found (looked in {self.pictures})")
        return jobs

    @property
    def tags_file(self) -> Path:
        return self.pictures / "tags.yml"

    def filter_jobs(self, jobs: list[ImageJob], expr: str | None, strict: bool = True) -> list[ImageJob]:
        """Keep images whose tags (pictures/tags.yml) satisfy ``expr``."""
        if not expr:
            return jobs
        from .tags import compile_filter, load_tags, tags_for
        pred, terms = compile_filter(str(expr))
        tags = load_tags(self.tags_file)
        known = set().union(*tags.values()) if tags else set()
        unknown = sorted(t for t in terms if not any(c in t for c in "*?[") and t not in known)
        if unknown:
            _log(f"note: filter uses tags no image has: {', '.join(unknown)}")
        kept = [j for j in jobs if pred(tags_for(j.source.name, tags))]
        if not kept and strict:
            raise ProjectError(f"none of the {len(jobs)} candidate image(s) match filter {expr!r} "
                               f"(the filter narrows 'images' in setup.yml, or the images you named)")
        if strict:
            _log(f"filter {expr!r}: {len(kept)} of {len(jobs)} images")
        return kept

    def out_dir(self, cfg: Configuration, job: ImageJob) -> Path:
        return self.backgrounds / cfg.name / job.stem

    # ------------------------------------------------------------------ render
    def crop_for(self, cfg: Configuration, job: ImageJob, override: dict | None = None) -> CropSpec:
        setup_crop = self.load_setup().get("crop")
        return CropSpec.from_dicts(cfg.crop, setup_crop, job.crop, override)

    @staticmethod
    def _hash(data) -> str:
        return hashlib.sha1(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:10]

    def _slice_keys(self, job: ImageJob, crop: CropSpec, layout: Layout, fmt: str, quality: int) -> dict:
        """Per-monitor cache keys: only what changes that monitor's pixels.

        The source file, crop settings, the size of the cropped desk area (it
        decides the scale) and this monitor's lit rectangle and resolution. So
        edits that can't change the picture (serials, names of other monitors,
        comments, v_ref vs an equivalent layout) re-render nothing, and moving
        one monitor inside the desk's outline re-renders only that monitor.
        """
        st = job.source.stat()
        base = [__version__, str(job.source), st.st_size, st.st_mtime_ns, crop.as_dict(),
                round(layout.canvas.w, 3), round(layout.canvas.h, 3), fmt, quality]
        return {p.monitor.name: self._hash(base + [p.active.as_tuple(), p.monitor.pixels]) for p in layout}

    def render(self, cfg: Configuration, job: ImageJob, force: bool = False,
               crop_override: dict | None = None, preview: bool = True) -> Rendered:
        crop = self.crop_for(cfg, job, crop_override)
        layout = compute_layout(cfg)
        out = self.out_dir(cfg, job)
        meta_path = out / "meta.json"
        fmt = str(cfg.output.get("format", "png")).lower().lstrip(".")
        ext = "jpg" if fmt == "jpeg" else fmt
        quality = int(cfg.output.get("quality", 95))
        names = {m.name: out / f"{m.index + 1}_{m.name}.{ext}" for m in cfg.monitors}
        keys = self._slice_keys(job, crop, layout, fmt, quality)
        key = self._hash(sorted(keys.items()))

        old_meta: dict = {}
        if meta_path.exists():
            try:
                old_meta = json.loads(meta_path.read_text())
            except (OSError, json.JSONDecodeError):
                old_meta = {}
        old_monitors = {m.get("name"): m for m in old_meta.get("monitors", [])}

        def reusable(name: str) -> bool:
            om = old_monitors.get(name)
            return (not force and om is not None and om.get("slice_key") == keys[name]
                    and names[name].exists())

        rendered = Rendered(job, out, names, key, [], skipped=True)
        if all(reusable(n) for n in names) and (not preview or (out / "preview.jpg").exists()):
            rendered.warnings = old_meta.get("warnings", [])
            rendered.crop, rendered.layout = crop, layout
            return rendered

        out.mkdir(parents=True, exist_ok=True)
        mapping = Mapping(load_image(job.source), layout.canvas, crop)
        rendered.crop, rendered.layout, rendered.mapping = crop, layout, mapping
        keep = {"meta.json", "preview.jpg"} | {p.name for p in names.values()}
        for old in out.glob("*"):
            if old.is_file() and old.name not in keep:
                old.unlink()  # slices of removed monitors, stale composites
        warnings, monitors_meta = [], []
        for p in layout:
            m = p.monitor
            if reusable(m.name):
                up = old_monitors[m.name].get("upscale", 1.0)
            else:
                rendered.skipped = False
                tile, up = mapping.render(p.active, m.pixels)
                save_image(tile, names[m.name], fmt, quality,
                           int(cfg.output.get("png_compression", 1)))
            if up > UPSCALE_WARN:
                warnings.append(f"{m.name}: source is enlarged {up:.2f}x (image has fewer pixels than the screen)")
            monitors_meta.append({
                "name": m.name, "slice_key": keys[m.name], "pixels": m.pixels, "rotation": m.rotation,
                "active_mm": p.active.as_tuple(), "housing_mm": p.outer.as_tuple(),
                "bezel_mm": m.bezel, "source_box_px": [round(v, 2) for v in mapping.box(p.active)],
                "upscale": round(up, 3), "notes": m.notes,
            })
        if preview:
            render_preview(mapping.image, mapping, layout).save(out / "preview.jpg", quality=88)
        meta = {
            "key": key, "panescape": __version__, "source": str(job.source),
            "configuration": str(cfg.path), "crop": crop.as_dict(),
            "canvas_mm": layout.canvas.as_tuple(), "px_per_mm": round(mapping.scale, 4),
            "crop_box_px": [round(v, 2) for v in mapping.crop_box],
            "monitors": monitors_meta, "warnings": warnings,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        meta_path.write_text(json.dumps(meta, indent=2))
        rendered.warnings = warnings
        return rendered

    def render_many(self, cfg: Configuration, jobs: list[ImageJob], force: bool = False,
                    then=None) -> list:
        """Render jobs in parallel (Pillow releases the GIL while resizing/encoding).

        ``then(job, rendered)`` runs in the same worker (e.g. building the
        desktop's combined image while the decoded source is still in memory).
        Returns [(rendered, then-result)] in the order of ``jobs``.
        """
        from concurrent.futures import ThreadPoolExecutor

        def work(job):
            r = self.render(cfg, job, force=force)
            res = then(job, r) if then else None
            r.mapping = None  # free the decoded source
            return r, res

        workers = max(1, min(len(jobs), MAX_WORKERS, os.cpu_count() or 1))
        if workers == 1:
            return [work(j) for j in jobs]
        with ThreadPoolExecutor(max_workers=workers) as ex:
            return list(ex.map(work, jobs))

    # ------------------------------------------------------------------ apply
    def composite(self, backend, cfg: Configuration, job: ImageJob, os_layout, pairing,
                  rendered: Rendered) -> Path:
        """Desktop-specific combined image (cached per desktop layout + render key)."""
        prefix = backend.__name__.rsplit(".", 1)[-1]
        path = rendered.out_dir / f"{prefix}-{os_layout.fingerprint()}-{rendered.key}.png"
        if path.exists():
            return path
        for old in rendered.out_dir.glob(f"{prefix}-*.png"):
            old.unlink()
        mapping = rendered.mapping
        crop = rendered.crop or self.crop_for(cfg, job)
        layout = rendered.layout or compute_layout(cfg)
        by_os = {pairing[p.monitor.name].connector: p for p in layout if p.monitor.name in pairing}

        def render_for(om, size):
            nonlocal mapping
            placed = by_os.get(om.connector)
            if placed is None:
                return None
            slice_path = rendered.slices.get(placed.monitor.name)
            if tuple(size) == tuple(placed.monitor.pixels) and slice_path and slice_path.exists():
                with Image.open(slice_path) as im:  # already rendered at exactly this size
                    return im.convert("RGB")
            if mapping is None:
                mapping = Mapping(load_image(job.source), layout.canvas, crop)
            tile, _ = mapping.render(placed.active, size)
            return tile

        img = backend.compose(os_layout, render_for, crop.background)
        tmp = path.with_suffix(".tmp")
        img.save(tmp, "PNG", compress_level=1)
        tmp.replace(path)
        return path

    # ------------------------------------------------------------------ state
    def state_path(self) -> Path:
        return self.backgrounds / STATE_FILE

    def load_state(self) -> dict:
        try:
            return json.loads(self.state_path().read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def save_state(self, state: dict) -> None:
        self.backgrounds.mkdir(parents=True, exist_ok=True)
        self.state_path().write_text(json.dumps(state, indent=2))

    # ------------------------------------------------------------------ rules
    # A rule is what the user asked for, saved so it can be re-evaluated later:
    #   {"source": "setup", ...overrides}   follow setup.yml (re-read every time)
    #   {"source": "cli", "images": [...], "filter": ..., "mode": ..., ...}
    # Keys interval/transition/shuffle/filter/crop/configuration on a setup rule
    # are command-line overrides that win over setup.yml.

    def evaluate_rule(self, rule: dict, strict: bool = True) -> dict:
        if rule.get("source") == "setup":
            setup = self.load_setup()
            if not setup:
                raise ProjectError(f"{self.setup_file.name} not found; run 'panescape init'")
            spec = setup.get("images", "all")
            pick = lambda k, d=None: rule[k] if rule.get(k) is not None else setup.get(k, d)  # noqa: E731
            mode = str(pick("mode", "auto")).lower()
        else:
            spec = rule.get("images") or "all"
            pick = lambda k, d=None: rule.get(k) if rule.get(k) is not None else d  # noqa: E731
            mode = str(rule.get("mode") or "auto").lower()
        if mode not in ("single", "slideshow", "auto"):
            raise ProjectError("mode must be 'single' or 'slideshow'")
        cfg = self.load_config(pick("configuration"))
        jobs = self.filter_jobs(self.resolve_images(spec, strict=strict), pick("filter"), strict=strict)
        for j in jobs:
            j.crop.update(rule.get("crop") or {})
        return {"cfg": cfg, "jobs": jobs, "mode": mode,
                "interval": pick("interval", "10m"), "transition": pick("transition", "2s"),
                "shuffle": bool(pick("shuffle", False))}

    def run(self, backend, rule: dict, pinned: list | None = None, strict: bool = True,
            continuity: bool = False, force: bool = False, dry_run: bool = False) -> Path | None:
        """Evaluate ``rule`` (+ pinned images) and put the result on the desktop."""
        ev = self.evaluate_rule(rule, strict=strict)
        pinned = list(pinned or [])
        jobs = ev["jobs"]
        if ev["mode"] == "single":
            jobs = jobs[:1]  # pinned images (panescape add) still join, making a slideshow
        seen = {j.source for j in jobs}
        for pj in self.resolve_images(pinned, strict=False) if pinned else []:
            if pj.source not in seen:
                jobs.append(pj)
                seen.add(pj.source)
        slideshow = ev["mode"] == "slideshow" or len(jobs) > 1
        if not jobs:
            raise ProjectError("the selection matches no images; wallpaper left unchanged")
        return self._apply(backend, ev["cfg"], jobs, slideshow, ev["interval"], ev["transition"],
                           ev["shuffle"], force, dry_run, rule, pinned, continuity)

    def sync(self, backend, add: list | None = None, dry_run: bool = False, force: bool = False,
             **overrides) -> Path | None:
        """Re-evaluate the saved rule (tags/pictures/setup.yml may have changed)."""
        state = self.load_state()
        if not state:
            raise ProjectError("nothing applied by panescape yet; use 'panescape apply' first")
        if "rule" not in state:
            raise ProjectError("the current wallpaper was applied by an older panescape; "
                               "run 'panescape apply' once, then sync/add/tag will keep it updated")
        rule = state["rule"]
        rule = dict(rule)
        rule.update({k: v for k, v in overrides.items() if v is not None})
        pinned = list(state.get("pinned", []))
        if add:
            for job in self.resolve_images(add):
                # pin even if the rule already includes it, so later tag changes can't drop it
                if str(job.source) not in {p["file"] for p in pinned}:
                    pinned.append({"file": str(job.source), **job.crop})
        return self.run(backend, rule, pinned, strict=False, continuity=True, force=force, dry_run=dry_run)

    # ------------------------------------------------------------------ apply
    def _apply(self, backend, cfg: Configuration, jobs: list[ImageJob], slideshow: bool,
               interval, transition, shuffle: bool, force: bool, dry_run: bool,
               rule: dict, pinned: list, continuity: bool) -> Path | None:
        old = self.load_state()
        os_layout = backend.get_layout()
        centres = {p.monitor.name: (p.active.x + p.active.w / 2, p.active.y + p.active.h / 2)
                   for p in compute_layout(cfg)}
        pairing = backend.match_monitors(cfg.monitors, os_layout, centres)
        for name, om in pairing.items():
            _log(f"  {name:<10} -> {om.connector} ({om.display_name}, serial {om.serial})")
        per_output = getattr(backend, "KIND", "spanned") == "per_output"
        for om in os_layout.monitors:
            if om not in pairing.values():
                _log(f"  (monitor {om.connector} is not in the configuration; "
                     f"{'left as it is' if per_output else 'it will be black'})")

        def build(job, r):
            return str(self.composite(backend, cfg, job, os_layout, pairing, r))

        # comp[source]: GNOME: path of the combined image; per-output: {output: slice path}
        comp: dict = {}
        renders = self.render_many(cfg, jobs, force=force, then=None if per_output else build)
        for job, (r, path) in zip(jobs, renders, strict=True):
            rel = r.out_dir.relative_to(self.root) if r.out_dir.is_relative_to(self.root) else r.out_dir
            _log(f"{'cached ' if r.skipped else 'rendered'} {job.source.name} -> {rel}")
            for w in r.warnings:
                _log(f"  warning: {w}")
            if per_output:
                path = {pairing[n].connector: str(p) for n, p in r.slices.items() if n in pairing}
            comp[str(job.source)] = path

        order = [str(j.source) for j in jobs]
        if slideshow and shuffle:
            prev = [o for o in old.get("order", []) if o in comp] if continuity else []
            new = [o for o in order if o not in prev]
            random.shuffle(new)
            order = prev
            for o in new:  # new images go in at random places, old ones keep their order
                order.insert(random.randint(0, len(order)), o)

        secs, trans = parse_duration(interval), parse_duration(transition)
        mode = "slideshow" if slideshow else "single"
        if continuity and old:
            before, after = set(old.get("order", [])), set(order)
            for o in sorted(after - before):
                _log(f"  + {Path(o).name}")
            for o in sorted(before - after):
                _log(f"  - {Path(o).name}")
            unchanged = (old.get("mode") == mode and old.get("order") == order
                         and old.get("composites") == [comp[o] for o in order]
                         and old.get("interval_s") == secs and old.get("transition_s") == trans
                         and Path(old.get("target", "")).exists()
                         and _is_current(backend, old.get("target", "")))
            if unchanged and not force:
                _log("wallpaper already up to date")
                self.save_state({**old, "rule": rule, "pinned": pinned})
                return None

        start = time.time()
        if slideshow and continuity and old.get("mode") == "slideshow":
            start = _continue_start(old, order, secs, start)
        state = {
            "mode": mode, "configuration": str(cfg.path), "kind": "per_output" if per_output else "spanned",
            "rule": rule, "pinned": pinned,
            "order": order, "composites": [comp[o] for o in order],
            "interval": interval, "transition": transition, "interval_s": secs, "transition_s": trans,
            "shuffle": shuffle, "start": start,
        }
        if per_output:
            return self._apply_per_output(backend, state, dry_run)
        if slideshow:
            sdir = self.backgrounds / cfg.name / "slideshows"
            # unique name every time: GNOME only reloads when the URI changes
            target = sdir / f"slideshow-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 10**6:06d}.xml"
            backend.write_slideshow(target, [Path(comp[o]) for o in order], secs, trans, start=start)
        else:
            target = Path(comp[order[0]])
        if dry_run:
            _log(f"dry run: would set wallpaper to {target}")
            if slideshow:
                target.unlink()
            return target
        backend.set_wallpaper(target)
        (self.backgrounds / RESTORE_FILE).unlink(missing_ok=True)  # calibration superseded
        if slideshow:  # old slideshow files are no longer referenced
            for f in (self.backgrounds / cfg.name / "slideshows").glob("slideshow-*.xml"):
                if f != target:
                    f.unlink()
        self.save_state({**state, "target": str(target), "applied": time.strftime("%Y-%m-%d %H:%M:%S")})
        _log(f"wallpaper set ({f'slideshow of {len(order)}' if slideshow else 'single image'}): {target}")
        return target

    def _apply_per_output(self, backend, state: dict, dry_run: bool) -> Path | None:
        """sway / Hyprland: show the current frame now; a slideshow runner does the rest."""
        idx, _ = frame_index(state)
        frame = state["composites"][idx]
        target = Path(next(iter(frame.values())))
        if dry_run:
            _log(f"dry run: would show {Path(state['order'][idx]).name} on {', '.join(frame)}")
            return target
        backend.set_outputs(frame, transition=state["transition_s"], setter=self.load_setup().get("setter"))
        (self.backgrounds / RESTORE_FILE).unlink(missing_ok=True)  # calibration superseded
        self.save_state({**state, "target": str(target), "shown": idx,
                         "applied": time.strftime("%Y-%m-%d %H:%M:%S")})
        if state["mode"] == "slideshow":
            _log(f"wallpaper set (slideshow of {len(state['order'])}, now {Path(state['order'][idx]).name})")
            started = self.ensure_slideshow_runner()
            if started:
                _log(f"  started the slideshow runner (pid {started}). To start it automatically "
                     f"when you log in:\n{runner_command(self.root)}")
        else:
            _log(f"wallpaper set (single image): {Path(state['order'][0]).name}")
        return target

    # ------------------------------------------------------------------ slideshow runner
    @property
    def runner_pidfile(self) -> Path:
        return self.backgrounds / ".slideshow.pid"

    def runner_pid(self) -> int | None:
        try:
            pid = int(self.runner_pidfile.read_text())
            os.kill(pid, 0)
            return pid
        except (OSError, ValueError):
            return None

    def ensure_slideshow_runner(self) -> int | None:
        """Start ``panescape slideshow`` in the background unless one is running."""
        if self.runner_pid():
            return None
        import subprocess
        import sys
        self.backgrounds.mkdir(parents=True, exist_ok=True)
        log = open(self.backgrounds / "slideshow.log", "a")  # noqa: SIM115 - handed to the child
        proc = subprocess.Popen([sys.executable, "-m", "panescape", "--root", str(self.root), "slideshow"],
                                stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
        return proc.pid

    def run_slideshow(self, backend, poll: float = 5.0, once: bool = False) -> None:
        """Keep per-output wallpapers in step with the saved slideshow.

        Re-reads the state on every wake, so apply / add / sync / tag take
        effect; pauses while a calibration pattern is up; exits when the
        current wallpaper is no longer a slideshow.
        """
        import signal
        if not once:
            self.backgrounds.mkdir(parents=True, exist_ok=True)
            self.runner_pidfile.write_text(str(os.getpid()))
            signal.signal(signal.SIGTERM, _raise_interrupt)
        last = None
        try:
            while True:
                state = self.load_state()
                if state.get("kind") != "per_output" or state.get("mode") != "slideshow":
                    _log("no per-output slideshow is applied; slideshow runner exiting")
                    return
                idx, wait = frame_index(state)
                key = (state.get("applied"), idx)
                if key != last and not (self.backgrounds / RESTORE_FILE).exists():
                    frame = state["composites"][idx]
                    # a fresh apply already showed this frame; otherwise it's our turn
                    fresh_apply = last is None or last[0] != state.get("applied")
                    if not (fresh_apply and state.get("shown") == idx):
                        try:
                            backend.set_outputs(frame, transition=state.get("transition_s", 0),
                                                setter=self.load_setup().get("setter"))
                            _log(f"{time.strftime('%H:%M:%S')} showing {Path(state['order'][idx]).name}")
                        except Exception as e:  # noqa: BLE001 - keep running, try again next tick
                            _log(f"{time.strftime('%H:%M:%S')} could not set wallpaper: {e}")
                    last = key
                if once:
                    return
                time.sleep(max(0.2, min(wait, poll)))
        except KeyboardInterrupt:
            pass
        finally:
            if not once:
                try:
                    if int(self.runner_pidfile.read_text()) == os.getpid():
                        self.runner_pidfile.unlink()
                except (OSError, ValueError):
                    pass

    def show_current(self, backend) -> bool:
        """Put the applied panescape wallpaper back (e.g. after calibrating)."""
        state = self.load_state()
        if not state:
            return False
        if state.get("kind") == "per_output":
            if getattr(backend, "KIND", "") != "per_output":
                return False
            idx, _ = frame_index(state)
            backend.set_outputs(state["composites"][idx], transition=0,
                                setter=self.load_setup().get("setter"))
            return True
        if state.get("target") and Path(state["target"]).exists() and hasattr(backend, "set_wallpaper"):
            backend.set_wallpaper(Path(state["target"]))
            return True
        return False


def _raise_interrupt(*_args) -> None:
    raise KeyboardInterrupt


def runner_argv(root: Path) -> list[str]:
    import sys
    return [sys.executable, "-m", "panescape", "--root", str(root), "slideshow"]


def runner_command(root: Path) -> str:
    import shlex
    cmd = shlex.join(runner_argv(root))
    return (f"    sway:     exec {cmd}\n"
            f"    Hyprland: exec-once = {cmd}\n"
            f"    KDE and other desktops: panescape --root {shlex.quote(str(root))} slideshow --autostart")


def autostart_file() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "autostart" / "panescape-slideshow.desktop"


def install_autostart(root: Path) -> Path:
    """XDG autostart entry (KDE, GNOME, Xfce, ...; not sway/Hyprland, which use exec lines)."""
    import shlex
    path = autostart_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[Desktop Entry]\nType=Application\nName=panescape slideshow\n"
                    "Comment=Keeps panescape's multi-monitor slideshow in step\n"
                    f"Exec={shlex.join(runner_argv(root))}\nX-GNOME-Autostart-enabled=true\nNoDisplay=true\n")
    return path


def frame_index(state: dict, now: float | None = None) -> tuple[int, float]:
    """(image index on screen now, seconds until the next change) for a slideshow state."""
    n = len(state.get("order") or [])
    if state.get("mode") != "slideshow" or n <= 1:
        return 0, 3600.0
    secs = float(state.get("interval_s") or 600)
    now = time.time() if now is None else now
    elapsed = (now - float(state.get("start", now))) % (n * secs)
    idx = int(elapsed // secs)
    return min(idx, n - 1), secs - (elapsed - idx * secs)


def _continue_start(old: dict, order: list[str], secs: float, now: float) -> float:
    """Start time for the new slideshow so the image on screen now stays on screen."""
    old_order, old_start, old_secs = old.get("order") or [], old.get("start"), old.get("interval_s")
    if not old_order or old_start is None or not old_secs:
        return now
    elapsed = (now - old_start) % (len(old_order) * old_secs)
    idx = int(elapsed // old_secs)
    within = min(elapsed - idx * old_secs, max(secs - 1.0, 0.0))
    current = old_order[min(idx, len(old_order) - 1)]
    j = order.index(current) if current in order else min(idx, len(order) - 1)
    return now - (j * secs + within)


def _is_current(backend, target: str) -> bool:
    """Is ``target`` what the desktop shows now (e.g. not replaced by calibrate)?"""
    get = getattr(backend, "current_uri", None)
    if get is None:
        return True
    try:
        return get() == Path(target).resolve().as_uri()
    except Exception:  # noqa: BLE001 - be permissive, reapplying is harmless
        return False
