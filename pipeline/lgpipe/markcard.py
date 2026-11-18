"""
Marking card: the same group ID marked every way this tool can mark it.

WHY THIS EXISTS
---------------
"The text does not mark well" has several possible causes and they need
different fixes:

  * the mark is too small for the machine to hold a line
  * the letterforms ask the gantry for strokes shorter than it can draw
  * the laser is FOLLOWING the letters (a line mark: a start, a stop and a
    corner on every stroke, which is where steel blobs) when it could be
    ENGRAVING them (sweeping across solid letters, already at speed)
  * the text is too long, so it had to be shrunk to fit the part

Arguing about which one it is from memory does not work. This card puts them
side by side on one piece of scrap, every line labelled:

    left column    LINE MARK   hairline letters the laser follows
    right column   ENGRAVE     the same letters filled solid, for engraving
    each column    both letter shapes, six sizes, and the short form
    bottom         how thick a filled letter should be, at 2.5 mm

Cut it once and the answer is the column, block and row where the letters
stop being readable.

Everything marked is on the MARK_TEXT layer and colour, so it runs at exactly
the settings the real group IDs will. The filled letters are solid HATCH
objects with no outline -- the same thing the pipeline writes in "filled"
mode. The frame is on CUT_OUTER so the card comes free of the sheet.
"""

from __future__ import annotations

from . import layers as L
from . import strokefont

SAMPLE = "MAAE-L1-G07 CHS"
SHORT = "M1-07 CHS"
HEIGHTS = (1.6, 2.0, 2.5, 3.0, 4.0, 5.0)
FILLED_PCT = 13                      # right column, unless the Group ID row sets one
WEIGHTS = (10, 13, 16, 20)           # the thickness ladder, % of height
# The ladder is drawn at the size the smallest part really gets. The real
# nut clip (29.7 x 16 mm, two bends, three holes) comes out at about 1.5 mm:
# that is the size where thickness decides whether it reads.
LADDER_H = 1.6
MARGIN = 8.0
ROW_GAP = 4.0
LABEL_W = 16.0
COL_GAP = 12.0
HEAD_H = 3.2
LABEL_H = 2.6

HOW_TO_READ = """\
How to read the marking card
  LEFT column, LINE MARK: hairline letters. The laser follows every stroke --
  a start, a stop and a corner on each one.
  RIGHT column, ENGRAVE: the same letters filled solid (at the Group ID
  row's stroke %, or 13% if that row is a hairline). The laser should
  sweep across them instead (its engraving settings). If they come into
  CorelDRAW as empty outlines, or not at all, the fill did not import -- say so
  before cutting.

  In each column: two letter shapes (ANGULAR, STANDARD) at six sizes, then the
  short form M1-07. Find the smallest line you can read at arm's length
  without squinting. The column it is in is how to mark (Settings, Laser
  colours, Group ID row: hairline or filled); the block is the letter shape
  (Settings: Letter shapes).

  BOTTOM: filled letters at 1.6 mm -- about what the nut clip gets -- at four
  thicknesses. Too thin and the letters look faint; too thick and the holes
  in 0, 8, A, B close up. The best one goes in the Group ID row's stroke box
  on the Settings page (Laser colours), with that row set to filled.

  The 1.6 lines are the ones that matter for the nut clip. Everything else
  gets 3 mm or more.
"""


def _plan():
    """Rows of one column: ("head", text) or ("line", font, text, height)."""
    rows: list[tuple] = [("head", "ANGULAR")]
    rows += [("line", "angular", SAMPLE, h) for h in reversed(HEIGHTS)]
    rows += [("head", "STANDARD")]
    rows += [("line", "standard", SAMPLE, h) for h in reversed(HEIGHTS)]
    # The short form's whole point is that it fits BIGGER in the same space,
    # so it is drawn at the size it would actually get -- scaled up by how
    # much narrower it is -- not at the same size as the full ID.
    bigger = len(SAMPLE) / float(len(SHORT))
    rows += [("head", "SHORT FORM")]
    rows += [("line", "angular", SHORT, round(h * bigger, 1)) for h in (2.0, 1.6)]
    return rows


def _row_height(item) -> float:
    if item[0] == "head":
        return HEAD_H + 2.0 + ROW_GAP
    return max(item[3], LABEL_H) + ROW_GAP


