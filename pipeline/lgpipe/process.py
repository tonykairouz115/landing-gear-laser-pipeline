"""
Per-part processing: normalise layers, inject marking, validate.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field

import ezdxf

from . import layers as L
from . import strokefont
from . import placement
from .geometry import load_part, PartGeometry
from .placement import find_text_box, plan_mark

# Material thickness, in mm. 1.52 = 16 ga steel per the project brief.
# Set from the shop profile; the laser rule of thumb is that a hole should be
# at least as wide as the material is thick, so this drives that check.
MATERIAL_THICKNESS = 1.52
MIN_HOLE_DIA = MATERIAL_THICKNESS
MIN_SEGMENT = 0.05                 # mm; shorter segments are noise


def set_material(thickness: float | None) -> None:
    """Set the material thickness the validator checks against."""
    global MATERIAL_THICKNESS, MIN_HOLE_DIA
    if thickness:
        MATERIAL_THICKNESS = float(thickness)
        MIN_HOLE_DIA = MATERIAL_THICKNESS


@dataclass
class PartResult:
    part_name: str
    group_id: str
    source: str
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    width: float = 0.0
    height: float = 0.0
    n_holes: int = 0
    n_bends: int = 0
    cut_length: float = 0.0
    mark_placed: bool = False
    mark_height: float = 0.0
    mark_shown: str = ""
    notes: list[str] = field(default_factory=list)
    deviation: dict = field(default_factory=dict)
    geo: PartGeometry | None = None
    _counts: dict = field(default_factory=dict, repr=False)

    def note(self, msg: str) -> None:
        """
        Record an observation for the marking team.

        Deliberately separate from warnings. A warning is about the CUT --
        something the shop may need to act on. A note is about the STUDENT'S
        MODEL, and requires no action at all: the part is cut exactly as
        submitted regardless. Mixing the two would bury the shop's actual
        to-do list under a full cohort' worth of assessment detail.
        """
        if msg not in self.notes:
            self.notes.append(msg)

    def flag(self, msg: str, fatal: bool = False) -> None:
        """
        Record a problem, collapsing repeats.

        A chassis with twelve unnamed open chains produces the same warning
        twelve times. Repeating it buries the one warning that matters in a
        report the shop actually has to read, so identical messages are
        counted instead.
        """
        bucket = self.errors if fatal else self.warnings
        if fatal:
            self.ok = False
        n = self._counts.get(msg, 0) + 1
        self._counts[msg] = n
        if n == 1:
            bucket.append(msg)
        else:
            for i, existing in enumerate(bucket):
                if existing == msg or existing.startswith(msg + "  (x"):
                    bucket[i] = f"{msg}  (x{n})"
                    break


def _est_diameter(pts: list[tuple[float, float]]) -> float:
    """Equivalent diameter of a closed loop, from its area."""
    n = len(pts)
    a = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    area = abs(a / 2.0)
    return 2.0 * math.sqrt(area / math.pi) if area > 0 else 0.0


def validate(geo: PartGeometry, res: PartResult, sheet: tuple[float, float]) -> None:
    outer = geo.outer
    if outer is None:
        res.flag("No outer profile found -- part cannot be cut.", fatal=True)
        return
    if not outer.closed:
        res.flag("Outer profile is not a closed loop.", fatal=True)

    x0, y0, x1, y1 = outer.bbox()
    res.width, res.height = x1 - x0, y1 - y0

    if res.width <= 0 or res.height <= 0:
        res.flag("Outer profile has zero extent.", fatal=True)
        return

    sw, sh = sheet
    fits = (res.width <= sw and res.height <= sh) or (res.height <= sw and res.width <= sh)
    if not fits:
        res.flag(
            f"Part is {res.width:.1f} x {res.height:.1f} mm and does not fit the "
            f"{sw:.0f} x {sh:.0f} mm stock sheet.",
            fatal=True,
        )

    holes = geo.by_role("CUT_INNER")
    res.n_holes = len(holes)
    for h in holes:
        d = _est_diameter(h.points)
        if 0 < d < MIN_HOLE_DIA:
            res.flag(
                f"Interior feature is only ~{d:.2f} mm across; below the "
                f"{MIN_HOLE_DIA:.2f} mm minimum for {MATERIAL_THICKNESS} mm steel. "
                f"The laser may not clear it."
            )

    bends = geo.by_role("MARK_BEND")
    res.n_bends = len(bends)

    res.cut_length = sum(
        c.length for c in geo.chains if c.role in ("CUT_OUTER", "CUT_INNER")
    )

    tiny = [c for c in geo.chains if c.length < MIN_SEGMENT and c.role.startswith("CUT")]
    if tiny:
        res.flag(f"{len(tiny)} near-zero-length cut segment(s) found -- likely "
                 f"duplicate or degenerate geometry from the CAD export.")


def kerf_offset(pts: list[tuple[float, float]], role: str, kerf: float):
    """
    Move a cut path so the beam's width falls in the scrap, not the part.

    A laser removes a slot of material (the kerf) centred on the path it
    follows. Left uncompensated, half that slot is taken out of the part:
    outer profiles finish kerf/2 undersize and holes finish kerf/2 oversize.

    So the beam path moves OUTWARD on an outer profile and INWARD on a hole.
    Either way the finished edge lands on the nominal line.

    NOTE: this is normally the machine's job, not the file's -- the correct
    offset depends on the measured kerf of that machine at that power, speed
    and thickness. Pre-compensating here is for shops that cannot set it
    machine-side, and it is dangerous to do both: compensating twice puts the
    part a full kerf out.
    """
    if kerf <= 0 or len(pts) < 3:
        return pts
    try:
        from shapely.geometry import Polygon
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
        d = kerf / 2.0 if role == "CUT_OUTER" else -kerf / 2.0
        # Mitred joins keep sharp corners sharp; rounded joins would put a
        # radius on every corner of the blank.
        out = poly.buffer(d, join_style=2, mitre_limit=4.0)
        if out.is_empty:
            return None                      # hole smaller than the kerf
        if out.geom_type == "MultiPolygon":
            out = max(out.geoms, key=lambda g: g.area)
        ring = list(out.exterior.coords)
        return ring
    except Exception:
        return pts


def part_primitives(
    geo: PartGeometry,
    group_id: str,
    part_name: str,
    res: PartResult,
    logo_strokes: list[list[tuple[float, float]]] | None = None,
    mark_text: str | None = None,
    kerf: float = 0.0,
) -> list[tuple[str, list[tuple[float, float]]]]:
    """
    Flatten a part to (layer_name, points) primitives, marking included.

    Both the per-part DXF writer and the nester consume this, so a part is
    guaranteed to look identical whether it is exported alone or nested --
    there is only one code path that decides what gets marked where.
    """
    prims: list[tuple[str, list[tuple[float, float]]]] = []

    for c in geo.chains:
        spec = L.SCHEME.get(c.role)
        if spec is None:
            continue
        pts = list(c.points)

        if kerf > 0 and c.closed and c.role in ("CUT_OUTER", "CUT_INNER"):
            adjusted = kerf_offset(pts, c.role, kerf)
            if adjusted is None:
                res.flag(
                    f"An interior feature on '{part_name}' is narrower than the "
                    f"{kerf:.2f} mm kerf and vanishes under compensation -- the "
                    f"laser cannot produce it.",
                    fatal=True,
                )
                continue
            pts = adjusted
        elif c.closed:
            pts = pts + [pts[0]]

        if c.role in L.FILLABLE and not c.closed:
            style, stroke = L.style_of(c.role)
            if style == "filled" and c.role == "MARK_BEND":
                # A filled bend line: a band `stroke` mm wide, engraved.
                from .engrave import fill_rings
                key = f"{group_id}/{part_name}/bends"
                for ring in fill_rings([pts], stroke, flat_ends=True):
                    prims.append((spec.name, ring, key))
                continue
        prims.append((spec.name, pts))

    outer = geo.outer
    if outer is None:
        return prims

    label = mark_text if mark_text is not None else f"{group_id} {part_name}"

    holes = [c.points for c in geo.by_role("CUT_INNER")]
    bends = [c.points for c in geo.by_role("MARK_BEND")]
    cands = label_candidates(label)

    # Every blank must carry its group ID -- an unmarked part cannot be sorted
    # back to its team. So rather than give up when the preferred clearances
    # leave no room, work down a ladder: first try normal clearances at a
    # comfortable size, then progressively tighten the clearances (never past
    # the hard limits, which are set by the bend zone and heat), and only then
    # accept a smaller mark. Whatever it takes is recorded so a marginal part
    # can be eyeballed at the pilot instead of discovered on the bench.
    LADDER = [
        (1.00, placement.MIN_HEIGHT, ""),
        (0.75, placement.MIN_HEIGHT, "tighter clearances"),
        (0.50, placement.MIN_HEIGHT, "minimum clearances"),
        (0.50, placement.ABS_MIN_HEIGHT, "minimum clearances, reduced size"),
    ]

    # Keep the BEST rung, not the last one that fits. A later rung gives up
    # clearance (or size floor), so it only wins if it buys a clearly bigger
    # mark -- at least 10% -- otherwise the roomier earlier rung stands, and
    # the note in the report is the note of the rung actually used.
    chosen = None
    for relax, min_h, note in LADDER:
        box = find_text_box(outer.points, holes, bends, relax=relax)
        if box is None:
            continue
        lines, fitted = plan_mark(box, cands, min_height=min_h)
        if fitted is None:
            continue
        if chosen is None or fitted[2] > chosen[1][2] * 1.10:
            chosen = (lines, fitted, note)
        if chosen[1][2] >= placement.GOOD_HEIGHT and not chosen[2]:
            break

    if chosen is None:
        res.flag(
            f"'{part_name}' has no markable area at all, even at minimum "
            f"clearances -- it will be cut unmarked. Check the part for a "
            f"clear face of at least ~3 x 5 mm.",
            fatal=False,
        )
        return prims

    lines, fitted, note = chosen
    cx, cy, height, rotated = fitted
    res.mark_height = height
    res.mark_shown = " ".join(lines)

    detail = []
    if lines != [label]:
        detail.append(f"stacked as {' / '.join(lines)}")
    if note:
        detail.append(note)
    if height < placement.MIN_HEIGHT:
        detail.append("below the preferred size -- verify legibility at the pilot")
    if detail:
        res.flag(
            f"Mark on '{part_name}' at {height:.2f} mm ({'; '.join(detail)})."
        )

    strokes = _render_block(lines, height)

    if rotated:
        strokes = [[(-y, x) for x, y in s] for s in strokes]
    strokes = [[(x + cx, y + cy) for x, y in s] for s in strokes]

    weight = strokefont.stroke() * height
    if weight > 0:
        # Filled letters for engraving: one solid object per mark, no outline.
        from .engrave import fill_rings
        key = f"{group_id}/{part_name}"
        for ring in fill_rings(strokes, weight):
            prims.append((L.SCHEME["MARK_TEXT"].name, ring, key))
    else:
        for s in strokes:
            prims.append((L.SCHEME["MARK_TEXT"].name, s))

    if logo_strokes:
        lh = min(height * 0.9, 3.0)
        logo_prims = _logo_prims(logo_strokes, box, cx, cy, height, lh)
        if logo_prims:
            prims.extend(logo_prims)
        else:
            res.flag(f"Logo omitted from '{part_name}' -- insufficient room.")

    res.mark_placed = True
    return prims


def _logo_prims(logo_strokes, box, cx, cy, text_h, logo_h):
    """Course logo placed just under the ID text, if there is room."""
    xs = [p[0] for s in logo_strokes for p in s]
    ys = [p[1] for s in logo_strokes for p in s]
    if not xs:
        return []
    lw = max(xs) - min(xs)
    lh = max(ys) - min(ys)
    if lh <= 0:
        return []

    scale = logo_h / lh
    y_top = cy - text_h / 2.0 - 1.0
    if y_top - logo_h < box[1]:
        return []
    if lw * scale > (box[2] - box[0]):
        return []

    ox = cx - (min(xs) + lw / 2.0) * scale
    oy = y_top - logo_h - min(ys) * scale

    return [
        (L.SCHEME["MARK_TEXT"].name,
         [(x * scale + ox, y * scale + oy) for x, y in s])
        for s in logo_strokes
    ]


def write_dxf(prims, path: str) -> None:
    """
    Write primitives to a normalised DXF at `path`.

    Every entity carries its colour TWICE OVER, on the entity itself:
    the AutoCAD colour index (group 62) and the true RGB (group 420). It also
    sits on a correctly-coloured layer.

    That looks redundant and it is not. The obvious way to write this file is
    to colour the layers and leave every entity BYLAYER, which is what a
    draughtsman would do -- but then the colour exists only in the layer table,
    and the importer has to resolve it. CorelDRAW, which is what drives a lot of
    laser shops, resolves BYLAYER poorly: the geometry arrives in a default
    outline colour, the colour-to-power mapping matches nothing, and someone
    stands at the machine reselecting objects by hand to recolour them.

    Writing the colour onto the entity removes the lookup. Whatever opens the
    file -- CorelDRAW, LightBurn, a print driver, AutoCAD -- reads the colour
    off the object in front of it.
    """
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4  # millimetres
    L.ensure_layers(doc)
    by_name = {spec.name: spec for spec in L.SCHEME.values()}
    msp = doc.modelspace()

    def attribs_for(layer_name):
        attribs = {"layer": layer_name}
        spec = by_name.get(layer_name)
        if spec is None:
            attribs["color"] = ezdxf.const.BYLAYER
        else:
            attribs["color"] = spec.aci
            attribs["true_color"] = ezdxf.rgb2int(spec.rgb)
        return attribs

    # Filled regions (engraved group IDs) are gathered by key: every ring of
    # one mark becomes one HATCH, in the order the marks were met.
    fills: dict = {}
    for prim in prims:
        layer_name, pts = prim[0], prim[1]
        if len(prim) > 2 and prim[2]:
            fills.setdefault((layer_name, prim[2]), []).append(pts)
            continue
        msp.add_lwpolyline(pts, dxfattribs=attribs_for(layer_name))
    for (layer_name, _key), rings in fills.items():
        add_fill(msp, rings, attribs_for(layer_name))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    doc.saveas(path)


def add_fill(msp, rings, attribs) -> None:
    """
    One solid-filled HATCH from closed rings -- letter outlines and their
    counters -- filled odd-even, so the hole in an 0 or an 8 stays open.

    Deliberately NO outline entity goes with it. A boundary line in the mark
    colour would be followed by the laser as well as engraved, which puts back
    exactly the starts and stops that filling the letters removes.
    """
    hatch = msp.add_hatch(color=attribs.get("color", 7), dxfattribs=attribs)
    hatch.dxf.hatch_style = 0          # normal: nested rings alternate
    rgb = attribs.get("true_color")
    if rgb is not None:
        hatch.set_solid_fill(color=attribs.get("color", 7), style=0,
                             rgb=ezdxf.colors.int2rgb(rgb))
    # Outer boundaries are flagged external + outermost and counters are plain
    # loops, the way AutoCAD writes a hatch with islands. A counter ring runs
    # the opposite way round to its letter (shapely's convention), so a reader
    # that decides holes by winding gets the same answer.
    outer_flag = (ezdxf.const.BOUNDARY_PATH_EXTERNAL
                  | ezdxf.const.BOUNDARY_PATH_OUTERMOST)
    for ring in rings:
        pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
        flag = outer_flag if _signed_area(pts) > 0 else 0
        hatch.paths.add_polyline_path(pts, is_closed=True, flags=flag)


def _signed_area(pts) -> float:
    """Shoelace area: positive counter-clockwise (an outline), negative for a
    counter-clockwise-reversed ring (a hole)."""
    a = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return a / 2.0


def _balanced_splits(tokens: list[str], seps: list[str]) -> list[list[str]]:
    """
    Every way of laying `tokens` out on 1..n lines, keeping order and losing
    nothing, most-compact-first.

    A mark now carries four fields -- department, lab section, group, part
    code -- and `MAAE-L1-G07 CHS` is far too long for one line on a nut clip.
    Rather than hard-code a couple of stacked forms, generate all the ordered
    groupings and let the fitter pick whichever reads largest in the clear
    area that particular blank happens to have.

    Splits are chosen to be balanced in width, so lines come out a similar
    length instead of one long line above one short one.
    """
    from .strokefont import text_width

    n = len(tokens)
    if n <= 1:
        return [list(tokens)]

    def join(chunk_idx: list[int]) -> str:
        out = tokens[chunk_idx[0]]
        for k in chunk_idx[1:]:
            out += seps[k - 1] + tokens[k]
        return out

    results = []
    for n_lines in range(1, n + 1):
        # Best balanced partition of n tokens into n_lines contiguous chunks.
        best, best_score = None, None
        def walk(start, remaining, acc):
            nonlocal best, best_score
            if remaining == 1:
                chunks = acc + [list(range(start, n))]
                widths = [text_width(join(c), 1.0) for c in chunks]
                score = max(widths) - min(widths)      # lower = more even
                if best_score is None or score < best_score:
                    best, best_score = chunks, score
                return
            for end in range(start + 1, n - remaining + 2):
                walk(end, remaining - 1, acc + [list(range(start, end))])
        walk(0, n_lines, [])
        if best:
            results.append([join(c) for c in best])
    return results


def label_candidates(label: str) -> list[list[str]]:
    """
    Ways to write `label`, most compact first.

    The label is a run of fields separated by "-" and " " -- for example
    `MAAE-L1-G07 CHS`: department, lab section, group, part code. Every
    candidate keeps all four. None of them is ever dropped to save space,
    because a mark that has lost a field is a mark that can send a blank to
    the wrong bench, and a smaller mark is only a nuisance by comparison.
    """
    def split(text):
        toks, seps, cur = [], [], ""
        for ch in text:
            if ch in "- ":
                toks.append(cur); seps.append(ch); cur = ""
            else:
                cur += ch
        toks.append(cur)
        return [t for t in toks if t], seps

    tokens, seps = split(label)
    if len(tokens) <= 1:
        return [[label]]

    out = _balanced_splits(tokens, seps)

    # Last resort for a very cramped blank: shorten the department to its
    # initial. With only MAAE and AERO in the course, M and A are a lossless
    # encoding -- distinct first letters, and every student knows which one
    # they are. This is tried only after every full-text layout, so a part
    # with room keeps the department spelled out.
    if tokens and tokens[0].upper() in DEPT_ABBREV:
        short = [DEPT_ABBREV[tokens[0].upper()]] + tokens[1:]
        out.extend(_balanced_splits(short, seps))

    # Absolute last resort: compress the whole ID into one token, M1-07.
    #
    # Only the cramped blanks ever reach this rung -- the rivet nut, mostly,
    # which is small and mostly hole. Everything with room keeps the ID spelled
    # out, so this is not a decision about how the batch is marked; it is what
    # happens instead of a nut wearing three stacked lines at 2.3 mm. On the
    # nut it buys 2.3 -> 3.4 mm and drops the mark from 14 strokes to 12, which
    # is the difference between readable and squinting.
    #
    # Still lossless: M and A are the only departments and the section is a
    # single digit. report.md prints the key whenever any part used this.
    if len(tokens) >= 3 and tokens[0].upper() in DEPT_ABBREV:
        m = re.match(r"^L(\d)$", tokens[1], re.IGNORECASE)
        g = re.match(r"^G(\d+)$", tokens[2], re.IGNORECASE)
        if m and g:
            packed = "%s%s-%02d" % (DEPT_ABBREV[tokens[0].upper()],
                                    m.group(1), int(g.group(1)))
            rest = [packed] + tokens[3:]
            out.extend(_balanced_splits(rest, seps[2:] or [" "]))
    return out


def _render_block(lines: list[str], height: float):
    """Render centred lines as stroke polylines about the origin."""
    n = len(lines)
    step = height * (1.0 + placement.LINE_GAP)
    total = height * n + placement.LINE_GAP * height * (n - 1)
    top = total / 2.0
    strokes = []
    for i, text in enumerate(lines):
        cy = top - height / 2.0 - i * step
        strokes.extend(
            strokefont.render(text, height=height, origin=(0, cy), align="cm")
        )
    return strokes


# Canonical three-letter codes for the six parts.
#
# The code is looked up from the student's component name, never copied from
# it. That matters: a student who names their component "Chassis final v3" or
# "Component1 (2)" would otherwise get that string burned into steel. Marking
# a canonical code decouples students' naming habits from the physical
# output, while still telling someone holding the blank what it is meant to be
# -- which is the whole point when parts are collected from a communal bin and
# a badly modelled part may not be recognisable on sight.
DEPT_ABBREV = {"MAAE": "M", "AERO": "A"}

PART_CODES = {
    # Full component names...
    "chassie": "CHS", "chassis": "CHS",
    "left cover": "LCV", "leftcover": "LCV", "left_cover": "LCV",
    "right cover": "RCV", "rightcover": "RCV", "right_cover": "RCV",
    "nut": "NUT",
    "leg": "LEG",
    "stabalizer": "STB", "stabilizer": "STB", "stabaliser": "STB",
    "stabiliser": "STB",
    # ...and the codes themselves, because naming the exported files
    # CHS.dxf / LCV.dxf is shorter and far harder for a student to
    # misspell than "Right Cover.dxf".
    "chs": "CHS", "lcv": "LCV", "rcv": "RCV",
    "stb": "STB",
}


def part_code(part_name: str, res: "PartResult | None" = None) -> str:
    """Canonical 3-letter code for a part, from its component name."""
    key = part_name.strip().lower()
    code = PART_CODES.get(key)
    if code:
        return code
    # Unrecognised name: fall back to initials of each word, or the first
    # three characters of a single word, so the blank still says something.
    words = key.split()
    if len(words) > 1:
        code = "".join(w[0] for w in words[:3]).upper()
    else:
        code = key[:3].upper()
    code = (code or "UNK")[:3]
    if res is not None:
        res.flag(
            f"Component name '{part_name}' is not one of the six expected "
            f"parts; marking it '{code}'. Check the naming."
        )
    return code


def mark_label(group_id: str, part_name: str, style: str) -> str:
    """
    The string laser-marked onto a blank.

    "group" is the default because the group ID is the only thing the mark has
    to carry: it is what stops group 7's chassis being handed to group 12.
    Which part it is, is obvious from looking at it. Dropping the part name
    frees the whole safe zone for the ID, so small blanks get a mark several
    times larger -- the stabiliser link goes from a squinting 1.6 mm to
    comfortably readable.
    """
    if style in ("short", "short-code"):
        gid = short_group_id(group_id)
        if style == "short":
            return gid
        return f"{gid} {part_code(part_name)}"
    if style == "group-code":
        return f"{group_id} {part_code(part_name)}"
    if style == "group-part":
        return f"{group_id} {part_name}"
    return group_id


_SHORT_RE = re.compile(r"^(MAAE|AERO)-L(\d)-G(\d+)$", re.IGNORECASE)


def short_group_id(group_id: str) -> str:
    """
    MAAE-L1-G07 -> M1-07.  AERO-L4-G12 -> A4-12.

    WHY BOTHER. The cost of a mark is not its ink, it is its STOPS. The head
    decelerates into every corner and lifts between every stroke, and on a
    small blank those stops are what turn the text into a smudge. Going from
    fifteen characters to nine cuts a mark from 21 strokes to 12 -- and because
    the text is shorter, it also fits into the same clear area at 3.9 mm
    instead of 2.33 mm. Bigger letters and half the stops, from the same area
    of steel.

    Nothing is lost: M and A are the only two departments and the section is a
    single digit, so the short form is reversible by anyone holding the key.
    Anything that does not fit that shape is returned UNCHANGED rather than
    guessed at -- a mark that cannot be traced back to a group is worse than a
    long one.
    """
    m = _SHORT_RE.match((group_id or "").strip())
    if not m:
        return group_id
    dept, section, num = m.group(1).upper(), m.group(2), m.group(3)
    return f"{dept[0]}{section}-{int(num):02d}"


def process_part(
    dxf_path: str,
    group_id: str,
    part_name: str,
    sheet: tuple[float, float],
    logo_strokes=None,
    mark_style: str = "group",
    kerf: float = 0.0,
):
    """Returns (PartResult, primitives) -- primitives is None if the part failed."""
    res = PartResult(part_name=part_name, group_id=group_id, source=dxf_path)
    try:
        geo = load_part(dxf_path, L.hint_for)
    except Exception as e:
        res.flag(f"Could not read DXF: {e}", fatal=True)
        return res, None

    res.geo = geo
    for w in geo.warnings:
        res.flag(w)

    validate(geo, res, sheet)
    if not res.ok:
        return res, None

    prims = part_primitives(
        geo, group_id, part_name, res, logo_strokes,
        mark_text=mark_label(group_id, part_name, mark_style),
        kerf=kerf,
    )
    if not res.ok:
        return res, None
    return res, prims
