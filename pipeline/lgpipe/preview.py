"""
SVG preview of a production sheet.

WHY
---
A DXF can only be looked at in a CAD viewer, and a CAD viewer shows it on the
background IT prefers. eDrawings uses black, which is exactly the colour the
outer profiles are: open a sheet there and the cut lines are invisible, which
looks alarming and means nothing.

So every sheet also gets an SVG beside it. Double-click, it opens in whatever
browser is already on the machine, white paper, real colours, no install and
no settings to find. It is for eyeballing a sheet before carrying it to the
shop -- is the nesting sane, did every part get a mark, is the mark big enough
to read -- and for nothing else. The DXF is what gets cut.

No new dependency: an SVG is text, and this writes it directly.
"""

from __future__ import annotations

import os

from . import layers as L

# Reference layers are drawn faintly: they are in the file, they must not be
# cut, and on a preview they should not compete with the geometry that is.
FAINT = ("REF_EXTENT", "REF_FRAME")
WIDE_PX = 1400          # preview width in pixels; the sheet scales to fit


def _style(layer_name: str) -> tuple[str, float, float]:
    """(stroke colour, width in mm, opacity) for a layer."""
    for spec in L.SCHEME.values():
        if spec.name == layer_name:
            rgb = "#%02x%02x%02x" % spec.rgb
            if spec.process == "reference":
                return "#b9c0c6", 0.6, 1.0
            if spec.process == "mark":
                return rgb, 0.5, 1.0
            return rgb, 0.9, 1.0
    return "#888888", 0.6, 1.0


def build(prims, path: str, sheet_w: float, sheet_h: float) -> str:
    """
    Write an SVG of `prims` -- the same (layer, points) list the DXF is made
    from, so the preview cannot drift from the file it claims to show.
    """
    scale = WIDE_PX / float(sheet_w or 1)
    px_w = sheet_w * scale
    px_h = sheet_h * scale

    # SVG's y axis runs down the page and the sheet's runs up it, so the whole
    # drawing is flipped once here rather than per point.
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{px_w:.0f}" '
        f'height="{px_h:.0f}" viewBox="0 0 {sheet_w:.2f} {sheet_h:.2f}">',
        f'<rect width="{sheet_w:.2f}" height="{sheet_h:.2f}" fill="#ffffff"/>',
        f'<g transform="translate(0,{sheet_h:.2f}) scale(1,-1)" '
        f'fill="none" stroke-linecap="round" stroke-linejoin="round">',
    ]

    buckets: dict[str, list] = {}
    fills: dict = {}
    for prim in prims:
        layer_name, pts = prim[0], prim[1]
        if len(prim) > 2 and prim[2]:
            # A ring of a filled (engraved) mark: drawn solid, below.
            fills.setdefault((layer_name, prim[2]), []).append(pts)
            continue
        buckets.setdefault(layer_name, []).append(pts)

    # Faint reference lines first, so real geometry draws over them.
    def order(name):
        return 0 if any(f in name for f in FAINT) else 1

    for layer_name in sorted(buckets, key=lambda n: (order(n), n)):
        colour, w, op = _style(layer_name)
        out.append(f'<g stroke="{colour}" stroke-width="{w:.2f}" '
                   f'opacity="{op:g}" data-layer="{layer_name}">')
        for pts in buckets[layer_name]:
            if len(pts) < 2:
                continue
            d = "M" + " L".join(f"{x:.3f},{y:.3f}" for x, y in pts)
            out.append(f'<path d="{d}"/>')
        out.append("</g>")

    # Filled marks: one path per mark, odd-even so counters stay open, and no
    # stroke -- exactly what the DXF carries.
    for (layer_name, _key), rings in fills.items():
        colour, _w, _op = _style(layer_name)
        d = " ".join("M" + " L".join(f"{x:.3f},{y:.3f}" for x, y in r) + " Z"
                     for r in rings if len(r) >= 3)
        out.append(f'<path d="{d}" fill="{colour}" fill-rule="evenodd" '
                   f'stroke="none" data-layer="{layer_name}"/>')

    out.append("</g>")
    out.append(f'<rect x="0" y="0" width="{sheet_w:.2f}" height="{sheet_h:.2f}" '
               f'fill="none" stroke="#9aa3ab" stroke-width="1"/>')
    out.append("</svg>")

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
        f.write("\n")
    return path
