"""Unsplash: search, suggestions from your tags, and downloading into pictures/.

The access key
--------------
* It is stored in the system keyring (GNOME Keyring, KWallet, ... via the
  ``keyring`` package) when one is available, otherwise in
  ``~/.config/panescape/unsplash-key`` readable only by you (0600). The
  environment variable ``PANESCAPE_UNSPLASH_KEY`` wins over both.
* It never goes in the project folder, setup.yml, QSettings or the cache.
* It is sent only to https://api.unsplash.com, in the Authorization header
  (never in a URL, so it can't end up in logs or caches). Redirects to any
  other host are refused, so the header can't follow them.
* Error messages are scrubbed of it.

Unsplash's API guidelines, followed here
----------------------------------------
* Pictures are shown from the hotlinked ``photo.urls`` (thumbnails are not
  saved to disk).
* Downloading a photo calls its ``download_location`` so the photographer's
  download count goes up.
* The photographer and Unsplash are credited, with links carrying
  ``utm_source=panescape&utm_medium=referral``; downloaded pictures keep the
  credit in ``pictures/credits.yml``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

API_HOST = "api.unsplash.com"
API = f"https://{API_HOST}"
IMAGE_HOSTS = {"images.unsplash.com", "plus.unsplash.com"}
APP_NAME = "panescape"
UTM = f"utm_source={APP_NAME}&utm_medium=referral"
KEY_ENV = "PANESCAPE_UNSPLASH_KEY"
KEYRING_SERVICE = "panescape"
KEYRING_USER = "unsplash-access-key"
NEW_APP_URL = "https://unsplash.com/oauth/applications"
MAX_DOWNLOAD = 120 * 1024 * 1024  # bytes
MAX_WIDTH = 8000                  # px; larger originals are scaled down by Unsplash's image server
CACHE_TTL = 3600                  # s; search results (the demo key allows 50 requests an hour)
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{20,128}$")
# tags Unsplash adds to nearly everything; they say nothing about the picture
GENERIC_TAGS = {"wallpaper", "wallpapers", "background", "backgrounds", "image", "images", "photo", "photos",
                "picture", "pictures", "free", "hd", "4k", "8k", "desktop", "unsplash", "stock", "photography",
                "high resolution", "beautiful", "aesthetic", "iphone", "mac", "windows"}
GENERIC_WORDS = {"wallpaper", "wallpapers", "background", "backgrounds", "free", "hd", "4k", "images",
                 "pictures", "photos", "stock"}


class UnsplashError(RuntimeError):
    pass


class KeyRejected(UnsplashError):
    """401: the key is wrong or was revoked."""


# --------------------------------------------------------------------------- the key

def _config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "panescape"


def key_file() -> Path:
    return _config_dir() / "unsplash-key"


def _keyring():
    """The keyring module if it has a real (encrypting, persistent) backend, else None."""
    if os.environ.get("PANESCAPE_NO_KEYRING"):
        return None
    try:
        import keyring
        kr = keyring.get_keyring()
    except Exception:  # noqa: BLE001 - no keyring package, no D-Bus, ...
        return None
    def real(b) -> bool:  # encrypting and persistent
        name = f"{type(b).__module__}.{type(b).__name__}".lower()
        return not any(bad in name for bad in ("fail", "null", "plaintext", "chainer"))
    ok = any(real(b) for b in getattr(kr, "backends", [])) if "chainer" in type(kr).__name__.lower() else real(kr)
    return keyring if ok else None


def check_key_format(key: str) -> str:
    key = key.strip()
    if not KEY_RE.match(key):
        raise UnsplashError("that doesn't look like an Unsplash access key (letters, digits, - and _, "
                            "usually 43 characters; use the Access Key, not the Secret key)")
    return key


def load_key() -> tuple[str | None, str | None]:
    """(key, where it came from: 'environment' | 'keyring' | 'file'), or (None, None)."""
    env = os.environ.get(KEY_ENV, "").strip()
    if env:
        return env, "environment"
    kr = _keyring()
    if kr is not None:
        try:
            key = kr.get_password(KEYRING_SERVICE, KEYRING_USER)
            if key:
                return key, "keyring"
        except Exception:  # noqa: BLE001 - locked or unavailable keyring: try the file
            pass
    f = key_file()
    try:
        st = f.stat()
    except OSError:
        return None, None
    if st.st_mode & 0o077:  # someone loosened it; tighten before using
        os.chmod(f, 0o600)
    key = f.read_text().strip()
    return (key, "file") if key else (None, None)


def save_key(key: str) -> str:
    """Store the key; returns where ('keyring' or 'file')."""
    key = check_key_format(key)
    kr = _keyring()
    if kr is not None:
        try:
            kr.set_password(KEYRING_SERVICE, KEYRING_USER, key)
            key_file().unlink(missing_ok=True)  # don't leave an older copy lying around
            return "keyring"
        except Exception:  # noqa: BLE001 - fall back to the private file
            pass
    d = _config_dir()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    tmp = d / f".unsplash-key.{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(key + "\n")
    os.replace(tmp, key_file())
    return "file"


def delete_key() -> None:
    kr = _keyring()
    if kr is not None:
        try:
            kr.delete_password(KEYRING_SERVICE, KEYRING_USER)
        except Exception:  # noqa: BLE001 - not stored there
            pass
    key_file().unlink(missing_ok=True)


# --------------------------------------------------------------------------- HTTP

class _StayOnHost(urllib.request.HTTPRedirectHandler):
    """Follow redirects only within the allowed hosts (urllib would copy the
    Authorization header to wherever a redirect points)."""

    def __init__(self, hosts: set[str]):
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        u = urllib.parse.urlsplit(newurl)
        if u.scheme != "https" or u.hostname not in self.hosts:
            raise UnsplashError(f"refused a redirect to {u.scheme}://{u.hostname}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _https_on(url: str, hosts: set[str]) -> None:
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or u.hostname not in hosts:
        raise UnsplashError(f"not an Unsplash address: {u.scheme}://{u.hostname}")


def _user_agent() -> str:
    from . import __version__
    return f"{APP_NAME}/{__version__} (+https://github.com/keo99/panescape)"


def fetch_image(url: str, max_bytes: int = 20 * 1024 * 1024, opener=None) -> bytes:
    """Bytes of a hotlinked image (no key involved), for showing it."""
    _https_on(url, IMAGE_HOSTS)
    opener = opener or urllib.request.build_opener(_StayOnHost(IMAGE_HOSTS))
    req = urllib.request.Request(url, headers={"User-Agent": _user_agent()})
    with opener.open(req, timeout=30) as r:
        data = r.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise UnsplashError("image too large")
    return data


def sized(url: str, **params) -> str:
    """An Unsplash image URL with imgix parameters set (keeps ixid etc.)."""
    u = urllib.parse.urlsplit(url)
    q = dict(urllib.parse.parse_qsl(u.query))
    q.update({k: str(v) for k, v in params.items()})
    return urllib.parse.urlunsplit(u._replace(query=urllib.parse.urlencode(q)))


def credit_links(photo: dict) -> tuple[str, str]:
    """(photographer profile URL, photo page URL) with the referral parameters."""
    def utm(url: str) -> str:
        return f"{url}{'&' if '?' in url else '?'}{UTM}" if url else ""
    user = photo.get("user") or {}
    profile = (user.get("links") or {}).get("html") or f"https://unsplash.com/@{user.get('username', '')}"
    page = (photo.get("links") or {}).get("html") or f"https://unsplash.com/photos/{photo.get('id', '')}"
    return utm(profile), utm(page)


class Client:
    """Talks to api.unsplash.com with one access key."""

    def __init__(self, key: str, cache_dir: Path | None = None, opener=None, ttl: float = CACHE_TTL):
        self._key = check_key_format(key)
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
        self.cache_dir = cache_dir or base / "panescape" / "unsplash"
        self._opener = opener or urllib.request.build_opener(_StayOnHost({API_HOST}))
        self._img_opener = opener or urllib.request.build_opener(_StayOnHost(IMAGE_HOSTS))
        self.ttl = ttl
        self.remaining: int | None = None
        self.limit: int | None = None

    def __repr__(self) -> str:  # never show the key
        return f"<unsplash.Client remaining={self.remaining}>"

    def _scrub(self, text: str) -> str:
        return str(text).replace(self._key, "•••")

    # ------------------------------------------------------------------ requests
    def _cache_path(self, url: str) -> Path:
        return self.cache_dir / "api" / (hashlib.sha1(url.encode()).hexdigest() + ".json")

    def _get(self, url: str, cache: bool = True, ttl: float | None = None):
        _https_on(url, {API_HOST})  # the key goes nowhere else
        path = self._cache_path(url)
        if cache:
            try:
                if time.time() - path.stat().st_mtime < (self.ttl if ttl is None else ttl):
                    return json.loads(path.read_text())
            except (OSError, ValueError):
                pass
        req = urllib.request.Request(url, headers={
            "Authorization": f"Client-ID {self._key}", "Accept-Version": "v1", "User-Agent": _user_agent()})
        try:
            with self._opener.open(req, timeout=30) as r:
                self._note_limits(r.headers)
                body = r.read(8 * 1024 * 1024)
        except urllib.error.HTTPError as e:
            self._note_limits(e.headers or {})
            detail = ""
            try:
                detail = "; ".join(json.loads(e.read(65536)).get("errors", []))
            except Exception:  # noqa: BLE001
                pass
            if e.code == 401:
                raise KeyRejected("Unsplash didn't accept the access key (401)"
                                  + (f": {self._scrub(detail)}" if detail else "")) from None
            if e.code == 403 and self.remaining == 0:
                raise UnsplashError("Unsplash's hourly limit is used up; try again later") from None
            raise UnsplashError(self._scrub(f"Unsplash: HTTP {e.code} {detail or e.reason}")) from None
        except UnsplashError:
            raise
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise UnsplashError(self._scrub(f"can't reach Unsplash: {getattr(e, 'reason', e)}")) from None
        data = json.loads(body)
        if cache:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            os.replace(tmp, path)
        return data

    def _note_limits(self, headers) -> None:
        for attr, name in (("remaining", "X-Ratelimit-Remaining"), ("limit", "X-Ratelimit-Limit")):
            v = headers.get(name) if headers else None
            if v is not None and str(v).isdigit():
                setattr(self, attr, int(v))

    def _url(self, path: str, **params) -> str:
        params = {k: v for k, v in params.items() if v not in (None, "")}
        return f"{API}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")

    # ------------------------------------------------------------------ API
    def check(self) -> int | None:
        """One cheap request to see that the key works; returns requests left this hour."""
        self._get(self._url("/photos", per_page=1), cache=False)
        return self.remaining

    def search(self, query: str, page: int = 1, orientation: str | None = "landscape",
               order_by: str = "relevant", per_page: int = 30) -> dict:
        query = query.strip()
        if not query:
            raise UnsplashError("type something to search for")
        return self._get(self._url("/search/photos", query=query, page=page, per_page=per_page,
                                   orientation=orientation, order_by=order_by, content_filter="high"))

    def photo(self, photo_id: str) -> dict:
        """The full photo (with tags); cached for a day."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", photo_id):
            raise UnsplashError(f"bad photo id {photo_id!r}")
        return self._get(self._url(f"/photos/{photo_id}"), ttl=86400)

    def track_download(self, photo: dict) -> None:
        """Required by Unsplash whenever a photo is downloaded."""
        loc = (photo.get("links") or {}).get("download_location")
        if not loc:
            loc = self._url(f"/photos/{photo['id']}/download")
        self._get(loc, cache=False)

    def download(self, photo: dict, dest_dir: Path, fetch_tags: bool = True) -> tuple[Path, dict]:
        """Save the photo into dest_dir (not overwriting); returns (path, photo).

        Costs one API request (the download report Unsplash requires), plus one to
        fetch the photo's own tags if ``fetch_tags`` (search results don't have them).
        The image file itself comes from images.unsplash.com and is free."""
        full = self.photo(photo["id"]) if fetch_tags else photo
        raw = (full.get("urls") or {}).get("raw") or (photo.get("urls") or {}).get("raw")
        if not raw:
            raise UnsplashError("this photo has no download address")
        params = {"fm": "jpg", "q": 90}
        if int(full.get("width") or 0) > MAX_WIDTH:
            params["w"] = MAX_WIDTH
        url = sized(raw, **params)
        _https_on(url, IMAGE_HOSTS)
        self.track_download(full)
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = _free_name(dest_dir / file_name(full))
        fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".unsplash-", suffix=".part")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _user_agent()})
            size = 0
            with os.fdopen(fd, "wb") as out, self._img_opener.open(req, timeout=120) as r:
                while chunk := r.read(1 << 20):
                    size += len(chunk)
                    if size > MAX_DOWNLOAD:
                        raise UnsplashError("download too large")
                    out.write(chunk)
            _check_image(Path(tmp))
            os.replace(tmp, target)
        except (urllib.error.URLError, OSError) as e:
            raise UnsplashError(f"download failed: {getattr(e, 'reason', e)}") from None
        finally:
            Path(tmp).unlink(missing_ok=True)
        return target, full


