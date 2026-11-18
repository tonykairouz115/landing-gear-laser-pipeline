"""
Geometry-first classification of a flat-pattern DXF.

WHY NOT JUST TRUST LAYER NAMES
------------------------------
Fusion does put outer profile / interior profiles / bend centre lines / bend
extent lines / text on separate layers, and that is the whole reason this
automation is cheap. But layer names have drifted between Fusion releases and
between the Fusion and Inventor exporters (IV_BEND vs "Bend Centre Lines"),
and a student who round-trips a file through another tool can lose them
entirely.

So: layer names are treated as a HINT, and the actual topology is treated as
the TRUTH. A closed loop is a profile. The largest-area closed loop is the
outer profile. Closed loops inside it are holes. Open chains inside it are
bend lines. That reasoning holds no matter what the layers are called, which
is what lets this survive a full cohort and next year's Fusion update.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import ezdxf
from ezdxf import path as ezpath

# Two endpoints closer than this (mm) are treated as the same point.
WELD_TOL = 0.02
# Max sagitta when flattening arcs/splines to polylines (mm).
FLATTEN_TOL = 0.01


@dataclass
class Chain:
    """A connected run of points extracted from the source DXF."""
    points: list[tuple[float, float]]
    source_layer: str
    closed: bool = False
    area: float = 0.0
    role: str = ""              # CUT_OUTER | CUT_INNER | MARK_BEND | REF_EXTENT
    length: float = 0.0

    def bbox(self) -> tuple[float, float, float, float]:
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return min(xs), min(ys), max(xs), max(ys)


@dataclass
class PartGeometry:
    chains: list[Chain] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    units_scale: float = 1.0

    @property
    def outer(self) -> Chain | None:
        for c in self.chains:
            if c.role == "CUT_OUTER":
                return c
        return None

    def by_role(self, role: str) -> list[Chain]:
        return [c for c in self.chains if c.role == role]

    def bbox(self) -> tuple[float, float, float, float]:
        o = self.outer
        if o:
            return o.bbox()
        xs, ys = [], []
        for c in self.chains:
            for x, y in c.points:
                xs.append(x)
                ys.append(y)
        if not xs:
            return (0, 0, 0, 0)
        return min(xs), min(ys), max(xs), max(ys)


def _shoelace(pts: list[tuple[float, float]]) -> float:
    a = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return a / 2.0


def _polyline_length(pts: list[tuple[float, float]]) -> float:
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def _key(p: tuple[float, float]) -> tuple[int, int]:
    q = WELD_TOL
    return (round(p[0] / q), round(p[1] / q))


def _extract_segments(msp) -> list[tuple[list[tuple[float, float]], str]]:
    """Flatten every drawable entity to a polyline + its source layer."""
    out: list[tuple[list[tuple[float, float]], str]] = []
    for e in msp:
        dxftype = e.dxftype()
        if dxftype in ("TEXT", "MTEXT", "DIMENSION", "INSERT", "ATTDEF", "HATCH"):
            # Text/dims from the source are discarded -- we inject our own
            # marking text with a controlled single-stroke font.
            continue
        try:
            p = ezpath.make_path(e)
        except Exception:
            continue
        pts = [(v.x, v.y) for v in p.flattening(FLATTEN_TOL)]
        if len(pts) < 2:
            continue
        out.append((pts, e.dxf.layer if e.dxf.hasattr("layer") else "0"))
    return out


def _stitch(segments: list[tuple[list[tuple[float, float]], str]]) -> list[Chain]:
    """
    Join flattened segments end-to-end into chains.

    Fusion exports a profile as many separate LINE/ARC entities. To decide
    'is this a closed loop', they have to be stitched back together first.
    """
    # Adjacency by welded endpoint.
    remaining = list(range(len(segments)))
    used = set()
    endpoint_map: dict[tuple[int, int], list[int]] = {}
    for i, (pts, _lyr) in enumerate(segments):
        endpoint_map.setdefault(_key(pts[0]), []).append(i)
        endpoint_map.setdefault(_key(pts[-1]), []).append(i)

    chains: list[Chain] = []

    for start in remaining:
        if start in used:
            continue
        used.add(start)
        pts, layer = segments[start]
        chain_pts = list(pts)
        layers = {layer}

        def _is_loop(p):
            return len(p) > 2 and math.dist(p[0], p[-1]) <= WELD_TOL * 2

        # Extend forward, then backward -- but never past a loop that has
        # already closed. A profile exported as ONE closed polyline starts
        # and ends on a corner, and a bend line can end on that same corner;
        # extending there swallowed the bend line, the outline stopped being
        # closed, and the part lost its outer cut (AERO-L3-G02 LEG, 2026).
        for direction in (1, -1):
            if _is_loop(chain_pts):
                break
            if direction == -1:
                chain_pts.reverse()
            while not _is_loop(chain_pts):
                tail = chain_pts[-1]
                cand = None
                for j in endpoint_map.get(_key(tail), []):
                    if j in used:
                        continue
                    cand = j
                    break
                if cand is None:
                    break
                used.add(cand)
                jpts, jlayer = segments[cand]
                layers.add(jlayer)
                if math.dist(jpts[0], tail) <= WELD_TOL * 2:
                    chain_pts.extend(jpts[1:])
                else:
                    chain_pts.extend(list(reversed(jpts))[1:])

        closed = (
            len(chain_pts) > 2
            and math.dist(chain_pts[0], chain_pts[-1]) <= WELD_TOL * 2
        )
        if closed:
            chain_pts = chain_pts[:-1]

        src = sorted(layers)[0] if layers else "0"
        chains.append(
            Chain(
                points=chain_pts,
                source_layer=src,
                closed=closed,
                area=abs(_shoelace(chain_pts)) if closed else 0.0,
                # A closed loop's length includes the edge back to its start.
                length=_polyline_length(chain_pts + chain_pts[:1] if closed else chain_pts),
            )
        )
    return chains


def _point_in_poly(pt: tuple[float, float], poly: list[tuple[float, float]]) -> bool:
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xin = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < xin:
                inside = not inside
    return inside


def classify(chains: list[Chain], hint_fn) -> tuple[list[Chain], list[str]]:
    """
    Assign a role to every chain.

    Topology decides; the layer-name hint only breaks the one genuinely
    ambiguous case (bend CENTRE line vs bend EXTENT line -- both are open
    chains inside the profile, geometrically indistinguishable).
    """
    warnings: list[str] = []
    closed = [c for c in chains if c.closed]
    open_ = [c for c in chains if not c.closed]

    if not closed:
        warnings.append(
            "No closed loop found -- the outer profile is not watertight. "
            "This part cannot be cut as-is."
        )
        for c in chains:
            c.role = hint_fn(c.source_layer) or "MARK_BEND"
        return chains, warnings

    outer = max(closed, key=lambda c: c.area)
    outer.role = "CUT_OUTER"

    for c in closed:
        if c is outer:
            continue
        centroid = (
            sum(p[0] for p in c.points) / len(c.points),
            sum(p[1] for p in c.points) / len(c.points),
        )
        if _point_in_poly(centroid, outer.points):
            c.role = "CUT_INNER"
        else:
            c.role = "CUT_OUTER"
            warnings.append(
                f"Extra closed loop outside the main profile "
                f"(area {c.area:.1f} mm2) -- treated as a second outer profile. "
                f"Check for stray sketch geometry."
            )

    for c in open_:
        hint = hint_fn(c.source_layer)
        if hint in ("REF_EXTENT", "MARK_BEND", "MARK_TEXT"):
            c.role = hint
        else:
            c.role = "MARK_BEND"
            if hint is None:
                warnings.append(
                    f"Open chain on layer '{c.source_layer}' had no recognisable "
                    f"name -- assumed to be a bend line."
                )

    return chains, warnings


def load_part(dxf_path: str, hint_fn) -> PartGeometry:
    """Read a flat-pattern DXF and return classified geometry in millimetres."""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()

    # $INSUNITS: 1=in, 4=mm. Fusion writes mm when told to; convert if not.
    insunits = doc.header.get("$INSUNITS", 4)
    scale = 25.4 if insunits == 1 else 1.0

    segments = _extract_segments(msp)
    if scale != 1.0:
        segments = [([(x * scale, y * scale) for x, y in pts], lyr)
                    for pts, lyr in segments]

    chains = _stitch(segments)
    chains, warnings = classify(chains, hint_fn)

    if scale != 1.0:
        warnings.append(
            f"Source file was in inches; scaled by 25.4 to millimetres."
        )

    geo = PartGeometry(chains=chains, warnings=warnings, units_scale=scale)
    return geo
