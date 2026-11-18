"""
Generate synthetic flat-pattern DXFs that mimic what Fusion actually exports,
so the pipeline can be tested end-to-end without Fusion installed.

Deliberately adversarial, to prove the pipeline is robust:
  * Profiles are written as many separate LINE and ARC entities, not one
    polyline -- exactly how Fusion writes them, and the reason the stitcher
    exists.
  * Group A uses descriptive Fusion-style layer names.
  * Group B uses Inventor-style IV_* names.
  * Group C dumps EVERYTHING on layer "0" -- the worst case, where only
    geometric reasoning can recover the cut/mark split.
  * Dimensions vary per group, as they will when every team measure
    independently.
"""

import math
import os
import random

import ezdxf

# Written to a TEST folder, never to raw/ -- raw/ is where the real Fusion
# export lands, and fake groups mixed in there would be cut with the real ones.
OUT = os.path.join(os.path.dirname(__file__), "..", "raw_test")

NAMING = {
    "A": {"outer": "Outer Profile", "inner": "Interior Profiles",
          "bend": "Bend Center Lines", "extent": "Bend Extent Lines"},
    "B": {"outer": "IV_OUTER_PROFILE", "inner": "IV_INTERIOR_PROFILES",
          "bend": "IV_BEND", "extent": "IV_BEND_EXTENT"},
    "C": {"outer": "0", "inner": "0", "bend": "0", "extent": "0"},
}


def add_rect_as_lines(msp, x0, y0, w, h, layer, r=0.0):
    """Rectangle emitted as discrete LINE (+ ARC) entities, like Fusion does."""
    x1, y1 = x0 + w, y0 + h
    if r <= 0:
        pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        for i in range(4):
            msp.add_line(pts[i], pts[(i + 1) % 4], dxfattribs={"layer": layer})
        return

    msp.add_line((x0 + r, y0), (x1 - r, y0), dxfattribs={"layer": layer})
    msp.add_line((x1, y0 + r), (x1, y1 - r), dxfattribs={"layer": layer})
    msp.add_line((x1 - r, y1), (x0 + r, y1), dxfattribs={"layer": layer})
    msp.add_line((x0, y1 - r), (x0, y0 + r), dxfattribs={"layer": layer})
    for cx, cy, a0, a1 in [
        (x1 - r, y0 + r, 270, 360), (x1 - r, y1 - r, 0, 90),
        (x0 + r, y1 - r, 90, 180), (x0 + r, y0 + r, 180, 270),
    ]:
        msp.add_arc((cx, cy), r, a0, a1, dxfattribs={"layer": layer})


def make_part(path, spec, names, jitter):
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()

    w = spec["w"] * jitter
    h = spec["h"] * jitter

    add_rect_as_lines(msp, 0, 0, w, h, names["outer"], spec.get("corner", 0))

    for hx, hy, hd in spec["holes"]:
        msp.add_circle((hx * w, hy * h), hd / 2.0,
                       dxfattribs={"layer": names["inner"]})

    for sx0, sy0, sx1, sy1 in spec.get("slots", []):
        add_rect_as_lines(msp, sx0 * w, sy0 * h,
                          (sx1 - sx0) * w, (sy1 - sy0) * h,
                          names["inner"], 1.0)

    for frac, horiz in spec["bends"]:
        if horiz:
            msp.add_line((0, frac * h), (w, frac * h),
                         dxfattribs={"layer": names["bend"]})
            for d in (-1, 1):
                y = frac * h + d * 1.2
                msp.add_line((0, y), (w, y), dxfattribs={"layer": names["extent"]})
        else:
            msp.add_line((frac * w, 0), (frac * w, h),
                         dxfattribs={"layer": names["bend"]})
            for d in (-1, 1):
                x = frac * w + d * 1.2
                msp.add_line((x, 0), (x, h), dxfattribs={"layer": names["extent"]})

    doc.saveas(path)


PARTS = {
    "Chassie": {
        "w": 142, "h": 112, "corner": 3,
        "holes": [(0.12, 0.20, 3.2), (0.12, 0.80, 3.2),
                  (0.88, 0.20, 3.2), (0.88, 0.80, 3.2),
                  (0.50, 0.50, 8.0), (0.30, 0.50, 3.2)],
        "slots": [(0.60, 0.42, 0.72, 0.58)],
        "bends": [(0.18, True), (0.82, True), (0.15, False), (0.85, False)],
    },
    "Left Cover": {
        "w": 112, "h": 96, "corner": 2,
        "holes": [(0.15, 0.15, 3.2), (0.15, 0.85, 3.2),
                  (0.85, 0.15, 3.2), (0.85, 0.85, 3.2), (0.5, 0.55, 12.0)],
        "bends": [(0.20, True), (0.80, True)],
    },
    "Right Cover": {
        "w": 112, "h": 96, "corner": 2,
        "holes": [(0.15, 0.15, 3.2), (0.15, 0.85, 3.2),
                  (0.85, 0.15, 3.2), (0.85, 0.85, 3.2), (0.5, 0.55, 12.0)],
        "bends": [(0.20, True), (0.80, True)],
    },
    "Nut": {
        "w": 32, "h": 27, "corner": 1.5,
        "holes": [(0.5, 0.62, 5.2), (0.22, 0.18, 2.2), (0.78, 0.18, 2.2)],
        "bends": [(0.35, True)],
    },
    "Leg": {
        "w": 106, "h": 26, "corner": 2,
        "holes": [(0.10, 0.5, 3.2), (0.50, 0.5, 3.2), (0.90, 0.5, 4.2)],
        "bends": [(0.28, False)],
    },
    "Stabalizer": {
        "w": 44, "h": 13, "corner": 5,
        "holes": [(0.14, 0.5, 3.2), (0.86, 0.5, 3.2)],
        "bends": [],
    },
}


def main():
    random.seed(7)
    os.makedirs(OUT, exist_ok=True)
    for section, gnum in [("A", 1), ("B", 2), ("C", 3)]:
        gid = f"{section}-G{gnum:02d}"
        gdir = os.path.join(OUT, gid)
        os.makedirs(gdir, exist_ok=True)
        names = NAMING[section]
        for pname, spec in PARTS.items():
            jitter = 1.0 + random.uniform(-0.03, 0.03)
            make_part(os.path.join(gdir, f"{pname}.dxf"), spec, names, jitter)
        print(f"wrote {gid} ({len(PARTS)} parts, layer style {section})")


if __name__ == "__main__":
    main()
