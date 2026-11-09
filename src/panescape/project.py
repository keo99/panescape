"""A panescape project folder: pictures/, configurations/, backgrounds/, setup.yml."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import __version__
from .config import Configuration, load_configuration
from .layout import Layout, compute_layout
from .render import UPSCALE_WARN, CropSpec, Mapping, load_image, render_preview, save_image
from .units import parse_duration

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".avif", ".heic"}
STATE_FILE = ".panescape-state.json"
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

    def _cache_key(self, cfg: Configuration, job: ImageJob, crop: CropSpec) -> str:
        st = job.source.stat()
        data = [__version__, str(job.source), st.st_size, int(st.st_mtime), cfg.fingerprint(), crop.as_dict()]
        return hashlib.sha1(json.dumps(data, default=str).encode()).hexdigest()[:10]

    def mapping(self, cfg: Configuration, job: ImageJob, crop: CropSpec) -> tuple[Layout, Mapping]:
        layout = compute_layout(cfg)
        img = load_image(job.source)
        return layout, Mapping(img, layout.canvas, crop)

    def render(self, cfg: Configuration, job: ImageJob, force: bool = False,
               crop_override: dict | None = None, preview: bool = True) -> Rendered:
        crop = self.crop_for(cfg, job, crop_override)
        key = self._cache_key(cfg, job, crop)
        out = self.out_dir(cfg, job)
        meta_path = out / "meta.json"
        fmt = str(cfg.output.get("format", "png")).lower().lstrip(".")
        ext = "jpg" if fmt == "jpeg" else fmt
        quality = int(cfg.output.get("quality", 95))
        names = {m.name: out / f"{m.index + 1}_{m.name}.{ext}" for m in cfg.monitors}

        if not force and meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                if meta.get("key") == key and all(p.exists() for p in names.values()):
                    return Rendered(job, out, names, key, meta.get("warnings", []), skipped=True)
            except (OSError, json.JSONDecodeError):
                pass

        layout, mapping = self.mapping(cfg, job, crop)
        warnings = []
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob("*"):
            if old.is_file() and old.name != "meta.json":
                old.unlink()
        monitors_meta = []
        for p in layout:
            m = p.monitor
            tile, up = mapping.render(p.active, m.pixels)
            save_image(tile, names[m.name], fmt, quality)
            if up > UPSCALE_WARN:
                warnings.append(f"{m.name}: source is enlarged {up:.2f}x (image has fewer pixels than the screen)")
            monitors_meta.append({
                "name": m.name, "pixels": m.pixels, "rotation": m.rotation,
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
        return Rendered(job, out, names, key, warnings, skipped=False)

    # ------------------------------------------------------------------ apply
    def composite(self, backend, cfg: Configuration, job: ImageJob, os_layout, pairing,
                  rendered: Rendered) -> Path:
        """Desktop-specific combined image (cached per layout + render key)."""
        path = rendered.out_dir / f"{backend.__name__.rsplit('.', 1)[-1]}-{os_layout.fingerprint()}-{rendered.key}.png"
        if path.exists():
            return path
        for old in rendered.out_dir.glob(f"{backend.__name__.rsplit('.', 1)[-1]}-*.png"):
            old.unlink()
        crop = self.crop_for(cfg, job)
        layout, mapping = self.mapping(cfg, job, crop)
        by_os = {pairing[p.monitor.name].connector: p for p in layout if p.monitor.name in pairing}

        def render_for(om, size):
            placed = by_os.get(om.connector)
            if placed is None:
                return None
            tile, _ = mapping.render(placed.active, size)
            return tile

        img = backend.compose(os_layout, render_for, crop.background)
        img.save(path, "PNG", compress_level=3)
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
        for om in os_layout.monitors:
            if om not in pairing.values():
                _log(f"  (GNOME monitor {om.connector} is not in the configuration; it will be black)")

        comp: dict[str, str] = {}
        for job in jobs:
            r = self.render(cfg, job, force=force)
            rel = r.out_dir.relative_to(self.root) if r.out_dir.is_relative_to(self.root) else r.out_dir
            _log(f"{'cached ' if r.skipped else 'rendered'} {job.source.name} -> {rel}")
            for w in r.warnings:
                _log(f"  warning: {w}")
            comp[str(job.source)] = str(self.composite(backend, cfg, job, os_layout, pairing, r))

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
        self.save_state({
            "mode": mode, "configuration": str(cfg.path),
            "rule": rule, "pinned": pinned,
            "order": order, "composites": [comp[o] for o in order],
            "interval": interval, "transition": transition, "interval_s": secs, "transition_s": trans,
            "shuffle": shuffle, "start": start,
            "target": str(target), "applied": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        _log(f"wallpaper set ({f'slideshow of {len(order)}' if slideshow else 'single image'}): {target}")
        return target


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