def _check_image(path: Path) -> None:
    from PIL import Image
    try:
        with Image.open(path) as im:
            im.verify()
    except Exception as e:  # noqa: BLE001
        raise UnsplashError(f"the download isn't a readable image ({e})") from None


def _free_name(path: Path) -> Path:
    if not path.exists():
        return path
    i = 2
    while (cand := path.with_name(f"{path.stem}-{i}{path.suffix}")).exists():
        i += 1
    return cand


# --------------------------------------------------------------------------- the library

ID_IN_NAME = re.compile(r"-([A-Za-z0-9_-]{11})-unsplash(?:-\d+)?\.[a-z]+$", re.I)


def file_name(photo: dict) -> str:
    """Unsplash's own naming, e.g. 'martin-martz-3_x1FRGAEwY-unsplash.jpg'."""
    user = re.sub(r"[^a-z0-9]+", "-", str((photo.get("user") or {}).get("name") or
                                          (photo.get("user") or {}).get("username") or "photo").lower()).strip("-")
    pid = re.sub(r"[^A-Za-z0-9_-]", "", str(photo["id"]))
    return f"{user or 'photo'}-{pid}-unsplash.jpg"


def library_ids(project) -> set[str]:
    """Unsplash photo ids already in pictures/ (from file names and credits.yml)."""
    ids = {m.group(1) for p in project.all_pictures() if (m := ID_IN_NAME.search(p.name))}
    ids |= {str(c.get("id")) for name, c in load_credits(project).items()
            if c.get("id") and (project.pictures / name).exists()}
    return ids


