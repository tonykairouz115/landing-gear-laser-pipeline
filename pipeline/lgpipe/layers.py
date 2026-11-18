"""
Shop layer scheme for MAAE/AERO 2001 landing-gear flat patterns.

Design intent
-------------
Every output entity carries BOTH a meaningful layer NAME and a fixed
AutoCAD Color Index (ACI) + true RGB colour. Different laser front-ends key
off different things:

  * LightBurn assigns a cut layer per unique COLOUR it encounters.
  * Trotec JobControl / Epilog print drivers map by RGB colour.
  * Most CAM/nesting packages and AutoCAD map by LAYER NAME.

Setting all three means one export works on any of them, and the shop only
has to build its colour->process template once.

Process ORDER matters and is encoded in the scheme:
  1. marks first, while the blank is still fully supported by the sheet
  2. interior cuts next, before the part can shift
  3. outer profile last, which is what frees the part
"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class LayerSpec:
    name: str
    aci: int                     # AutoCAD Color Index
    rgb: tuple[int, int, int]
    process: str                 # "mark" | "cut" | "reference"
    order: int                   # lower runs first
    description: str
    cut_side: str = "-"          # "outside" | "inside" | "on line" | "-"
    # How this role's lines are drawn.
    #   "hairline"  a single line the laser FOLLOWS (every cut, and the default)
    #   "filled"    thickened into a solid shape with no outline, which the
    #               laser ENGRAVES (sweeps across) instead of following.
    # `stroke` is the thickness when filled: for the group ID a percent of the
    # letter height, for bend lines a width in mm. Only the roles in
    # FILLABLE can be filled -- a filled cut outline would engrave the whole
    # part, and a reference line must never be output at all.
    style: str = "hairline"
    stroke: float = 0.0


# Roles whose lines may be filled, with what their stroke number means.
FILLABLE = {
    "MARK_TEXT": ("% of letter height", 13.0, 5.0, 25.0),
    "MARK_BEND": ("mm wide", 0.5, 0.1, 2.0),
}


def style_of(role: str) -> tuple[str, float]:
    """("hairline", 0) or ("filled", stroke) for a role, as currently set."""
    spec = SCHEME.get(role)
    if spec is None or role not in FILLABLE or spec.style != "filled":
        return "hairline", 0.0
    unit, default, lo, hi = FILLABLE[role]
    val = spec.stroke if spec.stroke > 0 else default
    return "filled", max(lo, min(hi, val))


def style_label(role: str) -> str:
    """Human description of a role's line style, for reports."""
    style, stroke = style_of(role)
    if style == "hairline":
        return "hairline"
    unit = FILLABLE[role][0]
    return f"filled, {stroke:g} {unit}" if unit.startswith("mm") \
        else f"filled, {stroke:g}{unit}"


SCHEME: dict[str, LayerSpec] = {
    "MARK_TEXT": LayerSpec(
        "MARK_TEXT", 6, (255, 0, 255), "mark", 1,
        "Group ID and course logo. Low-power surface mark.",
    ),
    "MARK_BEND": LayerSpec(
        "MARK_BEND", 3, (0, 255, 0), "mark", 2,
        "Bend centre lines. Low-power scribe so the brake operator can align.",
    ),
    "CUT_INNER": LayerSpec(
        "CUT_INNER", 5, (0, 0, 255), "cut", 3,
        "Interior profiles (holes, slots). Through-cut, run before outer. "
        "Compensate INSIDE the line so holes finish at nominal diameter.",
        cut_side="inside",
    ),
    "CUT_OUTER": LayerSpec(
        "CUT_OUTER", 1, (255, 0, 0), "cut", 4,
        "Outer profile. Through-cut, runs last to release the part. "
        "Compensate OUTSIDE the line so the part finishes at nominal size.",
        cut_side="outside",
    ),
    "REF_EXTENT": LayerSpec(
        "REF_EXTENT", 8, (128, 128, 128), "reference", 99,
        "Bend extent lines. Reference only -- must NOT be output by the machine.",
    ),
    # Deliberately the SAME colour as REF_EXTENT. Both must never be output,
    # so they are one setting to disable rather than two to remember -- and it
    # keeps ACI 7 free, which matters because black is a common cut colour and
    # a sheet frame sharing it would cut the sheet in half.
    "REF_FRAME": LayerSpec(
        "REF_FRAME", 8, (128, 128, 128), "reference", 99,
        "Sheet outline and nesting annotation. Reference only.",
    ),
}

# Layer-name fragments Fusion / Inventor use, mapped to our scheme.
# Matching is case-insensitive substring; geometry analysis is authoritative
# and overrides these when they disagree.
SOURCE_HINTS: list[tuple[str, str]] = [
    ("outer", "CUT_OUTER"),
    ("outside", "CUT_OUTER"),
    ("iv_outer", "CUT_OUTER"),
    ("interior", "CUT_INNER"),
    ("inner", "CUT_INNER"),
    ("iv_interior", "CUT_INNER"),
    ("bend_extent", "REF_EXTENT"),
    ("bend extent", "REF_EXTENT"),
    ("extent", "REF_EXTENT"),
    ("iv_bend_extent", "REF_EXTENT"),
    ("bend", "MARK_BEND"),
    ("iv_bend", "MARK_BEND"),
    ("centre", "MARK_BEND"),
    ("center", "MARK_BEND"),
    ("tangent", "REF_EXTENT"),
    ("arc_centers", "REF_EXTENT"),
    ("text", "MARK_TEXT"),
]


