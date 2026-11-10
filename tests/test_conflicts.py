"""Warnings about other programs that replace panescape's wallpaper."""

import json

from panescape.backends import base


def setup(monkeypatch, tmp_path, procs, enabled=None):
    monkeypatch.setattr(base, "running", lambda name: name in procs)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    if enabled is not None:
        (tmp_path / ".config").mkdir()
        (tmp_path / ".config" / "org.gabmus.hydrapaper.json").write_text(json.dumps({"enable_daemon": enabled}))


def test_nothing_running(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path, set())
    assert base.competing_tools() == []


def test_hydrapaper_daemon_enabled(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path, {"hydrapaperd"}, enabled=True)
    [w] = base.competing_tools()
    assert w.startswith("HydraPaper") and "preferences" in w


def test_hydrapaper_daemon_switched_off_but_still_running(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path, {"hydrapaperd"}, enabled=False)
    assert base.competing_tools() == []


def test_variety(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path, {"variety"})
    assert [w.split()[0] for w in base.competing_tools()] == ["Variety"]


def test_running_finds_this_process():
    import os
    from pathlib import Path
    me = Path(f"/proc/{os.getpid()}/comm").read_text().strip()
    assert base.running(me) and not base.running("no-such-process-x")
