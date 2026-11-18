"""
Automatic safe-zone placement for the group ID mark.

THE PROBLEM
-----------
Each of the a full cohort models the parts independently from their own physical
measurements, so no two chassis are dimensionally identical. Hard-coding "put
the text at X=40, Y=12" would land it on a hole in some designs and across a
bend line in others. A mark that straddles a bend is unreadable after forming,
and a mark that overlaps a hole gets cut away.

THE APPROACH
------------
Compute the region of the blank that is genuinely safe to mark:

    safe = outer_profile
           eroded by EDGE_MARGIN            (stay off the cut edge)
           minus holes grown by HOLE_CLEAR  (stay off interior cuts)
           minus bend lines grown by BEND_CLEAR  (stay on ONE flat face)

then find the largest empty axis-aligned rectangle inside it and fit the text
to that rectangle. This adapts to whatever the student actually modelled.

Subtracting the bend lines is the important part: it automatically confines the
text to a single flange, so it stays flat and readable after forming.
"""

from __future__ import annotations

import numpy as np
from shapely.geometry import Polygon, LineString, MultiPolygon
from shapely.ops import unary_union

# Clearances in mm, as absolute ceilings.
EDGE_MARGIN = 2.5     # from any cut edge
HOLE_CLEAR = 2.0      # from any interior cut
BEND_CLEAR = 3.0      # from a bend centre line
GRID = 0.5            # rasterisation cell size

# On a small blank, fixed clearances eat the whole part: 2.5 mm off each edge
# of a 16 mm-tall nut clip removes a third of it before holes are considered.
# So each clearance is also capped at a fraction of the blank's SHORT side,
# which leaves large parts untouched and gives small ones a fighting chance.
EDGE_FRAC = 0.12
HOLE_FRAC = 0.10
BEND_FRAC = 0.15


# Hard limits when clearances are relaxed to force a mark onto a cramped part.
# Below these the mark is either inside the bend deformation zone or close
# enough to a cut edge that heat affects it.
HARD_EDGE = 1.0
HARD_HOLE = 0.8
HARD_BEND = 1.8       # bend radius is ~1.5 mm; stay outside the formed zone


def clearances(short_side: float, relax: float = 1.0) -> tuple[float, float, float]:
    """
    (edge, hole, bend) clearances for a blank of this short dimension.

    `relax` < 1 shrinks them to squeeze a mark onto a part that would
    otherwise go out blank, but never below the hard limits above -- those
    are set by the physics of bending and heat, not by preference.
    """
    return (
        max(HARD_EDGE, min(EDGE_MARGIN, EDGE_FRAC * short_side) * relax),
        max(HARD_HOLE, min(HOLE_CLEAR, HOLE_FRAC * short_side) * relax),
        max(HARD_BEND, min(BEND_CLEAR, BEND_FRAC * short_side) * relax),
    )