def clean_tags(photo: dict, limit: int = 8) -> list[str]:
    out = []
    for t in photo.get("tags") or []:
        title = str(t.get("title") if isinstance(t, dict) else t).strip().lower()
        if (not title or title in GENERIC_TAGS or GENERIC_WORDS & set(title.split())
                or len(title) > 30 or title in out):
            continue
        out.append(title)
        if len(out) >= limit:
            break
    return out


STOPWORDS = set("""a an the and or of in on at to with without for from by over under near into onto
    is are was were be been being this that these those its it his her their our your my some many few
    two three one four five several group view top close up side front back during while very
    above below behind beside around between against across through along amid among beneath next
    photo photography image picture shot taken person people man woman""".split())


def derived_tags(photo: dict, limit: int = 8) -> list[str]:
    """Tags without an extra request: the words you searched for (or the tags a
    suggestion came from) and the photo's description."""
    out: list[str] = []

    def add(word: str) -> None:
        w = word.strip().lower()
        if (len(w) >= 3 and w.isalpha() and w not in STOPWORDS and w not in GENERIC_TAGS
                and w not in GENERIC_WORDS and w not in out and len(out) < limit):
            out.append(w)
    for q in photo.get("_found_by") or []:
        for w in re.split(r"[\s,+]+", str(q)):
            add(w)
    text = " ".join(str(photo.get(k) or "") for k in ("alt_description", "description"))
    for w in re.findall(r"[A-Za-z]+", text):
        add(w)
    return out


