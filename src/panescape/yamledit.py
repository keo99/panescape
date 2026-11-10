"""Editing panescape's YAML files without losing the user's comments or layout.

Reading for *use* goes through PyYAML (plain dicts). Anything that writes a
file the user also edits by hand (setup.yml, configurations/*.yml) goes
through ruamel.yaml's round-trip mode, so comments, key order and quoting
survive.
"""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq


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
    with open(path) as f:
        doc = _yaml().load(f)
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
        with os.fdopen(fd, "w") as f:
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
