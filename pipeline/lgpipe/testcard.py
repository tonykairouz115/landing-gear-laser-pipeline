"""
Test card -- find out what this machine's colours actually do.

WHY
---
A colour in a DXF is a label, nothing more. Whether magenta marks, cuts, or is
ignored entirely is decided by the profile loaded on the laser, and the only
honest way to learn that is to fire it at a piece of scrap and look.

Doing that with a real sheet costs the whole cohort's blanks if the mapping is
wrong. This costs one offcut and about a minute.

WHAT IT MAKES
-------------
One small DXF, roughly 150 x 110 mm, carrying a labelled tile per colour:

    +----------------+
    | [] BLACK       |    outline in that colour
    |    ACI 7       |    a circle inside it
    +----------------+    the colour's name marked in the SAME colour

Each tile carries the same three things, so whatever the machine does to that
colour you can see it: if the outline is cut through, that colour is a cut; if
it only browns the surface, it is a mark; if the tile comes back blank, that
colour is switched off.

Every colour the machine lists gets a tile, including the ones we do not plan
to use -- the disabled ones are exactly what the reference layers should be
put on, so it is worth knowing which they are.

    python -m lgpipe.cli --test-card testcard.dxf
"""

from __future__ import annotations

from . import strokefont

# The laser's own colour list, named its way. A tile per row of its settings
# table means the card and the machine's dialog can be read side by side.
CARD_COLOURS = [
    ("BLACK", 7, (0, 0, 0)),
    ("RED", 1, (255, 0, 0)),
    ("GREEN", 3, (0, 255, 0)),
    ("YELLOW", 2, (255, 255, 0)),
    ("BLUE", 5, (0, 0, 255)),
    ("MAGENTA", 6, (255, 0, 255)),
    ("CYAN", 4, (0, 255, 255)),
]

TILE_W = 44.0
TILE_H = 30.0
GAP = 6.0
COLS = 3
MARGIN = 8.0
TEXT_H = 4.0


def _rect(x, y, w, h):
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]


def _circle(cx, cy, r, n=48):
    import math
    return [(cx + r * math.cos(2 * math.pi * i / n),
             cy + r * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]


def build(path: str) -> str:
    """Write the card and return the path."""
    import ezdxf

    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4          # millimetres
    msp = doc.modelspace()

    rows = (len(CARD_COLOURS) + COLS - 1) // COLS
    width = MARGIN * 2 + COLS * TILE_W + (COLS - 1) * GAP
    height = MARGIN * 2 + rows * (TILE_H + GAP) + 14

    for i, (name, aci, rgb) in enumerate(CARD_COLOURS):
        col, row = i % COLS, i // COLS
        x = MARGIN + col * (TILE_W + GAP)
        y = height - MARGIN - 14 - (row + 1) * TILE_H - row * GAP

        layer = f"TEST_{name}"
        if layer not in doc.layers:
            lay = doc.layers.add(layer)
            lay.color = aci
            lay.rgb = rgb
            lay.description = f"test tile -- {name}"

        attr = {"layer": layer, "color": aci, "true_color": ezdxf.rgb2int(rgb)}

        # outline: does this colour cut through?
        msp.add_lwpolyline(_rect(x, y, TILE_W, TILE_H), dxfattribs=attr)
        # a hole: does it cut a clean small feature?
        msp.add_lwpolyline(_circle(x + TILE_W - 9, y + TILE_H / 2, 3.5),
                           dxfattribs=attr)
        # the colour's own name, in that colour: does it mark legibly?
        for stroke in strokefont.render(name, height=TEXT_H,
                                        origin=(x + 4, y + TILE_H / 2 + 2),
                                        align="lb"):
            msp.add_lwpolyline(stroke, dxfattribs=attr)
        for stroke in strokefont.render(f"ACI {aci}", height=3.0,
                                        origin=(x + 4, y + 5), align="lb"):
            msp.add_lwpolyline(stroke, dxfattribs=attr)

    # Title in black, so it prints on any machine that runs black at all.
    if "TEST_TITLE" not in doc.layers:
        t = doc.layers.add("TEST_TITLE")
        t.color = 7
        t.rgb = (0, 0, 0)
    for stroke in strokefont.render("LASER COLOUR TEST CARD", height=5.0,
                                    origin=(MARGIN, height - MARGIN - 8),
                                    align="lb"):
        msp.add_lwpolyline(stroke, dxfattribs={"layer": "TEST_TITLE", "color": 7})

    doc.saveas(path)
    return path


HOW_TO_READ = """\
How to read the card after you cut it

  Tile falls out of the plate      that colour is a THROUGH CUT
  Tile marked but still attached   that colour is a MARK
  Tile is blank                    that colour is DISABLED

Then, on the Settings page:

  Outer profile  -> a colour that cut through
  Holes          -> a colour that cut through (a different one, so cut side
                    can be set separately on the machine)
  Group ID       -> a colour that marked
  Bend lines     -> a colour that marked (may be the same one as the ID)
  Bend extents   -> a colour that came back BLANK
  Sheet frame    -> a colour that came back BLANK

Putting the two reference rows on disabled colours means the bend extent
lines cannot be cut by mistake, whatever anyone does with the template later.
"""
