"""sway / Hyprland backend, with the compositor and setter commands mocked."""

import types
from pathlib import Path

import pytest
from helpers import make_project

from panescape.backends import wlroots
from panescape.backends.base import BackendError
from panescape.project import frame_index

# trimmed `swaymsg -t get_outputs -r` for the README desk
SWAY = [
    {"name": "DP-6", "make": "Samsung Electric Company", "model": "U28E590", "serial": "S1", "active": True,
     "rect": {"x": 0, "y": 62, "width": 1440, "height": 2560}, "scale": 1.5, "transform": "90",
     "current_mode": {"width": 3840, "height": 2160}},
    {"name": "DP-8", "make": "Dell Inc.", "model": "DELL P2425H", "serial": "D1", "active": True,
     "rect": {"x": 1440, "y": 765, "width": 1920, "height": 1080}, "scale": 1.0, "transform": "normal",
     "current_mode": {"width": 1920, "height": 1080}},
    {"name": "DP-7", "make": "Samsung Electric Company", "model": "U28E590", "serial": "S2", "active": True,
     "rect": {"x": 3360, "y": 0, "width": 1440, "height": 2560}, "scale": 1.5, "transform": "270",
     "current_mode": {"width": 3840, "height": 2160}},
    {"name": "HDMI-A-1", "active": False, "rect": None},
]
# trimmed `hyprctl monitors -j`
HYPR = [
    {"name": "DP-6", "make": "Samsung", "model": "U28E590", "serial": "S1", "x": 0, "y": 62,
     "width": 3840, "height": 2160, "scale": 1.5, "transform": 1, "disabled": False},
    {"name": "DP-8", "make": "Dell", "model": "DELL P2425H", "serial": "D1", "x": 1440, "y": 765,
     "width": 1920, "height": 1080, "scale": 1.0, "transform": 0, "disabled": False},
]


def test_parse_sway_outputs():
    lay = wlroots.parse_sway_outputs(SWAY)
    assert [m.connector for m in lay.monitors] == ["DP-6", "DP-8", "DP-7"]   # inactive skipped
    left = lay.monitors[0]
    assert (left.width, left.height, left.rotation, left.scale) == (1440, 2560, 90, 1.5)
    assert wlroots.native_size(left) == (2160, 3840)
    assert lay.monitors[1].product == "DELL P2425H"


def test_parse_hyprland_monitors():
    lay = wlroots.parse_hyprland_monitors(HYPR)
    left, centre = lay.monitors
    assert (left.width, left.height, left.rotation) == (1440, 2560, 90)    # logical, rotated
    assert wlroots.native_size(left) == (2160, 3840)
    assert (centre.width, centre.height) == (1920, 1080)


@pytest.fixture
def recorder(monkeypatch):
    calls = []

    def fake_run(cmd, check=True):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")
    monkeypatch.setattr(wlroots, "_run", fake_run)
    return calls


def test_setters(recorder, monkeypatch, tmp_path):
    out = {"DP-6": tmp_path / "1 left.png", "DP-8": tmp_path / "2_centre.png"}
    for p in out.values():
        p.write_bytes(b"png")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    wlroots.set_outputs(out, setter="sway")
    assert recorder[-1][0] == "swaymsg"
    assert f"output DP-6 bg '{tmp_path / '1 left.png'}' fill #000000" in recorder[-1][1]   # quoted path

    recorder.clear()
    wlroots.set_outputs(out, setter="hyprpaper")
    assert [c[:3] for c in recorder] == [["hyprctl", "hyprpaper", "wallpaper"]] * 2
    first = recorder[0][3]
    assert first.startswith("DP-6, ") and first.endswith(", cover") and "/cache/panescape/hyprpaper/" in first
    # hyprpaper caches by path: a re-rendered file gets a new name
    recorder.clear()
    import os
    os.utime(out["DP-6"], ns=(1, 1))
    wlroots.set_outputs(out, setter="hyprpaper")
    assert recorder[0][3] != first

    recorder.clear()
    wlroots.set_outputs(out, setter="swww", transition=1.5)
    assert recorder[0][:4] == ["swww", "img", "--outputs", "DP-6"] and "fade" in recorder[0]

    spawned = []
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    class FakeProc:
        pid = 999999
        stderr = types.SimpleNamespace(close=lambda: None)

        def communicate(self, timeout=None):
            raise wlroots.subprocess.TimeoutExpired("swaybg", timeout)  # still running = good
    monkeypatch.setattr(wlroots.subprocess, "Popen", lambda cmd, **kw: spawned.append(cmd) or FakeProc())
    wlroots.set_outputs(out, setter="swaybg")
    assert spawned[0][:5] == ["swaybg", "-o", "DP-6", "-i", str(out["DP-6"])]
    assert (tmp_path / "panescape-swaybg.pid").read_text() == "999999"


