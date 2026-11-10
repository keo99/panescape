"""Unsplash: key storage, where the key may go, search cache, downloads, suggestions.

No network: a fake opener answers every request and records what was sent.
"""

import io
import json
import stat
import urllib.error
from email.message import Message

import pytest
from helpers import make_project
from PIL import Image

from panescape import unsplash

KEY = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-abcde"


def photo(pid, name="Ann Lee", tags=(), w=6000, h=3000):
    return {"id": pid, "width": w, "height": h, "alt_description": "a lake",
            "user": {"name": name, "username": name.lower().replace(" ", ""),
                     "links": {"html": f"https://unsplash.com/@{name.lower().replace(' ', '')}"}},
            "urls": {"raw": f"https://images.unsplash.com/photo-{pid}?ixid=abc&ixlib=rb-4.0.3",
                     "small": f"https://images.unsplash.com/photo-{pid}?ixid=abc&w=400"},
            "links": {"html": f"https://unsplash.com/photos/{pid}",
                      "download_location": f"https://api.unsplash.com/photos/{pid}/download?ixid=abc"},
            "tags": [{"title": t} for t in tags]}


class Resp:
    def __init__(self, body: bytes, headers=None):
        self._b = io.BytesIO(body)
        self.headers = Message()
        for k, v in (headers or {}).items():
            self.headers[k] = v

    def read(self, n=-1):
        return self._b.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeNet:
    def __init__(self):
        self.sent = []          # (url, headers)
        self.routes = {}        # url prefix -> body (dict/bytes) or HTTP status int

    def open(self, req, timeout=None):
        url = req.full_url
        self.sent.append((url, dict(req.header_items())))
        for prefix, body in sorted(self.routes.items(), key=lambda kv: -len(kv[0])):
            if url.startswith(prefix):
                if isinstance(body, int):
                    h = Message()
                    h["X-Ratelimit-Remaining"] = "0" if body == 403 else "40"
                    raise urllib.error.HTTPError(url, body, "nope", h,
                                                 io.BytesIO(json.dumps({"errors": [f"bad {KEY}"]}).encode()))
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                return Resp(data, {"X-Ratelimit-Remaining": "41", "X-Ratelimit-Limit": "50"})
        raise AssertionError(f"unexpected request {url}")

    def with_key(self):
        return [u for u, h in self.sent if any(KEY in v for v in h.values()) or KEY in u]


def jpeg_bytes(w=64, h=32):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "teal").save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv(unsplash.KEY_ENV, raising=False)
    monkeypatch.setenv("PANESCAPE_NO_KEYRING", "1")
    return tmp_path


# --------------------------------------------------------------------------- the key

def test_key_file_is_private_and_removable(env):
    assert unsplash.load_key() == (None, None)
    assert unsplash.save_key(f"  {KEY}\n") == "file"
    f = unsplash.key_file()
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert stat.S_IMODE(f.parent.stat().st_mode) == 0o700
    assert unsplash.load_key() == (KEY, "file")
    f.chmod(0o644)  # loosened by someone: tightened on the next read
    unsplash.load_key()
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    unsplash.delete_key()
    assert not f.exists() and unsplash.load_key() == (None, None)


def test_keyring_preferred_and_file_copy_removed(env, monkeypatch):
    store = {}

    class FakeKeyring:
        def get_password(self, s, u):
            return store.get((s, u))

        def set_password(self, s, u, k):
            store[(s, u)] = k

        def delete_password(self, s, u):
            store.pop((s, u))
    unsplash.save_key(KEY)  # a file copy from before
    monkeypatch.setattr(unsplash, "_keyring", lambda: FakeKeyring())
    assert unsplash.save_key(KEY) == "keyring"
    assert not unsplash.key_file().exists()
    assert unsplash.load_key() == (KEY, "keyring")
    monkeypatch.setenv(unsplash.KEY_ENV, "fromtheenvironment_0123456789")
    assert unsplash.load_key()[1] == "environment"
    monkeypatch.delenv(unsplash.KEY_ENV)
    unsplash.delete_key()
    assert store == {}