def credits_file(project) -> Path:
    return project.pictures / "credits.yml"


def load_credits(project) -> dict[str, dict]:
    from . import yamledit
    try:
        data = yamledit.safe_load(credits_file(project)) or {}
    except (OSError, yamledit.YAMLError):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


CREDITS_HEADER = ("# Where downloaded pictures came from (written by panescape).\n"
                  "# Unsplash photos are free to use under the Unsplash License: https://unsplash.com/license\n")


def save_download(project, path: Path, photo: dict) -> list[str]:
    """Record the credit and tag the new picture; returns the tags given."""
    from ruamel.yaml.comments import CommentedMap

    from . import yamledit
    from .tags import edit_tags
    cf = credits_file(project)
    doc = yamledit.load(cf) if cf.exists() else None
    if not isinstance(doc, dict):
        doc = CommentedMap()
        doc.yaml_set_start_comment(CREDITS_HEADER)
    user = photo.get("user") or {}
    profile, page = credit_links(photo)
    entry = CommentedMap()
    entry["source"] = "unsplash"
    entry["id"] = str(photo["id"])
    entry["photographer"] = str(user.get("name") or user.get("username") or "")
    entry["profile"] = profile
    entry["photo"] = page
    if photo.get("alt_description") or photo.get("description"):
        entry["about"] = str(photo.get("description") or photo.get("alt_description"))[:200]
    doc[path.name] = entry
    yamledit.save(cf, doc)
    tags = clean_tags(photo) or derived_tags(photo)
    if tags:
        edit_tags(project.tags_file, {path.name: (set(tags), set())})
    return tags


