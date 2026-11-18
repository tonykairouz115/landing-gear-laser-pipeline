"""
Turn the filled (HATCH) group IDs on a built sheet into a VECTOR fill:
concentric outlines stepped inward until the letter is filled. The laser
then marks each label completely, one at a time, instead of rastering the
whole sheet line by line. Nothing else on the sheet is touched.

    python tools_vectorfill.py sheet_01.dxf sheet_01_vectorfill.dxf --step 0.15
"""
import argparse
import ezdxf
from ezdxf import path as zpath
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union


def hatch_polygons(h):
    rings = []
    for p in h.paths:
        pts = [(v.x, v.y) for v in zpath.from_hatch_boundary_path(p).flattening(0.01)]
        if len(pts) >= 3:
            rings.append(Polygon(pts).buffer(0))
    # odd-even: symmetric difference of all rings (letters minus counters)
    geom = None
    for r in rings:
        geom = r if geom is None else geom.symmetric_difference(r)
    return geom


def contour_rings(geom, step):
    out = []
    g = geom.buffer(-step / 2, join_style=2)        # first pass half a step in
    while not g.is_empty:
        polys = g.geoms if isinstance(g, MultiPolygon) else [g]
        for p in polys:
            if p.is_empty:
                continue
            out.append(list(p.exterior.coords))
            out += [list(i.coords) for i in p.interiors]
        g = g.buffer(-step, join_style=2)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--step", type=float, default=0.15, help="mm between passes")
    a = ap.parse_args()
    doc = ezdxf.readfile(a.src)
    msp = doc.modelspace()
    hatches = [e for e in msp if e.dxftype() == "HATCH" and e.dxf.layer == "MARK_TEXT"]
    n_rings = 0
    length = 0.0
    for h in hatches:
        attribs = {"layer": "MARK_TEXT", "color": h.dxf.color}
        if h.dxf.hasattr("true_color"):
            attribs["true_color"] = h.dxf.true_color
        geom = hatch_polygons(h)
        if geom is None or geom.is_empty:
            continue
        for ring in contour_rings(geom, a.step):
            pl = msp.add_lwpolyline(ring[:-1], close=True, dxfattribs=attribs)
            n_rings += 1
            length += Polygon(ring).exterior.length
        msp.delete_entity(h)
    doc.saveas(a.dst)
    print("%d labels -> %d vector rings, %.0f mm of marking" % (len(hatches), n_rings, length))


if __name__ == "__main__":
    main()
