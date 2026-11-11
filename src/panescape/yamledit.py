"""All of panescape's YAML handling, through ruamel.yaml (pure Python, so it
installs on any Python, including ones too new for compiled wheels).

* ``safe_load`` / ``safe_dump``: plain dicts and lists, for *using* a file.
  YAML 1.2, but ``yes``/``no``/``on``/``off`` still mean true/false as they
  did with PyYAML, for files people wrote by hand. Files are UTF-8.
* ``load`` / ``save`` and friends: round-trip mode, for editing files the user
  also edits by hand (setup.yml, configurations/*.yml), so comments, key order
  and quoting survive.
"""

from __future__ import annotations

import io
import os
import re
import tempfile
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError
from ruamel.yaml.resolver import VersionedResolver

__all__ = ["YAMLError", "safe_load", "safe_dump", "load", "loads", "dumps", "save"]

# PyYAML's booleans beyond YAML 1.2's true/false (not y/n, which PyYAML left as text too)
_OLD_BOOLS = re.compile(r"^(?:yes|Yes|YES|no|No|NO|on|On|ON|off|Off|OFF)$")


class _Resolver(VersionedResolver):
    """YAML 1.2, plus yes/no/on/off as booleans, as PyYAML read them."""

    @property
    def versioned_resolver(self):
        table = super().versioned_resolver
        if not getattr(self, "_old_bools", False):
            self._old_bools = True
            for ch in "yYnNoO":
                table.setdefault(ch, []).append(("tag:yaml.org,2002:bool", _OLD_BOOLS))
        return table


def _safe() -> YAML:
    y = YAML(typ="safe", pure=True)  # pure: same behaviour everywhere, nothing to compile
    y.Resolver = _Resolver
    return y


def safe_load(source):
    """A Path, str or open file -> plain Python objects (None for an empty document).
    Pass a Path for files: it's read as UTF-8 on every system (Windows' default isn't)."""
    return _safe().load(source)


def safe_dump(data, *, flow_lists: bool = False, width: int = 120) -> str:
    """Keys stay in order; with ``flow_lists`` lists of scalars go on one line (``a.png: [lake, dawn]``)."""
    y = _safe()
    y.default_flow_style = None if flow_lists else False
    y.width = width
    y.allow_unicode = True
    y.representer.sort_base_mapping_type_on_output = False
    buf = io.StringIO()
    y.dump(data, buf)
    return buf.getvalue()


def _yaml() -> YAML:
    y = YAML()  # round-trip
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 120
    y.preserve_quotes = True
    return y


def load(path: str | Path) -> CommentedMap:
    path = Path(path)
    if not path.exists():
        return CommentedMap()
    doc = _yaml().load(path)  # a Path is read as bytes: UTF-8 whatever the system's code page
    return doc if doc is not None else CommentedMap()


def loads(text: str) -> CommentedMap:
    doc = _yaml().load(text)
    return doc if doc is not None else CommentedMap()


def dumps(doc) -> str:
    buf = io.StringIO()
    _yaml().dump(doc, buf)
    return buf.getvalue()


def save(path: str | Path, doc) -> None:
    """Write atomically (an editor or the slideshow runner may read it meanwhile)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = dumps(doc)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def plain(obj):
    """ruamel containers -> plain dicts / lists (for the loaders and the layout solver)."""
    if isinstance(obj, dict):
        return {str(k) if not isinstance(k, (int, float)) else k: plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, CommentedSeq)):
        return [plain(v) for v in obj]
    if hasattr(obj, "real") and type(obj).__name__.startswith("Scalar"):  # ScalarFloat etc.
        return float(obj) if isinstance(obj, float) else int(obj)
    if isinstance(obj, str):
        return str(obj)
    return obj


def set_key(m: CommentedMap, key: str, value, after: str | None = None) -> None:
    """Set ``key``; a new key goes after ``after`` (if present) instead of at the end."""
    if key in m or after is None or after not in m:
        m[key] = value
        return
    m.insert(list(m.keys()).index(after) + 1, key, value)


def drop_key(m: CommentedMap, key: str) -> None:
    if key in m:
        del m[key]
