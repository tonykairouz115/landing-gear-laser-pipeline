"""
Self-contained single-stroke (engraving) vector font.

Why this exists instead of using a system font:
  A TrueType font renders each glyph as a CLOSED OUTLINE. A laser will trace
  the outline of every letter, so "G07" becomes hollow shapes that take ~3x
  longer to mark and look wrong. Engraving needs a SINGLE-STROKE font, where
  each letter is just centreline paths.

  SHX/LFF stroke fonts exist but require the font file to be present on
  whatever machine opens the DXF. Embedding the strokes here means the output
  DXF contains plain LWPOLYLINE geometry -- it renders identically in
  LightBurn, JobControl, AutoCAD, or anything else, with zero dependencies.

Glyph coordinate system: x in [0,6], y in [0,10] (cap height 10, advance 8).
"""

from __future__ import annotations

# Each glyph is a list of strokes; each stroke is a list of (x, y) points.
_G: dict[str, list[list[tuple[float, float]]]] = {
    " ": [],
    "A": [[(0, 0), (3, 10), (6, 0)], [(1.2, 4), (4.8, 4)]],
    "B": [
        [(0, 0), (0, 10), (4, 10), (5.8, 8.5), (4.2, 5.2), (0, 5.2)],
        [(4.2, 5.2), (6, 3), (4.2, 0), (0, 0)],
    ],
    "C": [[(6, 8), (4, 10), (2, 10), (0, 8), (0, 2), (2, 0), (4, 0), (6, 2)]],
    "D": [[(0, 0), (0, 10), (3, 10), (6, 7), (6, 3), (3, 0), (0, 0)]],
    "E": [[(6, 10), (0, 10), (0, 0), (6, 0)], [(0, 5), (4, 5)]],
    "F": [[(6, 10), (0, 10), (0, 0)], [(0, 5), (4, 5)]],
    "G": [
        [(6, 8), (4, 10), (2, 10), (0, 8), (0, 2), (2, 0), (4, 0), (6, 2), (6, 4)],
        [(6, 4), (3.5, 4)],
    ],
    "H": [[(0, 0), (0, 10)], [(6, 0), (6, 10)], [(0, 5), (6, 5)]],
    "I": [[(1, 10), (5, 10)], [(3, 10), (3, 0)], [(1, 0), (5, 0)]],
    "J": [[(6, 10), (6, 2.5), (4, 0), (2, 0), (0, 2)]],
    "K": [[(0, 0), (0, 10)], [(6, 10), (0.5, 4.2)], [(2.2, 5.8), (6, 0)]],
    "L": [[(0, 10), (0, 0), (6, 0)]],
    "M": [[(0, 0), (0, 10), (3, 4.5), (6, 10), (6, 0)]],
    "N": [[(0, 0), (0, 10), (6, 0), (6, 10)]],
    "O": [[(2, 10), (4, 10), (6, 8), (6, 2), (4, 0), (2, 0), (0, 2), (0, 8), (2, 10)]],
    "P": [[(0, 0), (0, 10), (4, 10), (6, 8.2), (6, 6.8), (4, 5), (0, 5)]],
    "Q": [
        [(2, 10), (4, 10), (6, 8), (6, 2), (4, 0), (2, 0), (0, 2), (0, 8), (2, 10)],
        [(3.6, 2.6), (6, -0.4)],
    ],
    "R": [
        [(0, 0), (0, 10), (4, 10), (6, 8.2), (6, 6.8), (4, 5), (0, 5)],
        [(3, 5), (6, 0)],
    ],
    "S": [
        [(6, 8.4), (4, 10), (2, 10), (0, 8.4), (0, 6.4), (2, 5), (4, 5),
         (6, 3.6), (6, 1.6), (4, 0), (2, 0), (0, 1.6)]
    ],
    "T": [[(0, 10), (6, 10)], [(3, 10), (3, 0)]],
    "U": [[(0, 10), (0, 2), (2, 0), (4, 0), (6, 2), (6, 10)]],
    "V": [[(0, 10), (3, 0), (6, 10)]],
    "W": [[(0, 10), (1.5, 0), (3, 6), (4.5, 0), (6, 10)]],
    "X": [[(0, 0), (6, 10)], [(0, 10), (6, 0)]],
    "Y": [[(0, 10), (3, 5), (6, 10)], [(3, 5), (3, 0)]],
    "Z": [[(0, 10), (6, 10), (0, 0), (6, 0)]],
    "0": [
        [(2, 10), (4, 10), (6, 8), (6, 2), (4, 0), (2, 0), (0, 2), (0, 8), (2, 10)],
        [(0.8, 1.6), (5.2, 8.4)],
    ],
    "1": [[(1, 8), (3, 10), (3, 0)], [(1, 0), (5, 0)]],
    "2": [[(0, 8.2), (2, 10), (4, 10), (6, 8.2), (6, 6.2), (0, 0), (6, 0)]],
    "3": [
        [(0, 10), (6, 10), (2.6, 6)],
        [(2.6, 6), (5, 6), (6, 4.6), (6, 1.6), (4, 0), (1.5, 0), (0, 1.6)],
    ],
    "4": [[(4.4, 0), (4.4, 10), (0, 3.2), (6, 3.2)]],
    "5": [[(6, 10), (0, 10), (0, 5.8), (4, 5.8), (6, 4), (6, 1.8), (4, 0), (1.5, 0), (0, 1.6)]],
    "6": [
        [(5, 10), (2, 10), (0, 7), (0, 2), (2, 0), (4, 0), (6, 2), (6, 4), (4, 6), (2, 6), (0, 4)]
    ],
    "7": [[(0, 10), (6, 10), (2, 0)]],
    "8": [
        [(2.2, 5), (0.4, 6.6), (0.4, 8.4), (2.2, 10), (3.8, 10), (5.6, 8.4),
         (5.6, 6.6), (3.8, 5), (2.2, 5)],
        [(2.2, 5), (0, 3.2), (0, 1.5), (2, 0), (4, 0), (6, 1.5), (6, 3.2), (3.8, 5)],
    ],
    "9": [
        [(1, 0), (4, 0), (6, 3), (6, 8), (4, 10), (2, 10), (0, 8), (0, 6.2),
         (2, 4.4), (4, 4.4), (6, 6.2)]
    ],
    "-": [[(1, 5), (5, 5)]],
    "_": [[(0, 0), (6, 0)]],
    ".": [[(2.6, 0), (3.4, 0)]],
    ",": [[(3.4, 0.6), (2.4, -1.2)]],
    "/": [[(0, 0), (6, 10)]],
    "(": [[(4.5, 10), (2, 7), (2, 3), (4.5, 0)]],
    ")": [[(1.5, 10), (4, 7), (4, 3), (1.5, 0)]],
    "#": [[(1.5, 10), (0.8, 0)], [(4.5, 10), (3.8, 0)], [(0, 7), (6, 7)], [(0, 3), (6, 3)]],
    ":": [[(3, 7), (3, 7.8)], [(3, 2.2), (3, 3)]],
}

