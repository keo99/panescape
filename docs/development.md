# Development

```bash
pip install -e '.[dev,gui]'
pytest                       # GUI tests run headless (QT_QPA_PLATFORM=offscreen)
ruff check .
python docs/make_images.py   # rebuild the images and the window screenshots
```

## The documentation

This site is built with [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/)
from the `docs/` folder and `mkdocs.yml`, and published to GitHub Pages
whenever `main` changes.

```bash
pip install -r docs/requirements.txt
mkdocs serve                 # live preview at http://127.0.0.1:8000
mkdocs build --strict        # what CI runs: broken links fail the build
```

The pictures come from `docs/make_images.py`, which uses the example layout
and a public-domain photo, never your own files.

## Releasing

Set `__version__` in `src/panescape/__init__.py`, commit, then tag and push,
e.g. `git tag v0.2.0 && git push origin main v0.2.0`. The release workflow checks
that the tag matches the version, runs the tests, and publishes to PyPI once
a maintainer approves the `pypi` deployment.

## Adding a monitor model

Add an entry to `src/panescape/models.py`, using the spec sheet's active area,
outer size without stand, and the bezels if you've measured them. Then open a PR.