def credit_for(project, name: str) -> dict | None:
    return load_credits(project).get(name)


# --------------------------------------------------------------------------- suggestions

def tag_profile(project) -> Counter:
    """How many of your pictures carry each tag (generic ones left out)."""
    from .tags import load_tags, tags_for
    tags = load_tags(project.tags_file)
    c: Counter = Counter()
    for p in project.all_pictures():
        for t in tags_for(p.name, tags):
            if t not in GENERIC_TAGS:
                c[t] += 1
    return c


def suggest(client: Client, project, n_tags: int = 3, extra: list[str] | None = None) -> tuple[list[str], list[dict]]:
    """Photos like the ones you have: one (cached) search per favourite tag, mixed,
    photos matching several of them first, ones you already have left out.

    Returns (the tags used, photos)."""
    profile = tag_profile(project)
    favourites = [t for t, _ in profile.most_common(n_tags)]
    for t in extra or []:
        if t not in favourites:
            favourites.append(t)
    if not favourites:
        raise UnsplashError("tag some pictures first: suggestions are based on your tags")
    have = library_ids(project)
    seen: dict[str, dict] = {}
    score: Counter = Counter()
    rank: dict[str, float] = {}
    for t in favourites:
        for i, ph in enumerate(client.search(t).get("results", [])):
            pid = ph.get("id")
            if not pid or pid in have:
                continue
            seen.setdefault(pid, ph).setdefault("_found_by", []).append(t)
            score[pid] += 1
            rank[pid] = min(rank.get(pid, 1e9), i)
    order = sorted(seen, key=lambda pid: (-score[pid], rank[pid]))
    return favourites, [seen[p] for p in order]


def enlargement(width: int, height: int, layout) -> float:
    """How much the photo would be enlarged on the worst screen if it filled the desk
    (>1 means soft)."""
    c = layout.canvas
    s = min(width / c.w, height / c.h)  # px per mm, cover fit
    return max((p.monitor.pixels[0] / p.active.w) / s for p in layout) if s > 0 else float("inf")