#
# ANGULAR VARIANT
# --------------
# Same coordinate system, same letterforms, but nothing round is faked with
# small chamfers. Curves become long straight runs instead.
#
# WHY. A gantry draws a long diagonal as one smooth coordinated move. It draws
# a SHORT diagonal by stepping: a little across, a little up, a little across,
# a little up. On a 2.3 mm mark the chamfers that round off an O or an S are
# about a third of a millimetre long, and that is where the stepping shows --
# the letter looks like a staircase rather than a curve. The head also has to
# decelerate into every vertex, so a mark built from 62 short segments marks
# slowly and burns unevenly at the corners.
#
# Every stroke here is at least 3 units long -- 30% of cap height, so 0.9 mm
# even on the smallest mark this tool will place, against 0.42 mm for the
# shortest stroke in the standard font at the same size. A typical mark drops
# from 62 segments to 53. Nothing is drawn that the machine cannot draw
# cleanly, which beats asking the machine to do better.
_A: dict[str, list[list[tuple[float, float]]]] = {
    " ": [],
    "A": [[(0, 0), (3, 10), (6, 0)], [(1.2, 4), (4.8, 4)]],
    "B": [[(0, 0), (0, 10), (3, 10), (6, 7.5), (3, 5), (0, 5)],
          [(3, 5), (6, 2.5), (3, 0), (0, 0)]],
    "C": [[(6, 8), (3, 10), (0, 7), (0, 3), (3, 0), (6, 2)]],
    "D": [[(0, 0), (0, 10), (3, 10), (6, 7), (6, 3), (3, 0), (0, 0)]],
    "E": [[(6, 10), (0, 10), (0, 0), (6, 0)], [(0, 5), (4, 5)]],
    "F": [[(6, 10), (0, 10), (0, 0)], [(0, 5), (4, 5)]],
    "G": [[(6, 8), (3, 10), (0, 7), (0, 3), (3, 0), (6, 2), (6, 5), (3, 5)]],
    "H": [[(0, 0), (0, 10)], [(6, 0), (6, 10)], [(0, 5), (6, 5)]],
    "I": [[(1, 10), (5, 10)], [(3, 10), (3, 0)], [(1, 0), (5, 0)]],
    "J": [[(6, 10), (6, 3), (3, 0), (0, 3)]],
    "K": [[(0, 0), (0, 10)], [(6, 10), (0.5, 4.2)], [(2.2, 5.8), (6, 0)]],
    "L": [[(0, 10), (0, 0), (6, 0)]],
    "M": [[(0, 0), (0, 10), (3, 4.5), (6, 10), (6, 0)]],
    "N": [[(0, 0), (0, 10), (6, 0), (6, 10)]],
    "O": [[(3, 10), (6, 7), (6, 3), (3, 0), (0, 3), (0, 7), (3, 10)]],
    "P": [[(0, 0), (0, 10), (3, 10), (6, 7.5), (3, 5), (0, 5)]],
    "Q": [[(3, 10), (6, 7), (6, 3), (3, 0), (0, 3), (0, 7), (3, 10)],
          [(3.5, 2.5), (6, -0.5)]],
    "R": [[(0, 0), (0, 10), (3, 10), (6, 7.5), (3, 5), (0, 5)], [(3, 5), (6, 0)]],
    "S": [[(6, 9), (3, 10), (0, 9), (0, 6), (3, 5), (6, 4), (6, 1), (3, 0), (0, 1)]],
    "T": [[(0, 10), (6, 10)], [(3, 10), (3, 0)]],
    "U": [[(0, 10), (0, 3), (3, 0), (6, 3), (6, 10)]],
    "V": [[(0, 10), (3, 0), (6, 10)]],
    "W": [[(0, 10), (1.5, 0), (3, 6), (4.5, 0), (6, 10)]],
    "X": [[(0, 0), (6, 10)], [(0, 10), (6, 0)]],
    "Y": [[(0, 10), (3, 5), (6, 10)], [(3, 5), (3, 0)]],
    "Z": [[(0, 10), (6, 10), (0, 0), (6, 0)]],
    "0": [[(3, 10), (6, 7), (6, 3), (3, 0), (0, 3), (0, 7), (3, 10)],
          [(1, 2), (5, 8)]],
    "1": [[(0.5, 8), (3, 10), (3, 0)], [(1, 0), (5, 0)]],
    "2": [[(0, 7), (3, 10), (6, 7), (6, 4), (0, 0), (6, 0)]],
    "3": [[(0, 10), (6, 10), (3, 6)],
          [(3, 6), (6, 4.5), (6, 1.5), (3, 0), (0, 1.5)]],
    "4": [[(4.5, 0), (4.5, 10), (0, 3.5), (6, 3.5)]],
    "5": [[(6, 10), (0, 10), (0, 5.5), (3, 5), (6, 4), (6, 1), (3, 0), (0, 1)]],
    "6": [[(6, 9), (3, 10), (0, 7), (0, 3), (3, 0), (6, 3), (3, 5.5), (0, 4)]],
    "7": [[(0, 10), (6, 10), (2, 0)]],
    "8": [[(3, 5), (0, 7), (3, 10), (6, 7), (3, 5)],
          [(3, 5), (0, 2.5), (3, 0), (6, 2.5), (3, 5)]],
    "9": [[(0, 1), (3, 0), (6, 3), (6, 7), (3, 10), (0, 7), (3, 4.5), (6, 6)]],
    "-": [[(1, 5), (5, 5)]],
    "_": [[(0, 0), (6, 0)]],
    ".": [[(2.6, 0), (3.4, 0)]],
    ",": [[(3.4, 0.6), (2.4, -1.2)]],
    "/": [[(0, 0), (6, 10)]],
    "(": [[(4.5, 10), (2, 7), (2, 3), (4.5, 0)]],
    ")": [[(1.5, 10), (4, 7), (4, 3), (1.5, 0)]],
    "#": [[(1.5, 10), (0.8, 0)], [(4.5, 10), (3.8, 0)], [(0, 7), (6, 7)], [(0, 3), (6, 3)]],
    ":": [[(3, 7), (3, 7.8)], [(3, 2.2), (3, 3)]],
}

