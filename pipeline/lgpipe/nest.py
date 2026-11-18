"""
Sheet nesting for the full cohort.

At 36 groups x 7 blanks = 252 parts, hand-placing on sheets is hours of work
and is where material gets wasted. This is a shelf-based first-fit-decreasing
nester on bounding boxes: parts are sorted tall-first, packed into horizontal
shelves, rotated 90 degrees when that fits better.

Bounding-box nesting leaves more scrap than true contour nesting, but these
parts are mostly rectangular flanged blanks, so the loss is small, and the
result is deterministic and auditable -- which matters more for a course
deliverable than squeezing out the last few percent of steel.

Parts are kept grouped: a group's seven blanks are placed consecutively, so a
group's parts stay together on the same sheet wherever possible and the shop
can hand a coherent set to each team.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# Spacing between nested parts. This is set in the DXF -- the geometry is
# placed at these distances, so the laser simply cuts where the file says.
# Too small and the web between parts overheats and warps; too large and the
# sheet stops fitting. 4 mm is a safe default for 1.52 mm steel on a fibre
# laser; the shop may have their own number.
PART_GAP = 4.0      # mm between neighbouring parts
SHEET_MARGIN = 8.0  # mm clear border around the whole sheet


def set_spacing(part_gap=None, sheet_margin=None):
    """Override the nesting spacing (called from the shop profile)."""
    global PART_GAP, SHEET_MARGIN
    if part_gap is not None:
        PART_GAP = float(part_gap)
    if sheet_margin is not None:
        SHEET_MARGIN = float(sheet_margin)


@dataclass
class NestItem:
    group_id: str
    part_name: str
    prims: list[tuple[str, list[tuple[float, float]]]]
    w: float
    h: float
    x: float = 0.0
    y: float = 0.0
    rotated: bool = False
    sheet: int = -1


@dataclass
class Sheet:
    index: int
    width: float
    height: float
    items: list[NestItem] = field(default_factory=list)
    group_id: str = ""

    def used_area(self) -> float:
        return sum(i.w * i.h for i in self.items)

    def utilisation(self) -> float:
        a = self.width * self.height
        return self.used_area() / a if a else 0.0


def _bbox(prims) -> tuple[float, float, float, float]:
    # A primitive is (layer, points) or (layer, points, fill_key); only the
    # points matter here.
    xs = [p[0] for prim in prims for p in prim[1]]
    ys = [p[1] for prim in prims for p in prim[1]]
    return min(xs), min(ys), max(xs), max(ys)


def make_item(group_id: str, part_name: str, prims) -> NestItem:
    x0, y0, x1, y1 = _bbox(prims)
    # Normalise so every part sits with its bbox corner at the origin.
    moved = [(prim[0], [(x - x0, y - y0) for x, y in prim[1]]) + tuple(prim[2:])
             for prim in prims]
    return NestItem(group_id, part_name, moved, x1 - x0, y1 - y0)


@dataclass
class _Shelf:
    y: float
    height: float
    cursor: float = 0.0


def nest_per_group(items, sheet_w, sheet_h):
    """
    One small sheet per group -- for when each team cuts their own parts.

    Every group gets a self-contained job: their seven blanks, nested tight,
    on their own sheet. The shop loads an offcut, runs one team's file while
    that team watches, unloads, repeats. Costs far more machine time than a
    single batch, but it is the only layout that lets a group genuinely cut
    their own parts, which is what the project brief asks for.
    """
    from collections import defaultdict
    by_group = defaultdict(list)
    for it in items:
        by_group[it.group_id].append(it)

    sheets = []
    for gid in sorted(by_group):
        sub = nest(by_group[gid], sheet_w, sheet_h, mode="compact")
        for s in sub:
            s.index = len(sheets)
            s.group_id = gid
            for it in s.items:
                it.sheet = s.index
            sheets.append(s)
    return sheets




# --------------------------------------------------------------- corner fit --

def _shelf_trial(ordered, width, usable_h):
    """
    Shelf-pack into a virtual strip `width` wide. Pure: returns placements
    rather than moving anything, so a width can be tried and discarded.

    Returns (placements, used_w, used_h) or None if it does not fit inside
    usable_h.
    """
    shelves: list[_Shelf] = []
    out = []
    used_w = 0.0
    for it in ordered:
        if it.w > width:
            return None
        placed = False
        for sh in shelves:
            if sh.height >= it.h and sh.cursor + it.w <= width:
                out.append((it, sh.cursor, sh.y))
                used_w = max(used_w, sh.cursor + it.w)
                sh.cursor += it.w + PART_GAP
                placed = True
                break
        if placed:
            continue
        top = 0.0
        if shelves:
            last = shelves[-1]
            top = last.y + last.height + PART_GAP
        if top + it.h > usable_h:
            return None
        sh = _Shelf(y=top, height=it.h)
        shelves.append(sh)
        out.append((it, 0.0, sh.y))
        used_w = max(used_w, it.w)
        sh.cursor = it.w + PART_GAP
    used_h = shelves[-1].y + shelves[-1].height if shelves else 0.0
    return out, used_w, used_h


def _corner_pack(ordered, usable_w, usable_h):
    """
    Grow the job out of one corner instead of striping the full sheet width.

    WHY
    ---
    Plain shelf packing fills the sheet row by row at full width. With a
    handful of parts that lays one long band across the top: the sheet is
    still mostly steel, but it has a full-width cut across it, and what is
    left is awkward to reuse. Clustering the same parts into a corner leaves
    the remnant as one large rectangle the shop can put back on the rack.

    HOW
    ---
    The packer is run at many trial widths and the one whose occupied
    bounding box has the smallest area wins, with a near-square result
    breaking ties. A small job settles into a square block in the corner; a
    job that fills the sheet converges on the full width on its own, which is
    ordinary compact nesting. No special case, no switch to throw.

    Returns placements, or None when the set will not fit on one sheet at any
    width -- the caller then falls back to the plain shelf nester.
    """
    if not ordered:
        return None
    widest = max(i.w for i in ordered)
    area = sum(i.w * i.h for i in ordered)
    # Start near the width a perfectly square block would need; no point
    # trying narrower than the widest single part.
    lo = max(widest, math.sqrt(area) * 0.7)
    if lo > usable_w:
        return None

    best = None
    STEPS = 48
    for k in range(STEPS + 1):
        w = lo + (usable_w - lo) * k / STEPS
        trial = _shelf_trial(ordered, w, usable_h)
        if trial is None:
            continue
        placements, uw, uh = trial
        box = uw * uh
        squareness = abs(math.log((uw + 1e-9) / (uh + 1e-9)))
        score = (round(box, 1), squareness)
        if best is None or score < best[0]:
            best = (score, placements)
    return best[1] if best else None


def nest(
    items: list[NestItem],
    sheet_w: float,
    sheet_h: float,
    mode: str = "compact",
) -> list[Sheet]:
    """
    First-Fit-Decreasing-Height shelf packing.

    mode="compact"  -- sort every blank by height descending, cohort-wide, and
                       first-fit into ANY open shelf. Parts of similar height
                       share a shelf, so voids are small. A group's seven
                       blanks end up scattered across the sheet, which is only
                       acceptable because every blank carries its own laser-
                       marked group ID. The marking is what pays for the
                       material saving.

    mode="group"     -- keep each group's blanks consecutive so the shop can
                       hand out coherent sets. Costs roughly a full extra
                       sheet at cohort scale. Use only if the marking step is
                       disabled.

    mode="corner"    -- compact, but grown out of one corner as a block rather
                       than striped across the full sheet width, so the
                       leftover steel stays one reusable rectangle. Falls back
                       to plain compact when the job needs more than a sheet.
    """
    if mode == "per-group":
        return nest_per_group(items, sheet_w, sheet_h)

    usable_w = sheet_w - 2 * SHEET_MARGIN
    usable_h = sheet_h - 2 * SHEET_MARGIN

    # Orient every blank landscape (short side vertical) so shelves stay low.
    for it in items:
        if it.h > it.w:
            it.w, it.h = it.h, it.w
            it.rotated = not it.rotated

    if mode == "group":
        ordered = sorted(items, key=lambda i: (i.group_id, -i.h))
    else:
        ordered = sorted(items, key=lambda i: (-i.h, -i.w))

    if mode == "corner":
        placements = _corner_pack(ordered, usable_w, usable_h)
        if placements is not None:
            sheet = Sheet(0, sheet_w, sheet_h)
            for it, x, y in placements:
                it.x = SHEET_MARGIN + x
                it.y = SHEET_MARGIN + y
                it.sheet = 0
                sheet.items.append(it)
            return [sheet]
        # Too big for one sheet -- plain shelf packing from here.

    sheets: list[Sheet] = []
    shelves: list[list[_Shelf]] = []

    def open_sheet() -> int:
        sheets.append(Sheet(len(sheets), sheet_w, sheet_h))
        shelves.append([])
        return len(sheets) - 1

    open_sheet()

    for it in ordered:
        if it.w > usable_w or it.h > usable_h:
            continue  # validation already rejected these

        placed = False
        for si in range(len(sheets)):
            # First fit into an existing shelf.
            for sh in shelves[si]:
                if sh.height >= it.h and sh.cursor + it.w <= usable_w:
                    it.x = SHEET_MARGIN + sh.cursor
                    it.y = SHEET_MARGIN + sh.y
                    it.sheet = si
                    sheets[si].items.append(it)
                    sh.cursor += it.w + PART_GAP
                    placed = True
                    break
            if placed:
                break

            # Otherwise open a new shelf on this sheet.
            used = 0.0
            if shelves[si]:
                last = shelves[si][-1]
                used = last.y + last.height + PART_GAP
            if used + it.h <= usable_h:
                sh = _Shelf(y=used, height=it.h)
                shelves[si].append(sh)
                it.x = SHEET_MARGIN
                it.y = SHEET_MARGIN + sh.y
                it.sheet = si
                sheets[si].items.append(it)
                sh.cursor = it.w + PART_GAP
                placed = True
                break

        if not placed:
            si = open_sheet()
            sh = _Shelf(y=0.0, height=it.h)
            shelves[si].append(sh)
            it.x = SHEET_MARGIN
            it.y = SHEET_MARGIN
            it.sheet = si
            sheets[si].items.append(it)
            sh.cursor = it.w + PART_GAP

    return [s for s in sheets if s.items]


def placed_primitives(item: NestItem):
    """
    Item primitives transformed into sheet coordinates.

    A rotated part is turned 90 degrees CCW and shifted back into the positive
    quadrant as ONE rigid body. The shift is the part's own height before the
    turn -- the same number for every line in the part. (An earlier version
    worked the shift out per line, which slid every hole, bend line and mark
    of a rotated part to its left edge while the outline stayed put.)
    """
    shift = 0.0
    if item.rotated:
        shift = max((y for prim in item.prims for _x, y in prim[1]), default=0.0)
    out = []
    for prim in item.prims:
        pts = prim[1]
        if item.rotated:
            pts = [(shift - y, x) for x, y in pts]
        extra = tuple(prim[2:])
        if extra and extra[0]:
            # A filled mark's key names the part it belongs to. The same part
            # can be placed twice (the stabiliser link is cut x2), so tag the
            # key with this placement -- otherwise both copies' letters would
            # be written as ONE filled object spanning the two parts.
            extra = (f"{extra[0]}@{item.sheet}:{item.x:.3f},{item.y:.3f}",) + extra[1:]
        out.append((prim[0], [(x + item.x, y + item.y) for x, y in pts]) + extra)
    return out