def build(path: str) -> str:
    """Write the card and return the path."""
    import ezdxf
    from .engrave import fill_rings
    from .process import add_fill

    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4          # millimetres
    L.ensure_layers(doc)
    msp = doc.modelspace()

    mark = L.SCHEME["MARK_TEXT"]
    cut = L.SCHEME["CUT_OUTER"]
    m_attr = {"layer": mark.name, "color": mark.aci,
              "true_color": ezdxf.rgb2int(mark.rgb)}
    c_attr = {"layer": cut.name, "color": cut.aci,
              "true_color": ezdxf.rgb2int(cut.rgb)}

    keep_font = strokefont.font_name()
    keep_stroke = strokefont.stroke()

    def draw(text, font, height, origin, pct):
        """Mark `text` hairline (pct 0) or filled at pct % weight."""
        strokefont.set_font(font)
        strokefont.set_stroke(pct / 100.0)
        strokes = strokefont.render(text, height=height, origin=origin,
                                    align="lb")
        if pct > 0:
            # One solid object per line of text, no outline: exactly what the
            # pipeline writes for a filled group ID.
            add_fill(msp, fill_rings(strokes, height * pct / 100.0), m_attr)
        else:
            for s in strokes:
                msp.add_lwpolyline(s, dxfattribs=m_attr)

    def width_of(text, font, height, pct):
        strokefont.set_font(font)
        strokefont.set_stroke(pct / 100.0)
        return strokefont.text_width(text, height) + height * pct / 100.0

    rows = _plan()
    style, stroke = L.style_of("MARK_TEXT")
    right = stroke if style == "filled" else FILLED_PCT
    columns = (("LINE MARK", 0), ("ENGRAVE %g%%" % right, right))

    col_w = []
    for title, pct in columns:
        w = width_of(title, "angular", HEAD_H, pct)
        for item in rows:
            if item[0] == "head":
                w = max(w, width_of(item[1], "angular", HEAD_H, pct))
            else:
                _k, font, text, h = item
                w = max(w, LABEL_W + width_of(text, font, h, pct))
        col_w.append(w)

    ladder_title = "THICKNESS, FILLED, AT %.1f MM" % LADDER_H
    ladder_w = max(width_of(ladder_title, "angular", HEAD_H, right),
                   max(LABEL_W + width_of(SAMPLE, "angular", LADDER_H, p)
                       for p in WEIGHTS))

    col_h = (HEAD_H + 2.0 + ROW_GAP) + sum(_row_height(r) for r in rows)
    ladder_h = (HEAD_H + 2.0 + ROW_GAP) + len(WEIGHTS) * (LABEL_H + ROW_GAP)

    width = MARGIN * 2 + max(col_w[0] + COL_GAP + col_w[1], ladder_w)
    height = MARGIN * 2 + col_h + ROW_GAP + ladder_h

    top = height - MARGIN
    x = MARGIN
    for (title, pct), cw in zip(columns, col_w):
        y = top - HEAD_H - 2.0
        draw(title, "angular", HEAD_H, (x, y), pct)
        y -= ROW_GAP
        for item in rows:
            if item[0] == "head":
                y -= HEAD_H + 2.0
                draw(item[1], "angular", HEAD_H, (x, y), pct)
                y -= ROW_GAP
                continue
            _k, font, text, h = item
            y -= max(h, LABEL_H)
            draw(text, font, h, (x + LABEL_W, y), pct)
            # The size label is always big enough to read, so a line that
            # fails to mark can still be identified by the number beside it.
            draw("%.1f" % h, "angular", LABEL_H, (x, y), pct)
            y -= ROW_GAP
        x += cw + COL_GAP

    # The thickness ladder, full width under both columns.
    y = top - col_h - ROW_GAP - HEAD_H - 2.0
    draw(ladder_title, "angular", HEAD_H, (MARGIN, y), right)
    y -= ROW_GAP
    for p in WEIGHTS:
        y -= LABEL_H
        draw(SAMPLE, "angular", LADDER_H, (MARGIN + LABEL_W, y), p)
        draw("%d%%" % p, "angular", LABEL_H, (MARGIN, y), right)
        y -= ROW_GAP

    msp.add_lwpolyline([(0, 0), (width, 0), (width, height), (0, height), (0, 0)],
                       dxfattribs=c_attr)

    strokefont.set_font(keep_font)
    strokefont.set_stroke(keep_stroke)
    doc.saveas(path)
    return path


def size_mm(path: str) -> tuple[float, float]:
    """Width and height of a card already written, from its frame."""
    import ezdxf
    msp = ezdxf.readfile(path).modelspace()
    cut = L.SCHEME["CUT_OUTER"].name
    for e in msp.query("LWPOLYLINE"):
        if e.dxf.layer == cut:
            xs = [p[0] for p in e.get_points()]
            ys = [p[1] for p in e.get_points()]
            return max(xs) - min(xs), max(ys) - min(ys)
    return 0.0, 0.0