# Percent sign, for the thickness labels on the marking card. Straight strokes
# only, so it is the same in both fonts.
_PERCENT = [[(0.2, 0), (5.8, 10)],
            [(0.4, 7), (1.9, 7), (1.9, 9.2), (0.4, 9.2), (0.4, 7)],
            [(4.1, 0.8), (5.6, 0.8), (5.6, 3), (4.1, 3), (4.1, 0.8)]]
_G["%"] = _PERCENT
_A["%"] = _PERCENT

FONTS = {"standard": _G, "angular": _A}

# Filled letters only: a zero with no slash, narrower than the letter O.
#
# Thickened, the slash of a hairline zero joins the ring and splits the
# counter into two slivers, which at a 2-3 mm mark close up and engrave as a
# solid blob. The two slivers also sit diagonally, and some DXF readers work
# out which loop of a fill is a hole by comparing bounding boxes -- two
# overlapping ones can make them fill a counter that should be open. One
# open counter avoids both. The narrower width is what tells 0 from O.
_FILLED = {
    "angular": {
        "0": [[(3, 10), (5.2, 7.4), (5.2, 2.6), (3, 0), (0.8, 2.6),
               (0.8, 7.4), (3, 10)]],
    },
    "standard": {
        "0": [[(2.2, 10), (3.8, 10), (5.4, 8), (5.4, 2), (3.8, 0), (2.2, 0),
               (0.6, 2), (0.6, 8), (2.2, 10)]],
    },
}
_ACTIVE = _A          # angular is the default; see the note above