def test_bad_keys_are_refused(env):
    for bad in ("", "short", "has spaces in it but is long enough", "x" * 200):
        with pytest.raises(unsplash.UnsplashError):
            unsplash.save_key(bad)
    assert not unsplash.key_file().exists()


def test_the_key_goes_only_to_the_api_and_never_in_a_url(env):
    net = FakeNet()
    net.routes["https://api.unsplash.com/search/photos"] = {"total": 1, "results": [photo("aaaaaaaaaaa")]}
    c = unsplash.Client(KEY, opener=net)
    assert KEY not in repr(c)
    c.search("lake")
    url, headers = net.sent[0]
    assert KEY not in url and headers["Authorization"] == f"Client-ID {KEY}"
    assert c.remaining == 41
    for bad in ("http://api.unsplash.com/photos", "https://evil.example/photos",
                "https://api.unsplash.com.evil.example/x"):
        with pytest.raises(unsplash.UnsplashError):
            c._get(bad)
    # a download_location pointing elsewhere must not get the key
    with pytest.raises(unsplash.UnsplashError):
        c.track_download({"id": "x", "links": {"download_location": "https://evil.example/d"}})
    assert len(net.sent) == 1
    # images are fetched without it
    net.routes["https://images.unsplash.com/"] = jpeg_bytes()
    unsplash.fetch_image(photo("aaaaaaaaaaa")["urls"]["small"], opener=net)
    assert net.sent[-1][1].get("Authorization") is None
    with pytest.raises(unsplash.UnsplashError):
        unsplash.fetch_image("https://evil.example/x.jpg", opener=net)
    # nothing with the key in it is cached
    for f in (env / "cache").rglob("*"):
        if f.is_file():
            assert KEY not in f.read_text()


def test_redirects_off_the_api_are_refused():
    h = unsplash._StayOnHost({"api.unsplash.com"})
    with pytest.raises(unsplash.UnsplashError):
        h.redirect_request(None, None, 302, "Found", {}, "https://elsewhere.example/")
    with pytest.raises(unsplash.UnsplashError):
        h.redirect_request(None, None, 302, "Found", {}, "http://api.unsplash.com/x")


def test_errors_are_scrubbed_and_401_is_recognised(env):
    net = FakeNet()
    net.routes["https://api.unsplash.com/photos"] = 401
    c = unsplash.Client(KEY, opener=net)
    with pytest.raises(unsplash.KeyRejected) as e:
        c.check()
    assert KEY not in str(e.value)
    net.routes["https://api.unsplash.com/photos"] = 500
    with pytest.raises(unsplash.UnsplashError) as e:
        c.check()
    assert KEY not in str(e.value) and "•••" in str(e.value)
    net.routes["https://api.unsplash.com/photos"] = 403
    with pytest.raises(unsplash.UnsplashError, match="hourly limit"):
        c.check()


def test_searches_are_cached(env):
    net = FakeNet()
    net.routes["https://api.unsplash.com/search/photos"] = {"total": 0, "results": []}
    c = unsplash.Client(KEY, opener=net)
    c.search("lake")
    c.search("lake")
    c.search("lake", page=2)
    assert len(net.sent) == 2


# --------------------------------------------------------------------------- downloads

