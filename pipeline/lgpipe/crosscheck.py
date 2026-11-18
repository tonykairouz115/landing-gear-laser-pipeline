"""
Cross-group consistency checks.

THE IDEA
--------
a full cohort all reverse-engineer the SAME physical landing gear. So although no
two chassis are dimensionally identical, they should all be within a few
percent of each other, and they should all have exactly the same NUMBER of
holes and bends. That redundancy makes deviations visible: a part is measured
against 68 independent opinions of what that part should be.

WHAT THIS IS NOT
----------------
It is not a gate, and it does not correct anything. The blank is cut exactly
as submitted, always. A group that forgot a hole gets a part with no hole; a
group that measured 70 mm where the reference is 61.7 gets a 70 mm part; a
group that modelled a bend that does not exist gets that bend scribed and can
try to fold it. Getting it wrong IS the assessment, and a pipeline that
quietly fixed those mistakes would erase the thing being marked.

So every deviation here is recorded as EVIDENCE, in neutral terms, and written
to a marking annex for the TA team. Nothing is blocked. The only fatal
conditions in the pipeline are physical impossibilities -- a profile that is
not closed, or a blank larger than the stock sheet -- because those cannot be
cut at all.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

# A blank more than this fraction off the cohort median is suspicious.
# Independent measurement of the same part realistically scatters a few
# percent; 12% is well outside that but still below "obviously wrong".
SIZE_TOL = 0.12


def _mode(values):
    try:
        return statistics.mode(values)
    except statistics.StatisticsError:
        return statistics.median_low(values)


def check(results) -> list[str]:
    """
    Annotate `results` in place with cross-group warnings.
    Returns a list of cohort-level summary lines for the report.
    """
    by_part = defaultdict(list)
    for r in results:
        if r.ok and r.width > 0:
            by_part[r.part_name.strip().lower()].append(r)

    summary = []
    for part, rows in sorted(by_part.items()):
        if len(rows) < 5:
            continue        # too few to say anything about the cohort

        med_w = statistics.median(r.width for r in rows)
        med_h = statistics.median(r.height for r in rows)
        mode_holes = _mode([r.n_holes for r in rows])
        mode_bends = _mode([r.n_bends for r in rows])

        n_odd = 0
        for r in rows:
            dw = abs(r.width - med_w) / med_w if med_w else 0
            dh = abs(r.height - med_h) / med_h if med_h else 0
            dev = max(dw, dh)

            # Recorded on the result so the marking annex can quantify it.
            r.deviation = {
                "median_w": round(med_w, 2),
                "median_h": round(med_h, 2),
                "size_dev_pct": round(dev * 100, 1),
                "class_holes": mode_holes,
                "holes_delta": r.n_holes - mode_holes,
                "class_bends": mode_bends,
                "bends_delta": r.n_bends - mode_bends,
            }

            # These are OBSERVATIONS, not faults. The part is cut exactly as
            # submitted either way -- a group that mis-measured gets a part
            # built to their own measurement, which is the point of the
            # exercise. The note exists so the marking team can see it.
            if dev > SIZE_TOL:
                n_odd += 1
                r.note(
                    f"{dev * 100:.0f}% from class median "
                    f"({r.width:.1f} x {r.height:.1f} vs {med_w:.1f} x {med_h:.1f} mm)"
                )
            if r.n_holes != mode_holes:
                n_odd += 1
                d = r.n_holes - mode_holes
                r.note(
                    f"{abs(d)} {'more' if d > 0 else 'fewer'} interior cut(s) "
                    f"than the class ({r.n_holes} vs {mode_holes})"
                )
            if r.n_bends != mode_bends:
                n_odd += 1
                d = r.n_bends - mode_bends
                r.note(
                    f"{abs(d)} {'more' if d > 0 else 'fewer'} bend line(s) "
                    f"than the class ({r.n_bends} vs {mode_bends})"
                )

        summary.append(
            f"| `{rows[0].part_name}` | {len(rows)} | "
            f"{med_w:.1f} x {med_h:.1f} | {mode_holes} | {mode_bends} | {n_odd} |"
        )

    return summary


def roster_check(group_ids, roster_path) -> tuple[set, list[str]]:
    """
    Compare the group folders found against an expected roster.

    This is the one failure the geometry cannot catch. A student who names
    their file `L2-G7.f3d` instead of `L2-G07.f3d`, or leaves it as
    `Landing Gear v3.f3d`, produces a perfectly valid DXF carrying a WRONG
    laser mark. Nothing downstream can tell -- the parts look right, cut
    right, and end up in the wrong team's hands.

    So the roster is checked explicitly, and an unrecognised ID is a hard
    stop rather than a warning.
    """
    problems = []
    if not roster_path:
        return set(), problems

    with open(roster_path, encoding="utf-8", errors="replace") as f:
        expected = {
            line.split("#", 1)[0].strip().upper()
            for line in f
            if line.split("#", 1)[0].strip()
        }

    found = {g.strip().upper() for g in group_ids}

    unknown = sorted(found - expected)
    missing = sorted(expected - found)

    for u in unknown:
        problems.append(
            f"Group '{u}' is not on the roster, so its parts were LEFT OFF the "
            f"sheet -- they would be laser-marked '{u}', which belongs to "
            f"nobody. Fix the file name (or add the group to roster.txt) and "
            f"build again."
        )
    for m in missing:
        problems.append(
            f"Roster group '{m}' submitted nothing -- no folder found."
        )
    return expected, problems