def set_font(name: str) -> str:
    """Choose which glyph set render() draws with. Returns the name in use."""
    global _ACTIVE
    key = (name or "").strip().lower()
    _ACTIVE = FONTS.get(key, _A)
    return key if key in FONTS else "angular"


def font_name() -> str:
    for k, v in FONTS.items():
        if v is _ACTIVE:
            return k
    return "angular"


GLYPH_HEIGHT = 10.0
GLYPH_ADVANCE = 8.0

# Letter weight, as a fraction of the cap height.
#
#   0     hairline. Each stroke is a single line the laser follows: a LINE MARK.
#         The head starts, stops and turns on every stroke of every letter.
#   > 0   filled. Each stroke is thickened to this weight and the letter is
#         written as a solid shape -- which a laser program ENGRAVES (sweeps
#         back and forth and fills in) instead of following. No starts, stops
#         or corners inside a letter, so nothing to blob.
#
# A thick letter is wider than its centreline by one weight, and two
# neighbours would touch at about 20%, so filled text is also spaced out by
# half a weight per letter. Placement asks text_width() and stroke() for the
# size it is fitting, so a filled mark still lands inside the clear area.
_STROKE = 0.0
MAX_STROKE = 0.25


def set_stroke(fraction) -> float:
    """Set the letter weight (0 = hairline). Returns the value in use."""
    global _STROKE
    try:
        f = float(fraction or 0.0)
    except (TypeError, ValueError):
        f = 0.0
    _STROKE = max(0.0, min(MAX_STROKE, f))
    return _STROKE


def stroke() -> float:
    """Current letter weight as a fraction of cap height (0 = hairline)."""
    return _STROKE


def _glyph(ch: str):
    """Strokes for `ch` in the active font, with the filled-mode variants."""
    if _STROKE > 0:
        alt = _FILLED.get(font_name(), {}).get(ch)
        if alt is not None:
            return alt
    return _ACTIVE.get(ch, [])


def _advance() -> float:
    return GLYPH_ADVANCE + 0.5 * _STROKE * GLYPH_HEIGHT


def available_chars() -> set[str]:
    return set(_ACTIVE.keys())


def sanitize(text: str) -> str:
    """Uppercase and drop anything the font cannot draw."""
    out = []
    for ch in text.upper():
        out.append(ch if ch in _ACTIVE else "-")
    return "".join(out)


def text_width(text: str, height: float) -> float:
    """Total advance width of `text` rendered at cap height `height`."""
    if not text:
        return 0.0
    scale = height / GLYPH_HEIGHT
    adv = _advance()
    return (len(text) * adv - (adv - 6.0)) * scale


def render(
    text: str,
    height: float = 5.0,
    origin: tuple[float, float] = (0.0, 0.0),
    align: str = "bl",
) -> list[list[tuple[float, float]]]:
    """
    Render `text` as a list of open polylines in model coordinates.

    align: two chars -- horizontal (l/c/r) + vertical (b/m/t), e.g. "cm" for
           centred on `origin`.
    """
    text = sanitize(text)
    scale = height / GLYPH_HEIGHT
    strokes: list[list[tuple[float, float]]] = []

    pen_x = 0.0
    adv = _advance()
    for ch in text:
        for glyph_stroke in _glyph(ch):
            strokes.append([((x + pen_x) * scale, y * scale)
                            for x, y in glyph_stroke])
        pen_x += adv

    w = text_width(text, height)
    h = height

    dx = {"l": 0.0, "c": -w / 2.0, "r": -w}[align[0]]
    dy = {"b": 0.0, "m": -h / 2.0, "t": -h}[align[1]]
    ox, oy = origin

    return [[(x + dx + ox, y + dy + oy) for x, y in s] for s in strokes]
