"""
Filled letters, for raster engraving.

WHY
---
A laser program treats a file two ways. A thin line it FOLLOWS: the head
traces the line with the beam on, which is how it cuts and how it has been
marking the group IDs. A filled shape it ENGRAVES: the head sweeps back and
forth like a printer, already at speed, and switches the beam on over the
shape. There is no start, stop or corner inside the letter, and those are
exactly where a line mark on steel blobs -- every stroke of every letter
begins with the head standing still and the beam on.

So in "filled" mode the group ID is not written as strokes at all. Each stroke
of the single-stroke font is thickened into a band of the chosen weight, the
bands are merged into letter shapes (counters in 0, 8, A, B stay open), and
the result is written to the DXF as a solid HATCH -- a filled region with no
outline, so there is no line on it for the machine to follow.

A primitive for a filled region is (layer, ring_points, key). Every ring that
shares a key is one filled object: its outer boundaries and its holes,
filled by the odd-even rule. One key per mark keeps each group ID a separate
object, so a laser program that engraves object by object sweeps a few
millimetres per mark rather than the whole sheet.
"""

from __future__ import annotations

from shapely.geometry import LineString, Point, Polygon, MultiPolygon
from shapely.ops import unary_union

# Segments per quarter circle on the round stroke ends. At the 0.2-0.6 mm
# weights a mark uses, 4 is already finer than the beam.
_QUAD = 4


def fill_rings(strokes, weight: float,
               flat_ends: bool = False) -> list[list[tuple[float, float]]]:
    """
    Merge `strokes` (centreline polylines) thickened to `weight` mm into
    closed rings: every letter's outer boundary and every counter.

    Letters get round ends. A bend line gets square ends (`flat_ends`): it runs
    edge to edge across the blank, and a round end would poke half a width
    past the cut edge onto the scrap.
    """
    if weight <= 0:
        return []
    half = weight / 2.0
    bands = []
    for s in strokes:
        pts = [tuple(p) for p in s]
        if len(pts) >= 2:
            bands.append(LineString(pts).buffer(
                half, quad_segs=_QUAD,
                cap_style="flat" if flat_ends else "round"))
        elif len(pts) == 1:
            bands.append(Point(pts[0]).buffer(half, quad_segs=_QUAD))
    if not bands:
        return []
    merged = unary_union(bands)
    if isinstance(merged, Polygon):
        polys = [merged]
    elif isinstance(merged, MultiPolygon):
        polys = list(merged.geoms)
    else:
        polys = [g for g in getattr(merged, "geoms", []) if isinstance(g, Polygon)]

    from shapely.geometry.polygon import orient

    # Outlines counter-clockwise, counters clockwise. write_dxf tells a counter
    # from an outline by its winding, and the nester only ever rotates a part
    # (never mirrors it), which keeps the winding, so fixing it here holds all
    # the way to the sheet.
    rings = []
    for poly in polys:
        if poly.is_empty:
            continue
        poly = orient(poly, sign=1.0)
        rings.append([(x, y) for x, y in poly.exterior.coords])
        for hole in poly.interiors:
            rings.append([(x, y) for x, y in hole.coords])
    return rings


def is_fill(prim) -> bool:
    """True when a primitive is one ring of a filled region."""
    return len(prim) > 2 and bool(prim[2])
