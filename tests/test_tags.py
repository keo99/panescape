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
