"""
Shop profile: capture every run decision once, in a file.

WHY
---
Every setting this pipeline takes -- sheet size, kerf, mark style, nesting
mode -- is a decision that gets made once for the course and then has to be
remembered correctly on every subsequent run, by whoever is running it.
Passing them as command-line flags means the person at the keyboard has to
know all of them, which is exactly the kind of manual step this whole tool
exists to remove.

So they live in a JSON profile that is written once, checked in beside the
code, and used forever:

    python -m lgpipe.cli --raw raw/ --out out/ --config shop.json

Next year's TA runs that same line without knowing what a kerf is. A flag
given explicitly on the command line still wins, for one-off experiments.
"""

from __future__ import annotations

import json
import os

DEFAULTS = {
    # 48 x 48 in, the stock the course buys. Used whenever no shop.json is
    # given, so a run without one still nests onto the real sheet.
    "sheet": "1219.2x1219.2",
    "kerf": 0.0,
    "mark": "group-code",
    # "angular" draws letters with long straight strokes only; "standard" uses
    # small chamfers to round the curves, which a laser stair-steps on a small
    # mark. See lgpipe/strokefont.py.
    "font": "angular",
    "nest_mode": "corner",
    "part_gap": 4.0,
    "sheet_margin": 8.0,
    "logo": None,
    "material_thickness": 1.52,
    # Set each term from the app's "Each semester" page. They live here rather
    # than in machine-local prefs so they travel with the project folder.
    "seed": "2026",
    "expect_lo": 3,
    "expect_hi": 4,
    # Layer name / colour / line style per role live under "layers", written
    # by the Settings page once the colours are known. There is deliberately
    # no default block here: until someone has cut the test card and ticked
    # "colours checked", every run warns.
    "colours_confirmed": False,
    "_comment": (
        "Shop profile for the MAAE/AERO 2001 landing gear pipeline. "
        "kerf 0 means the geometry stays nominal and the MACHINE applies cut "
        "compensation (outer profile outside the line, holes inside). Set kerf "
        "to the shop's MEASURED kerf only if you also switch machine "
        "compensation OFF -- doing both puts parts a full kerf out."
    ),
}

# Config keys -> argparse dest names, where they differ.
_ALIASES = {"nest_mode": "nest_mode", "nest-mode": "nest_mode"}


def load(path: str | None) -> dict:
    """Read a profile, falling back to defaults for anything absent."""
    cfg = dict(DEFAULTS)
    cfg["_layers_from_profile"] = False
    if not path:
        return cfg
    if not os.path.exists(path):
        # A folder that has been unzipped but never opened in the app has no
        # shop.json yet -- only the starter it is made from. Use that rather
        # than refusing to run. The layer warning still fires, because a
        # starter is by definition not colours anyone checked on a machine.
        starter = os.path.join(os.path.dirname(os.path.abspath(path)),
                               "shop.default.json")
        if os.path.exists(starter):
            print(f"No {os.path.basename(path)} yet -- using shop.default.json.")
            path = starter
        else:
            raise FileNotFoundError(f"Config not found: {path}")
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    for k, v in data.items():
        if k.startswith("_"):
            continue
        cfg[_ALIASES.get(k, k)] = v
    # Did a human CHECK the laser colours on the machine? Having a layers
    # block is not enough -- the Settings page writes one the first time
    # anything is saved. Only the explicit tick counts. Every other setting
    # is harmless to leave at a default; this one decides whether the machine
    # marks a part or cuts it in half.
    cfg["_layers_from_profile"] = bool(data.get("layers")) and \
        bool(data.get("colours_confirmed", False))

    # Profiles saved by the version that had one global "letters" setting:
    # carry it over onto the group ID row, which is where it now lives.
    legacy = str(data.get("letters", "")).strip().lower()
    if legacy in ("hairline", "filled"):
        layers = dict(cfg.get("layers") or {})
        mt = dict(layers.get("MARK_TEXT") or {})
        if "style" not in mt:
            mt["style"] = legacy
            if data.get("stroke_pct") is not None:
                mt["stroke"] = data.get("stroke_pct")
            layers["MARK_TEXT"] = mt
            cfg["layers"] = layers
    return cfg


def write_template(path: str) -> None:
    """Write a starter profile for the shop to fill in."""
    with open(path, "w") as f:
        json.dump(DEFAULTS, f, indent=2)
        f.write("\n")


def describe(cfg: dict) -> str:
    keys = ("sheet", "kerf", "mark", "nest_mode", "material_thickness",
            "part_gap")
    return "  ".join(f"{k}={cfg.get(k)}" for k in keys)