def _largest_rectangle(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """
    Largest all-True axis-aligned rectangle in a boolean matrix.

    Standard 'maximal rectangle in histogram' sweep: O(rows * cols).
    Returns (r0, c0, r1, c1) inclusive, or None.
    """
    rows, cols = mask.shape
    if rows == 0 or cols == 0:
        return None

    heights = np.zeros(cols, dtype=np.int32)
    best = (0, None)

    for r in range(rows):
        heights = np.where(mask[r], heights + 1, 0)

        stack: list[int] = []
        for c in range(cols + 1):
            h = heights[c] if c < cols else 0
            start = c
            while stack and stack[-1][1] >= h:
                s, sh = stack.pop()
                area = sh * (c - s)
                if area > best[0]:
                    best = (area, (r - sh + 1, s, r, c - 1))
                start = s
            stack.append((start, h))

    return best[1]


def safe_region(
    outer_pts: list[tuple[float, float]],
    hole_pts_list: list[list[tuple[float, float]]],
    bend_lines: list[list[tuple[float, float]]],
    relax: float = 1.0,
) -> Polygon | MultiPolygon | None:
    """Build the markable region as a shapely geometry."""
    try:
        outer = Polygon(outer_pts)
        if not outer.is_valid:
            outer = outer.buffer(0)

        minx, miny, maxx, maxy = outer.bounds
        edge_c, hole_c, bend_c = clearances(min(maxx - minx, maxy - miny), relax)

        region = outer.buffer(-edge_c)
        if region.is_empty:
            return None

        blockers = []
        for h in hole_pts_list:
            if len(h) < 3:
                continue
            poly = Polygon(h)
            if not poly.is_valid:
                poly = poly.buffer(0)
            blockers.append(poly.buffer(hole_c))
        for b in bend_lines:
            if len(b) < 2:
                continue
            blockers.append(LineString(b).buffer(bend_c))

        if blockers:
            region = region.difference(unary_union(blockers))
        return region if not region.is_empty else None
    except Exception:
        return None


def find_text_box(
    outer_pts: list[tuple[float, float]],
    hole_pts_list: list[list[tuple[float, float]]],
    bend_lines: list[list[tuple[float, float]]],
    relax: float = 1.0,
) -> tuple[float, float, float, float] | None:
    """
    Largest safe rectangle on the blank.

    Returns (xmin, ymin, xmax, ymax) in model coordinates, or None if the part
    has no markable area (very small parts like the stabiliser link often
    won't).
    """
    region = safe_region(outer_pts, hole_pts_list, bend_lines, relax)
    if region is None:
        return None

    minx, miny, maxx, maxy = region.bounds
    nx = max(1, int((maxx - minx) / GRID))
    ny = max(1, int((maxy - miny) / GRID))
    if nx < 2 or ny < 2:
        return None

    # Vectorised point-in-polygon over the whole grid at once. Looping cell by
    # cell with shapely Points is ~50x slower and becomes the pipeline
    # bottleneck at hundreds of parts.
    import shapely

    xs = minx + (np.arange(nx) + 0.5) * GRID
    ys = miny + (np.arange(ny) + 0.5) * GRID
    gx, gy = np.meshgrid(xs, ys)
    mask = shapely.contains_xy(region, gx.ravel(), gy.ravel()).reshape(ny, nx)

    rect = _largest_rectangle(mask)
    if rect is None:
        return None

    r0, c0, r1, c1 = rect
    return (
        minx + c0 * GRID,
        miny + r0 * GRID,
        minx + (c1 + 1) * GRID,
        miny + (r1 + 1) * GRID,
    )


# Text sizing. The mark scales with the clear area available on the blank, so
# a chassis carries a proportionally larger mark than a stabiliser link
# instead of both being stamped at one fixed height.
FILL = 0.72          # fraction of the safe box the text fills
# On a cramped part -- one whose mark would come out under GOOD_HEIGHT -- the
# text may use more of its box. The box is already inset by every clearance,
# so this only spends breathing room INSIDE the safe area, and it is the
# difference between 1.6 mm and about 1.9 mm on the nut clip.
CRAMPED_FILL = 0.90
MAX_HEIGHT = 8.0     # ceiling: past this, marking time grows for no benefit
MIN_HEIGHT = 1.6     # preferred floor: comfortably readable on steel
ABS_MIN_HEIGHT = 1.0 # hard floor: below this strokes crowd and the mark blurs


LINE_GAP = 0.35      # blank line spacing as a fraction of cap height
GOOD_HEIGHT = 3.0    # a mark at least this tall is comfortably readable


def fit_lines(
    box: tuple[float, float, float, float],
    lines: list[str],
    max_height: float = MAX_HEIGHT,
    min_height: float = MIN_HEIGHT,
    pad: float = 0.4,
    fill: float = FILL,
) -> tuple[float, float, float, bool] | None:
    """
    Fit one or more lines of text into `box`, sized in proportion to the box.

    Height is `fill` of whatever the box could hold, clamped to a legibility
    floor and a marking-time ceiling. Because the box is the largest genuinely
    clear area on that blank, a bigger part with more open face gets a bigger
    mark -- which is what makes a batch of mixed parts look deliberate rather
    than rubber-stamped.

    Returns (centre_x, centre_y, cap_height, rotated) or None.
    """
    from .strokefont import text_width, sanitize, stroke

    lines = [sanitize(t) for t in lines if sanitize(t)]
    if not lines:
        return None

    x0, y0, x1, y1 = box
    bw = (x1 - x0) - 2 * pad
    bh = (y1 - y0) - 2 * pad
    if bw <= 0 or bh <= 0:
        return None

    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0

    n = len(lines)
    # Filled letters are one weight wider and taller than their centrelines
    # (half a weight each side), so the ink block grows by that much.
    weight = stroke()
    widest_at_1 = max(text_width(t, 1.0) for t in lines) + weight
    block_at_1 = n + LINE_GAP * (n - 1) + weight   # block height per unit cap

    def best_h(avail_w: float, avail_h: float) -> float:
        if widest_at_1 <= 0 or block_at_1 <= 0:
            return 0.0
        natural = min(avail_w / widest_at_1, avail_h / block_at_1)
        scaled = natural * fill
        # Don't shrink below the floor merely because the box is snug: if the
        # box can hold min_height at all, take it.
        if scaled < min_height <= natural:
            scaled = min(min_height, natural)
        return min(scaled, max_height)

    cands = [(best_h(bw, bh), False), (best_h(bh, bw), True)]
    cands.sort(key=lambda p: -p[0])
    for h, rot in cands:
        if h >= min_height:
            return (cx, cy, h, rot)
    return None


def plan_mark(
    box: tuple[float, float, float, float],
    candidates: list[list[str]],
    good_height: float = GOOD_HEIGHT,
    min_height: float = MIN_HEIGHT,
):
    """
    Pick the best way to write the mark in `box`.

    `candidates` are label layouts in descending order of preference -- most
    complete first, most abbreviated last.

    A candidate that is comfortably readable AND no more than two lines deep
    wins outright: two lines is the natural shape of a mark, the ID over the
    part code, and there is nothing to gain by looking further.

    Everything else is a trade between cap height and how deeply the label had
    to be stacked, and height alone gets that trade wrong. Splitting
    `M-L2-G07 NUT` into `M / L2 / G07 / NUT` buys a fraction of a millimetre
    and spends it on four short fragments crowded together, where
    `M2-07 / NUT` is two lines shorter and reads at a glance. So an extra line
    has to EARN its place: it must come out at least 10% larger, not merely
    larger. A flatter layout wins even if it gives up a little size.

    This is what stops the tool choosing a label that technically fits at
    2 mm over one that fits cleanly at 3.5 mm. Trying only the fullest label
    and falling back on outright failure gets that trade wrong on exactly the
    small parts where legibility matters most.
    """
    best = None
    for lines in candidates:
        fitted = fit_lines(box, lines, min_height=min_height)
        if fitted is None or fitted[2] < good_height:
            roomy = fit_lines(box, lines, min_height=min_height, fill=CRAMPED_FILL)
            if roomy is not None and (fitted is None or roomy[2] > fitted[2]):
                fitted = (roomy[0], roomy[1], min(roomy[2], good_height), roomy[3])
        if fitted is None:
            continue
        if fitted[2] >= good_height and len(lines) <= 2:
            return lines, fitted
        if best is None:
            best = (lines, fitted)
            continue
        if len(lines) > len(best[0]):
            need = 1.10 ** (len(lines) - len(best[0]))
        elif len(lines) < len(best[0]):
            need = 0.91 ** (len(best[0]) - len(lines))
        else:
            need = 1.0
        if fitted[2] > best[1][2] * need:
            best = (lines, fitted)
    return best if best else (None, None)
