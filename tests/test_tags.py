import pytest

from panescape.tags import FilterError, compile_filter, load_tags, save_tags, tags_for


def test_filter_grammar():
    def f(expr, *tags):
        return compile_filter(expr)[0](set(tags))

    assert f("landscape + water + !mountains", "landscape", "water")
    assert not f("landscape + water + !mountains", "landscape", "water", "mountains")
    assert f("landscape water !mountains", "landscape", "water")          # implicit AND
    assert f("beach | lake", "lake") and not f("beach | lake", "sea")
    assert f("(beach | lake) & !night", "beach") and not f("(beach | lake) & !night", "beach", "night")
    assert f("!night | city", "night", "city")                            # NOT binds tightest
    assert f("Water", "water") and f("city*", "citylights")
    assert f("not mountains and water", "water")
    for bad in ("water +", "(water", "| water", "water )"):
        with pytest.raises(FilterError):
            compile_filter(bad)


def test_tags_file(tmp_path):
    path = tmp_path / "tags.yml"
    path.write_text("lake.jpg: [Landscape, water]\nalps: landscape, mountains\n'city-*': [urban]\n")
    tags = load_tags(path)
    assert tags_for("lake.jpg", tags) == {"landscape", "water"}
    assert tags_for("alps.jpg", tags) == {"landscape", "mountains"}   # key without extension
    assert tags_for("city-1.jpg", tags) == {"urban"}
    save_tags(path, tags)
    assert load_tags(path) == tags


def test_two_word_tags_are_joined_with_a_hyphen(tmp_path):
    from panescape import tags as tagmod
    from panescape.unsplash import clean_tags
    assert tagmod.norm_tag("  Black Hole ") == "black-hole"
    assert tagmod.norm_tag("abstract_art") == "abstract-art"
    path = tmp_path / "tags.yml"
    path.write_text("# mine\nspace.jpg: [abstract art, black hole, blue]\nlake.jpg: [lake]\n")
    loaded = load_tags(path)
    assert loaded["space.jpg"] == {"abstract-art", "black-hole", "blue"}
    pred, _ = compile_filter("black-hole + !lake")
    assert pred(tags_for("space.jpg", loaded)) and not pred(tags_for("lake.jpg", loaded))
    # the next edit tidies the file, keeping its comment
    tagmod.edit_tags(path, {"lake.jpg": ({"dawn"}, set())})
    text = path.read_text()
    assert "# mine" in text and "abstract-art" in text and "abstract art" not in text
    # Unsplash's two-word tags arrive in the same form
    assert clean_tags({"tags": [{"title": "Black Hole"}, {"title": "black hole"}, {"title": "galaxy"}]}) == \
        ["black-hole", "galaxy"]