def test_download_tracks_credits_and_tags(env):
    proj = make_project(env, names=("a",))
    net = FakeNet()
    p = photo("Zx3_abc-DEF", name="Ann Lee", tags=("Lake", "mountain", "HD Wallpapers", "stockholm", "free"),
              w=9000)
    net.routes["https://api.unsplash.com/photos/Zx3_abc-DEF"] = p
    net.routes["https://api.unsplash.com/photos/Zx3_abc-DEF/download"] = {"url": "..."}
    net.routes["https://images.unsplash.com/"] = jpeg_bytes()
    c = unsplash.Client(KEY, opener=net)
    path, full = c.download({"id": "Zx3_abc-DEF"}, proj.pictures)
    assert path.name == "ann-lee-Zx3_abc-DEF-unsplash.jpg"
    urls = [u for u, _ in net.sent]
    assert any(u.startswith("https://api.unsplash.com/photos/Zx3_abc-DEF/download") for u in urls)
    img_url = next(u for u in urls if u.startswith("https://images"))
    assert "fm=jpg" in img_url and "w=8000" in img_url and "ixid=abc" in img_url
    assert net.with_key() == [u for u in urls if u.startswith("https://api.unsplash.com/")]
    tags = unsplash.save_download(proj, path, full)
    assert tags == ["lake", "mountain", "stockholm"]
    cr = unsplash.credit_for(proj, path.name)
    assert cr["photographer"] == "Ann Lee" and "utm_source=panescape" in cr["profile"]
    assert "Unsplash License" in unsplash.credits_file(proj).read_text()
    assert "Zx3_abc-DEF" in unsplash.library_ids(proj)
    # a second download of the same photo doesn't overwrite
    path2, _ = c.download({"id": "Zx3_abc-DEF"}, proj.pictures)
    assert path2.name == "ann-lee-Zx3_abc-DEF-unsplash-2.jpg"


def test_a_broken_download_leaves_nothing(env):
    proj = make_project(env, names=("a",))
    net = FakeNet()
    net.routes["https://api.unsplash.com/photos/bbbbbbbbbbb"] = photo("bbbbbbbbbbb")
    net.routes["https://images.unsplash.com/"] = b"<html>not an image</html>"
    c = unsplash.Client(KEY, opener=net)
    net.routes["https://api.unsplash.com/photos/bbbbbbbbbbb/download"] = {}
    with pytest.raises(unsplash.UnsplashError):
        c.download({"id": "bbbbbbbbbbb"}, proj.pictures)
    assert sorted(p.name for p in proj.pictures.iterdir()) == ["a.png"]


def test_suggestions_follow_your_tags_and_skip_what_you_have(env):
    proj = make_project(env, names=("a", "b", "c"))
    (proj.pictures / "tags.yml").write_text("a.png: [lake, mountain]\nb.png: [lake]\nc.png: [mountain, snow]\n")
    (proj.pictures / "jo-HAVEitALRDY-unsplash.jpg").write_bytes(jpeg_bytes())
    net = FakeNet()
    lake = [photo("lake1111111"), photo("both2222222"), photo("HAVEitALRDY")]
    mountain = [photo("both2222222"), photo("mount333333")]
    net.routes["https://api.unsplash.com/search/photos?query=lake"] = {"results": lake}
    net.routes["https://api.unsplash.com/search/photos?query=mountain"] = {"results": mountain}
    net.routes["https://api.unsplash.com/search/photos?query=snow"] = {"results": []}
    used, photos = unsplash.suggest(unsplash.Client(KEY, opener=net), proj)
    assert set(used[:2]) == {"lake", "mountain"}
    assert [p["id"] for p in photos] == ["both2222222", "lake1111111", "mount333333"]


def test_enlargement():
    from helpers import DESK

    from panescape.config import configuration_from_dict
    from panescape.layout import compute_layout
    lay = compute_layout(configuration_from_dict(DESK))
    assert unsplash.enlargement(12000, 4000, lay) < 1 < unsplash.enlargement(3000, 1000, lay)


def test_download_with_one_request(env):
    proj = make_project(env, names=("a",))
    net = FakeNet()
    net.routes["https://api.unsplash.com/photos/ccccccccccc/download"] = {}
    net.routes["https://images.unsplash.com/"] = jpeg_bytes()
    found = dict(photo("ccccccccccc"), tags=None, alt_description="Snowy peaks above a quiet lake at dawn",
                 _found_by=["alpine lake"])
    path, ph = unsplash.Client(KEY, opener=net).download(found, proj.pictures, fetch_tags=False)
    api = [u for u, _ in net.sent if u.startswith("https://api.unsplash.com/")]
    assert len(api) == 1 and "/download" in api[0]
    assert unsplash.save_download(proj, path, ph) == ["alpine", "lake", "snowy", "peaks", "quiet", "dawn"]