def test_setter_choice(monkeypatch):
    monkeypatch.setenv("PANESCAPE_SETTER", "swww")
    assert wlroots.choose_setter() == "swww"
    with pytest.raises(BackendError):
        wlroots.choose_setter("feh")


def test_frame_index():
    st = {"mode": "slideshow", "order": ["a", "b", "c"], "interval_s": 60, "start": 1000.0}
    assert frame_index(st, now=1000 + 61) == (1, pytest.approx(59))
    assert frame_index(st, now=1000 + 3 * 60 * 7 + 125)[0] == 2
    assert frame_index({"mode": "single", "order": ["a"]})[0] == 0


def _per_output_backend(shown):
    from helpers import os_layout
    be = types.ModuleType("fakewl")
    be.KIND = "per_output"
    be.get_layout = os_layout
    be.match_monitors = wlroots.match_monitors
    be.native_size = wlroots.native_size
    be.set_outputs = lambda frame, transition=0, setter=None: shown.append(
        {k: Path(v).name for k, v in frame.items()})
    return be


def test_project_per_output_slideshow(tmp_path, monkeypatch):
    proj = make_project(tmp_path)
    monkeypatch.setattr(proj, "ensure_slideshow_runner", lambda: None)
    shown = []
    be = _per_output_backend(shown)
    proj.run(be, {"source": "cli", "images": ["a.png", "b.png"], "interval": "1m", "transition": "0"})
    # each output got its own native slice, no combined image
    assert shown == [{"DP-6": "1_left.png", "DP-8": "2_centre.png", "DP-7": "3_right.png"}]
    assert not list((tmp_path / "backgrounds").rglob("gnome-*.png"))
    st = proj.load_state()
    assert st["kind"] == "per_output" and st["mode"] == "slideshow" and len(st["composites"]) == 2

    # the runner shows the next image when its slot comes
    st["start"] -= 60
    proj.save_state(st)
    proj.run_slideshow(be, once=True)
    assert len(shown) == 2 and "/b/" in str(proj.load_state()["composites"][1]["DP-6"])

    # calibration pauses the runner; show_current puts the slideshow back
    (tmp_path / "backgrounds" / ".calibrate-restore.json").write_text("{}")
    proj.run_slideshow(be, once=True)
    assert len(shown) == 2
    assert proj.show_current(be) and len(shown) == 3


def test_switching_setter_stops_our_swaybg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(wlroots, "compositor", lambda: "hyprland")
    killed = []
    monkeypatch.setattr(wlroots.os, "kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(wlroots, "_run", lambda cmd, check=True: types.SimpleNamespace(returncode=0, stdout="ok"))
    wlroots._pidfile().write_text("4242")
    wlroots.set_outputs({"DP-6": tmp_path / "a.png"}, setter="swww")
    assert killed == [4242] and not wlroots._pidfile().exists()


def test_runner_started_by_the_window_is_not_turned_away(monkeypatch, tmp_path):
    """ensure_slideshow_runner writes the child's pid early; the child must not mistake it for another runner."""
    import os

    from panescape import cli
    proj = make_project(tmp_path, names=("a",))
    proj.backgrounds.mkdir(exist_ok=True)
    proj.runner_pidfile.write_text(str(os.getpid()))
    ran = []
    monkeypatch.setattr(cli, "_backend", lambda: types.SimpleNamespace(KIND="per_output"))
    monkeypatch.setattr(cli.Project, "run_slideshow", lambda self, be, once=False: ran.append(once))
    cli.main(["--root", str(tmp_path), "slideshow"])
    assert ran == [False]
