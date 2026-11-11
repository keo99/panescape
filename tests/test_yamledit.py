"""The plain YAML loader/dumper (ruamel.yaml, replacing PyYAML)."""

from panescape import tags, yamledit


def test_reads_like_pyyaml_did():
    data = yamledit.safe_load("shuffle: yes\nfade: Off\nn: x\nletters: [y, n]\nquoted: 'no'\n"
                              "size: 27\nratio: 1.5\nwhen: 2026-11-11\nempty:\n")
    assert data["shuffle"] is True and data["fade"] is False
    assert data["letters"] == ["y", "n"] and data["quoted"] == "no"  # PyYAML left y/n alone too
    assert "n" in data  # not turned into the key False
    assert data["size"] == 27 and data["ratio"] == 1.5 and data["empty"] is None
    assert str(data["when"]) == "2026-11-11"
    assert yamledit.safe_load("") is None


def test_dump_keeps_order_and_text_and_reads_back():
    data = {"name": "desk", "monitors": [{"id": "left", "size": [600, 340]}], "flag": "yes", "a": 1}
    text = yamledit.safe_dump(data)
    assert text.index("name") < text.index("monitors") < text.index("flag")
    assert yamledit.safe_load(text) == data  # the string "yes" stays a string


def test_files_are_utf8(tmp_path):
    path = tmp_path / "tags.yml"
    tags.save_tags(path, {"café.jpg": {"crème", "lake"}})
    raw = path.read_bytes().decode("utf-8")
    assert "café.jpg: [crème, lake]" in raw  # readable, one line per picture
    assert tags.load_tags(path) == {"café.jpg": {"crème", "lake"}}
    assert yamledit.safe_load(path)["café.jpg"] == ["crème", "lake"]
