"""Built-in physical data for known monitor models.

Every entry is given in the panel's *native landscape* orientation:

* ``active_area``: lit pixel area in mm (width, height), from the spec sheet.
* ``bezel``: housing border around the active area in mm (top/right/bottom/left).
  These are derived from (outer size - active size). Where the spec sheet only
  gives a total, the top/bottom split is an estimate, flagged with
  ``bezel_estimated``. Measure your own monitor and override in the config
  for the best result.

Contributions welcome: add your monitor here and open a PR.
"""

from __future__ import annotations

import re

MODELS: dict[str, dict] = {
    "samsung u28e590": {
        "aliases": ["u28e590", "u28e590d", "lu28e590ds", "ue590"],
        "resolution": (3840, 2160),
        "active_area": (620.93, 341.28),
        # outer (no stand) 660.9 x 381.3 mm -> 20.0 mm per side, 40.0 mm top+bottom
        "bezel": {"top": 16.0, "right": 20.0, "bottom": 24.0, "left": 20.0},
        "bezel_estimated": True,
        "source": "Samsung LU28E590DS spec sheet",
    },
    "dell p2425h": {
        "aliases": ["p2425h", "dell24p2425h"],
        "resolution": (1920, 1080),
        "active_area": (527.04, 296.46),
        # outer (no stand) 538.64 x 314.23 mm -> 5.8 mm per side, 17.77 mm top+bottom
        "bezel": {"top": 5.8, "right": 5.8, "bottom": 11.97, "left": 5.8},
        "bezel_estimated": True,
        "source": "Dell P2425H spec sheet",
    },
    "dell p2422h": {
        "aliases": ["p2422h", "dell24p2422h"],
        "resolution": (1920, 1080),
        "active_area": (527.04, 296.46),  # 0.2745 mm pixel pitch
        # outer (no stand) 537.8 x 318.5 mm -> 5.38 mm per side, 22.04 mm top+bottom
        "bezel": {"top": 5.4, "right": 5.38, "bottom": 16.64, "left": 5.38},
        "bezel_estimated": True,
        "source": "Dell P2422H spec sheet (pixel pitch, size without stand)",
    },
    "samsung s22b300": {
        "aliases": ["s22b300", "s22b300b", "s22b300h", "s22b300n", "ls22b300"],
        "resolution": (1920, 1080),
        "active_area": (476.64, 268.11),  # 0.24825 mm pixel pitch
        # no bezel: the manual's outer sizes aren't clearly for this model; measure them
        "source": "Samsung S22B300 user manual (display area)",
    },
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def lookup(name: str | None) -> dict | None:
    """Find a model by name, alias or the product string reported by the OS."""
    if not name:
        return None
    key = _norm(name)
    for model, data in MODELS.items():
        candidates = [_norm(model)] + [_norm(a) for a in data.get("aliases", [])]
        if key in candidates:
            return {"model": model, **data}
    # looser: product string contains an alias (e.g. "DELL P2425H", "U28E590")
    for model, data in MODELS.items():
        for a in [model] + data.get("aliases", []):
            na = _norm(a)
            if len(na) >= 5 and (na in key or key in na):
                return {"model": model, **data}
    return None
