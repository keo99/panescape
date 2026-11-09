"""Image tags (``pictures/tags.yml``) and boolean tag filters.

tags.yml maps file names (or globs) to tag lists::

    mendhak-Red_Acer.jpg: [nature, autumn, trees]
    "city-*": [urban, night]          # globs add tags to every match

Filter syntax (case-insensitive)::

    landscape + water + !mountains    AND: + & and   (or just a space)
    beach | lake                      OR:  | , or
    !mountains   not mountains        NOT: ! not
    (beach | lake) + !night           parentheses group
    city*                             globs match tag names
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import yaml

_TOKEN = re.compile(r"\s*(\(|\)|!|&|\+|\||,|[^\s()!&+|,]+)")


class FilterError(ValueError):
    pass


def norm_tag(t) -> str:
    return str(t).strip().lower()


def load_tags(path: Path) -> dict[str, set[str]]:
    if not path.exists():
        return {}
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise FilterError(f"{path} must map image names to tag lists")
    out = {}
    for key, tags in raw.items():
        if isinstance(tags, str):
            tags = [t for t in re.split(r"[,\s]+", tags) if t]
        out[str(key)] = {norm_tag(t) for t in (tags or [])}
    return out


def save_tags(path: Path, tags: dict[str, set[str]]) -> None:
    data = {k: sorted(v) for k, v in sorted(tags.items()) if v}
    header = ("# Image tags for panescape. Keys are file names in this folder (globs allowed).\n"
              "# Filter in setup.yml with e.g.  filter: landscape + water + !mountains\n")
    path.write_text(header + yaml.safe_dump(data, sort_keys=False, default_flow_style=None, width=100))


def tags_for(name: str, tags: dict[str, set[str]]) -> set[str]:
    out: set[str] = set()
    stem = Path(name).stem
    for key, ts in tags.items():
        if key == name or key == stem or fnmatch.fnmatch(name, key):
            out |= ts
    return out


def compile_filter(expr: str):
    """Return (predicate(tagset) -> bool, set of tag terms used)."""
    tokens = _TOKEN.findall(expr or "")
    if "".join(tokens).replace(" ", "") != re.sub(r"\s+", "", expr or ""):
        raise FilterError(f"cannot parse filter {expr!r}")
    pos = 0
    terms: set[str] = set()

    def peek():
        return tokens[pos].lower() if pos < len(tokens) else None

    def take():
        nonlocal pos
        pos += 1
        return tokens[pos - 1]

    def parse_or():
        left = parse_and()
        while peek() in ("|", ",", "or"):
            take()
            right = parse_and()
            left = (lambda a, b: lambda s: a(s) or b(s))(left, right)
        return left

    def parse_and():
        left = parse_not()
        while peek() is not None and peek() not in ("|", ",", "or", ")"):
            if peek() in ("+", "&", "and"):
                take()
            right = parse_not()  # adjacent terms = implicit AND
            left = (lambda a, b: lambda s: a(s) and b(s))(left, right)
        return left

    def parse_not():
        if peek() in ("!", "not"):
            take()
            inner = parse_not()
            return lambda s: not inner(s)
        return parse_atom()

    def parse_atom():
        tok = peek()
        if tok is None:
            raise FilterError(f"filter {expr!r} ends unexpectedly")
        if tok == "(":
            take()
            inner = parse_or()
            if peek() != ")":
                raise FilterError(f"missing ')' in filter {expr!r}")
            take()
            return inner
        if tok in (")", "+", "&", "|", ",", "and", "or"):
            raise FilterError(f"unexpected {tok!r} in filter {expr!r}")
        term = norm_tag(take())
        terms.add(term)
        if any(c in term for c in "*?["):
            return lambda s: any(fnmatch.fnmatch(t, term) for t in s)
        return lambda s: term in s

    if not tokens:
        return (lambda s: True), set()
    pred = parse_or()
    if pos != len(tokens):
        raise FilterError(f"unexpected {tokens[pos]!r} in filter {expr!r}")
    return pred, terms
