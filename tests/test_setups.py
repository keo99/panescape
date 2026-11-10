"""Named setups in setup.yml, and editing it without losing comments."""

import pytest

from panescape.cli import main
from panescape.project import Project, ProjectError

SINGLE = """\
# my desk
configuration: desk   # the one with three screens
mode: slideshow
interval: 2h
"""

MULTI = """\
setter: swaybg
active: evening
setups:
  work:
    configuration: desk
    filter: abstract
  evening:
    configuration: desk
    interval: 30m
"""


def project(tmp_path, text):
    (tmp_path / "setup.yml").write_text(text)
    (tmp_path / "configurations").mkdir()
    (tmp_path / "configurations" / "desk.yml").write_text("monitors:\n  - {resolution: FHD, size: 24in}\n")
    return Project(tmp_path)


def test_single_setup_reads_as_before(tmp_path):
    p = project(tmp_path, SINGLE)
    assert p.setup_names() == ["default"]
    assert p.active_setup() == "default"
    assert p.load_setup()["interval"] == "2h"


def test_named_setups_merge_shared_keys(tmp_path):
    p = project(tmp_path, MULTI)
    assert p.setup_names() == ["work", "evening"]
    assert p.active_setup() == "evening"
    assert p.load_setup() == {"setter": "swaybg", "configuration": "desk", "interval": "30m"}
    assert p.load_setup("work")["filter"] == "abstract"
    with pytest.raises(ProjectError):
        p.load_setup("nope")


def test_switching_keeps_comments(tmp_path):
    p = project(tmp_path, MULTI.replace("filter: abstract", "filter: abstract   # keep me"))
    p.use_setup("work")
    text = (tmp_path / "setup.yml").read_text()
    assert "active: work" in text and "# keep me" in text
    assert p.active_setup() == "work"


def test_second_setup_converts_and_back(tmp_path):
    p = project(tmp_path, SINGLE)
    p.create_setup("evening")
    assert p.setup_names() == ["default", "evening"]
    assert p.active_setup() == "evening"
    text = (tmp_path / "setup.yml").read_text()
    assert text.startswith("# my desk") and "# the one with three screens" in text
    assert p.load_setup("evening")["interval"] == "2h"  # copied from the active one
    p.update_setup({"interval": "5m", "shuffle": True})
    assert p.load_setup()["interval"] == "5m" and p.load_setup("default")["interval"] == "2h"
    p.rename_setup("evening", "night")
    assert p.setup_names() == ["default", "night"] and p.active_setup() == "night"
    p.delete_setup("night")
    # back to the simple single-setup form, comments intact
    text = (tmp_path / "setup.yml").read_text()
    assert "setups:" not in text and "# the one with three screens" in text
    assert p.load_setup()["interval"] == "2h"
    with pytest.raises(ProjectError):
        p.delete_setup("default")


def test_rename_config_updates_setups(tmp_path):
    p = project(tmp_path, MULTI)
    p.rename_config("desk", "office")
    assert p.config_names() == ["office"]
    assert p.load_setup("work")["configuration"] == "office"
    assert p.setups_using("office") == ["work", "evening"]


def test_bad_names(tmp_path):
    p = project(tmp_path, MULTI)
    for bad in ("", "a/b", ".hidden", "work"):
        with pytest.raises(ProjectError):
            p.create_setup(bad)


def test_cli_setup_command(tmp_path, capsys):
    project(tmp_path, MULTI)
    assert main(["--root", str(tmp_path), "setup"]) == 0
    out = capsys.readouterr().out
    assert "* evening" in out and "  work" in out
    assert main(["--root", str(tmp_path), "setup", "work"]) == 0
    assert Project(tmp_path).active_setup() == "work"
    assert main(["--root", str(tmp_path), "setup", "missing"]) == 1
