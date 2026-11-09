"""KDE Plasma backend with kscreen-doctor and plasmashell mocked."""

import json

import pytest

from panescape.backends import kde
from panescape.backends.base import BackendError

# trimmed `kscreen-doctor -j` for the README desk (Plasma 6)
KSCREEN = {"outputs": [
    {"id": 1, "name": "DP-6", "enabled": True, "connected": True, "pos": {"x": 0, "y": 62},
     "scale": 1.5, "rotation": 2, "currentModeId": "1", "priority": 1, "type": "DisplayPort",
     "modes": [{"id": "1", "size": {"width": 3840, "height": 2160}}]},
    {"id": 2, "name": "DP-8", "enabled": True, "connected": True, "pos": {"x": 1440, "y": 765},
     "scale": 1.0, "rotation": 1, "currentModeId": "7", "priority": 2,
     "modes": [{"id": "7", "size": {"width": 1920, "height": 1080}}]},
    {"id": 3, "name": "DP-7", "enabled": True, "connected": True, "pos": {"x": 3360, "y": 0},
     "scale": 1.5, "rotation": 8, "currentModeId": "1", "priority": 3,
     "modes": [{"id": "1", "size": {"width": 3840, "height": 2160}}]},
    {"id": 4, "name": "HDMI-A-1", "enabled": False, "connected": False, "pos": {"x": 0, "y": 0}},
]}
# what SCREENS_JS prints: Plasma numbers screens in its own order
SCREENS = [{"screen": 0, "x": 1440, "y": 765, "width": 1920, "height": 1080},
           {"screen": 1, "x": 0, "y": 62, "width": 1440, "height": 2560},
           {"screen": 2, "x": 3360, "y": 0, "width": 1440, "height": 2560}]


def test_parse_kscreen():
    lay = kde.parse_kscreen(KSCREEN)
    assert [m.connector for m in lay.monitors] == ["DP-6", "DP-8", "DP-7"]
    left = lay.monitors[0]
    assert (left.width, left.height, left.rotation) == (1440, 2560, 90)
    assert kde.native_size(left) == (2160, 3840)
    assert lay.monitors[2].rotation == 270 and lay.monitors[0].primary


def test_screen_matching():
    lay = kde.parse_kscreen(KSCREEN)
    assert [kde.screen_for(m, SCREENS) for m in lay.monitors] == [1, 0, 2]
    far = kde.parse_kscreen({"outputs": [{**KSCREEN["outputs"][1], "pos": {"x": 9000, "y": 0}}]}).monitors[0]
    assert kde.screen_for(far, SCREENS) is None


def test_set_outputs_script(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(kde, "get_layout", lambda: kde.parse_kscreen(KSCREEN))
    scripts = []

    def fake_eval(script):
        scripts.append(script)
        return json.dumps(SCREENS) if "screenGeometry" in script else ""
    monkeypatch.setattr(kde, "evaluate", fake_eval)
    imgs = {}
    for name in ("DP-6", "DP-8"):
        imgs[name] = tmp_path / f"{name}.png"
        imgs[name].write_bytes(b"x")
    kde.set_outputs(imgs)
    urls = json.loads(scripts[-1].split("var urls = ")[1].split(";")[0])
    assert set(urls) == {"1", "0"}                        # DP-6 is Plasma screen 1, DP-8 screen 0
    assert urls["1"].startswith("file://") and "DP-6" in urls["1"]
    assert 'd.wallpaperPlugin = "org.kde.image"' in scripts[-1]

    # same file again -> same link; rewritten file -> new link, so Plasma reloads it
    first = urls["1"]
    kde.set_outputs(imgs)
    assert json.loads(scripts[-1].split("var urls = ")[1].split(";")[0])["1"] == first
    imgs["DP-6"].write_bytes(b"changed!")
    kde.set_outputs(imgs)
    assert json.loads(scripts[-1].split("var urls = ")[1].split(";")[0])["1"] != first


def test_unmatched_screen_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(kde, "get_layout", lambda: kde.parse_kscreen(KSCREEN))
    monkeypatch.setattr(kde, "evaluate", lambda s: "[]")
    (tmp_path / "a.png").write_bytes(b"x")
    with pytest.raises(BackendError):
        kde.set_outputs({"DP-6": tmp_path / "a.png"})


def test_autostart_entry(monkeypatch, tmp_path):
    from panescape.project import autostart_file, install_autostart
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = install_autostart(tmp_path / "my desk")
    assert path == autostart_file() and path.parent.name == "autostart"
    text = path.read_text()
    assert "Exec=" in text and "slideshow" in text and "'" in text   # path with a space is quoted


def test_plasma_sizes_override_kscreen():
    # nested KWin: kscreen says 1440x810 at scale 1, Plasma's logical screens are 960x540
    lay = kde.parse_kscreen({"outputs": [
        {"name": f"WL-{i}", "enabled": True, "pos": {"x": 960 * i, "y": 0}, "scale": 1, "rotation": 1,
         "currentModeId": "1", "modes": [{"id": "1", "size": {"width": 1440, "height": 810}}]} for i in range(3)]})
    screens = [{"screen": i, "x": 960 * i, "y": 0, "width": 960, "height": 540} for i in range(3)]
    assert [kde.screen_for(m, screens) for m in lay.monitors] == [0, 1, 2]
    kde.refine_with_plasma(lay, screens)
    assert [(m.width, m.height) for m in lay.monitors] == [(960, 540)] * 3