def hint_for(source_layer: str) -> str | None:
    """Best-guess target layer from a source DXF layer name, or None."""
    s = (source_layer or "").strip().lower()
    if not s:
        return None
    for frag, target in SOURCE_HINTS:
        if frag in s:
            return target
    return None


def ensure_layers(doc) -> None:
    """Create every scheme layer in the document with correct colours."""
    for spec in SCHEME.values():
        if spec.name in doc.layers:
            layer = doc.layers.get(spec.name)
        else:
            layer = doc.layers.add(spec.name)
        layer.color = spec.aci
        layer.rgb = spec.rgb
        layer.description = spec.description


def colour_clashes() -> list[tuple[bool, str]]:
    """
    Find roles that end up indistinguishable to the machine.

    A laser front-end picks its power and speed from ONE property -- usually the
    colour. Two roles wearing the same colour are therefore one machine setting,
    and nothing downstream can separate them again.

    Sometimes that is fine: MARK_TEXT and MARK_BEND on the same colour just
    means the group ID and the bend lines are scribed identically, which is what
    the shop's own profile usually wants anyway.

    Sometimes it destroys the sheet. REF_FRAME is the sheet outline; if it
    shares a colour with CUT_OUTER, the machine cuts around the whole sheet and
    through the annotation text. A mark sharing with a cut is the same class of
    mistake in the other direction.

    Front-ends disagree about WHICH property they read -- some the colour index,
    some the RGB -- so a clash in either one is a clash.

    Returns (dangerous, message) pairs.
    """
    out: list[tuple[bool, str]] = []
    seen: set[frozenset] = set()
    for key, label in (("aci", "colour index"), ("rgb", "RGB")):
        buckets: dict = {}
        for spec in SCHEME.values():
            buckets.setdefault(getattr(spec, key), []).append(spec)
        for value, specs in buckets.items():
            if len(specs) < 2:
                continue
            names = frozenset(s.name for s in specs)
            if names in seen:
                continue          # already reported under the other property
            seen.add(names)
            procs = sorted({s.process for s in specs})
            shown = ", ".join(sorted(s.name for s in specs))
            colour = value if key == "aci" else "%d,%d,%d" % value
            if len(procs) == 1:
                out.append((False,
                            f"{shown} share {label} {colour}. Both are "
                            f"'{procs[0]}', so the machine treats them the same "
                            f"-- intended, as long as you meant it."))
            else:
                out.append((True,
                            f"{shown} share {label} {colour} but must be run "
                            f"DIFFERENTLY ({' vs '.join(procs)}). The machine "
                            f"cannot tell them apart: whichever process you set "
                            f"for that colour is applied to both."))
    return out


def process_table() -> list[LayerSpec]:
    """Scheme ordered the way the machine should run it."""
    return sorted(SCHEME.values(), key=lambda s: (s.order, s.name))


def apply_overrides(overrides: dict | None) -> None:
    """
    Let the shop profile rename or recolour the layers.

    The internal ROLE keys (CUT_OUTER, MARK_BEND, ...) never change -- they are
    what the classifier assigns. What the profile controls is how each role is
    written into the DXF: its layer name, its AutoCAD colour index, and its RGB.

    That matters because a shop may already have a template keyed to their own
    names and colours ("CUT" and "ENGRAVE" in red and blue, say). Rather than
    making them rebuild it around mine, the file can be made to match theirs.

    Profile shape:

        "layers": {
          "CUT_OUTER": { "name": "CUT", "aci": 1, "rgb": [255, 0, 0] },
          "MARK_BEND": { "name": "ENGRAVE", "aci": 3 }
        }

    Any key may be omitted; only what is given is changed.
    """
    if not overrides:
        return
    from dataclasses import replace
    for role, over in overrides.items():
        spec = SCHEME.get(role)
        if spec is None or not isinstance(over, dict):
            continue
        kw = {}
        if over.get("name"):
            kw["name"] = str(over["name"])
        if over.get("aci") is not None:
            kw["aci"] = int(over["aci"])
        rgb = over.get("rgb")
        if rgb and len(rgb) == 3:
            kw["rgb"] = (int(rgb[0]), int(rgb[1]), int(rgb[2]))
        style = str(over.get("style", "")).strip().lower()
        if style in ("hairline", "filled"):
            kw["style"] = style if role in FILLABLE else "hairline"
        if over.get("stroke") is not None:
            try:
                kw["stroke"] = float(over["stroke"])
            except (TypeError, ValueError):
                pass
        if kw:
            SCHEME[role] = replace(spec, **kw)
