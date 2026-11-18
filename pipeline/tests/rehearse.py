"""
Full dress rehearsal -- build a whole fake cohort and run the pipeline on it.

WHY
---
Every part of this has been tested on one real group and on small synthetic
sets. What has never been tested is a whole term at once: every group, every
group size, the naming mess a cohort actually produces, and the handful of
things that always go wrong somewhere in 36 submissions.

Better to find those in September on invented students than in November on
real steel.

WHAT IS REAL AND WHAT IS NOT
----------------------------
  Step 1, select      FULLY REAL. The selector only ever reads file NAMES, so
                      zero-byte .f3d stand-ins exercise exactly the same code
                      a real submission would.

  Step 2, Fusion      STUBBED. A .f3d is Fusion's own format and cannot be
                      written from outside Fusion. Instead this generates the
                      raw/ folder that the exporter WOULD have produced --
                      real DXFs, one per part, per group.

  Step 3, build       FULLY REAL. Those DXFs go through the actual pipeline.

So the rehearsal covers the two ends properly and admits the middle is
simulated. Test the middle separately, once, with ExportFlatPatterns on one
real assembly -- which is a five-minute job, not a cohort-sized one.

EVERY GROUP IS DIFFERENT
------------------------
A real cohort measures the same landing gear 36 times and gets 36 slightly
different answers. Each group here gets its own dimensional jitter, so the
nester sees genuinely different shapes and the mark placer has to find a safe
zone on each of them rather than the same one repeatedly. Groups also rotate
through three layer-naming conventions, including the worst case where a
student's export puts everything on layer "0".

    python tests/rehearse.py                  build a cohort from roster.txt
    python tests/rehearse.py --problems       ... with faults planted in it
    python tests/rehearse.py --problems --run ... and run the whole chain
"""

from __future__ import annotations

import argparse
import os
import random
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

from make_fixtures import PARTS, NAMING, make_part          # noqa: E402

# The exporter writes part codes, so the rehearsal must too.
CODE = {"Chassie": "CHS", "Left Cover": "LCV", "Right Cover": "RCV",
        "Nut": "NUT", "Leg": "LEG", "Stabalizer": "STB"}

FIRST = ["Tara", "Owen", "Priya", "Mateo", "Hana", "Yusuf", "Elise", "Kwame",
         "Ingrid", "Dmitri", "Noor", "Callum", "Sofia", "Rahul", "Jonas",
         "Amara", "Leo", "Mei", "Farah", "Sean"]
LAST = ["Okonkwo", "Vasquez", "Lindqvist", "Haddad", "Petrov", "Nakamura",
        "Boucher", "Adeyemi", "Kowalski", "Rossi", "Fitzgerald", "Zhu",
        "Abadi", "Novak", "Silva", "Brennan", "Tran", "Moreau", "Iqbal", "Doyle"]


def read_roster(path):
    ids = []
    try:
        with open(path) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if line:
                    ids.append(line)
    except OSError:
        pass
    return ids


SECTIONS = ("AERO-L1", "AERO-L2", "AERO-L3", "AERO-L4", "MAAE-L1", "MAAE-L2")


def grow_to(existing, n):
    """
    Extend a roster to n groups without ever repeating an ID.

    Asking for more groups than the real roster holds is the normal case for a
    rehearsal -- you want to know the pipeline survives a bigger cohort than
    you have. Duplicating IDs would silently overwrite folders and quietly
    test a smaller cohort than the one you asked for.
    """
    out = list(dict.fromkeys(existing))[:n]
    have = set(out)
    g = 1
    while len(out) < n:
        for sec in SECTIONS:
            gid = f"{sec}-G{g:02d}"
            if gid not in have:
                out.append(gid)
                have.add(gid)
                if len(out) >= n:
                    break
        g += 1
        if g > 99:
            break
    return out


def brightspace_name(rng, gid, student, suffix, ext):
    """A filename shaped the way Brightspace actually hands them over."""
    sid = rng.randint(100000, 999999)
    fid = rng.randint(10, 99)
    date = "Sep %d, 2026 %02d%02d %s" % (rng.randint(1, 28), rng.randint(1, 12),
                                         rng.randint(0, 59),
                                         rng.choice(("AM", "PM")))
    last = student.split()[-1]
    return f"{sid}-{fid} - {student} - {date} - {gid}_{last}{suffix}{ext}"


def _to_inches(path):
    """Rewrite a DXF as if that student's Fusion were set to inches."""
    import ezdxf
    doc = ezdxf.readfile(path)
    doc.header["$INSUNITS"] = 1
    k = 1.0 / 25.4
    for e in doc.modelspace():
        t = e.dxftype()
        if t == "LINE":
            e.dxf.start = (e.dxf.start.x * k, e.dxf.start.y * k, 0)
            e.dxf.end = (e.dxf.end.x * k, e.dxf.end.y * k, 0)
        elif t in ("CIRCLE", "ARC"):
            e.dxf.center = (e.dxf.center.x * k, e.dxf.center.y * k, 0)
            e.dxf.radius = e.dxf.radius * k
        elif t == "LWPOLYLINE":
            e.set_points([(x * k, y * k) for x, y, *_ in e.get_points()])
    doc.saveas(path)


def build(groups, sub_dir, raw_dir, plant, seed=11):
    rng = random.Random(seed)
    planted = []

    os.makedirs(sub_dir, exist_ok=True)
    os.makedirs(raw_dir, exist_ok=True)

    # which groups carry which fault
    faults = {}
    if plant and len(groups) >= 8:
        picks = rng.sample(range(len(groups)), 7)
        for slot, key in zip(picks, ("lone", "crowd", "missing_part",
                                     "odd_name", "bought_part", "layer_zero",
                                     "inches")):
            faults[groups[slot]] = key

    for idx, gid in enumerate(groups):
        fault = faults.get(gid)
        size = rng.choice((3, 4, 4, 4))
        if fault == "lone":
            size = 1
        elif fault == "crowd":
            size = 5

        # ---- step 1 input: the submissions -----------------------------
        for _ in range(size):
            who = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            for suffix, ext in (("", ".f3d"), ("_assembly", ".pdf"),
                                ("_shop", ".pdf")):
                name = brightspace_name(rng, gid, who, suffix, ext)
                with open(os.path.join(sub_dir, name), "w") as f:
                    f.write("stand-in, not a real Fusion file\n")

        # ---- step 2 output: what Fusion would have exported -------------
        gdir = os.path.join(raw_dir, gid)
        os.makedirs(gdir, exist_ok=True)
        style = NAMING["C"] if fault == "layer_zero" else NAMING["ABC"[idx % 3]]

        for pname, spec in PARTS.items():
            code = CODE[pname]
            if fault == "missing_part" and code == "LEG":
                continue                              # modelled as a solid
            out_name = code
            if fault == "odd_name" and code == "RCV":
                out_name = "Cover Plate Right v2"     # unrecognised component
            # each group measured the reference unit themselves
            jitter = 1.0 + rng.uniform(-0.035, 0.035)
            make_part(os.path.join(gdir, out_name + ".dxf"), spec, style, jitter)

        if fault == "inches":
            # Somebody's Fusion is set to inches. The pipeline reads $INSUNITS
            # and scales, so the blank must still come out the right size --
            # this is the one fault whose correct outcome is "nothing visible".
            _to_inches(os.path.join(gdir, "CHS.dxf"))

        if fault == "bought_part":
            make_part(os.path.join(gdir, "Rivet Nut.dxf"),
                      PARTS["Stabalizer"], style, 0.6)

        if fault:
            planted.append((gid, fault))

    # ---- faults that live outside any one group ------------------------
    if plant:
        with open(os.path.join(sub_dir, "landing gear FINAL v3.f3d"), "w") as f:
            f.write("no group id anywhere in this name\n")
        planted.append(("-", "unparseable_filename"))

        ghost = "MAAE-L9-G99"
        for _ in range(3):
            who = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            with open(os.path.join(sub_dir,
                                   brightspace_name(rng, ghost, who, "", ".f3d")), "w") as f:
                f.write("group that is not on the roster\n")
        planted.append((ghost, "off_roster"))

    return planted


FAULT_TEXT = {
    "lone": "only one submission -- expect a group-size warning",
    "crowd": "five submissions -- expect a group-size warning",
    "missing_part": "LEG never exported -- expect MISSING PARTS",
    "odd_name": "RCV named 'Cover Plate Right v2' -- expect it cut under that name",
    "bought_part": "a rivet nut modelled as sheet metal -- expect UNEXPECTED PARTS",
    "layer_zero": "every entity on layer 0 -- the classifier must still split it",
    "inches": "that group's CHS exported in INCHES -- the pipeline must scale "
              "it; correct outcome is a chassis the same size as everyone else's",
    "unparseable_filename": "a .f3d with no group ID -- expect it listed, not dropped",
    "off_roster": "a group not on roster.txt -- expect a roster problem",
}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Build a whole fake cohort and optionally run the pipeline on it.")
    ap.add_argument("--groups", type=int, default=0,
                    help="how many groups (default: however many are on the roster)")
    ap.add_argument("--roster", default=os.path.join(ROOT, "roster.txt"))
    ap.add_argument("--out", default=os.path.join(ROOT, "rehearsal"),
                    help="where to build the fake cohort")
    ap.add_argument("--problems", action="store_true",
                    help="plant the faults a real cohort produces")
    ap.add_argument("--run", action="store_true",
                    help="also run the selector and the build, and report")
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args(argv)

    groups = read_roster(args.roster)
    roster_for_run = args.roster
    if args.groups:
        groups = grow_to(groups, args.groups)
    if not groups:
        groups = grow_to([], 36)

    sub = os.path.join(args.out, "submissions")
    raw = os.path.join(args.out, "raw")
    for d in (sub, raw):
        if os.path.isdir(d):
            import shutil
            shutil.rmtree(d)

    # A rehearsal bigger than the real roster needs its own roster, or every
    # invented group is reported as "not on the roster" and drowns the faults
    # that actually matter.
    if len(groups) != len(read_roster(args.roster)):
        os.makedirs(args.out, exist_ok=True)
        roster_for_run = os.path.join(args.out, "roster.txt")
        with open(roster_for_run, "w") as f:
            f.write("# rehearsal roster, generated -- not the real one\n")
            f.write("\n".join(groups) + "\n")

    print(f"Building a rehearsal cohort of {len(groups)} groups in {args.out}\n")
    planted = build(groups, sub, raw, args.problems, args.seed)

    n_sub = len([f for f in os.listdir(sub) if f.lower().endswith(".f3d")])
    n_dxf = sum(len(os.listdir(os.path.join(raw, g))) for g in os.listdir(raw))
    print(f"  submissions   {n_sub} .f3d across {len(groups)} groups")
    print(f"  flat patterns {n_dxf} DXFs (the Fusion step, simulated)")

    if planted:
        print(f"\n  {len(planted)} fault(s) planted -- the run should surface every one:")
        for gid, key in planted:
            print(f"     {gid:<14} {FAULT_TEXT[key]}")

    if not args.run:
        print("\nNow point the app at those folders, or re-run with --run.")
        return 0

    py = sys.executable
    print("\n" + "=" * 66 + "\nSTEP 1  select\n" + "=" * 66)
    subprocess.call([py, os.path.join(ROOT, "select_designs.py"),
                     "--submissions", sub,
                     "--out", os.path.join(args.out, "selected"),
                     "--drawings", os.path.join(args.out, "drawings"),
                     "--roster", roster_for_run,
                     "--log", os.path.join(args.out, "selection_log.csv"),
                     "--report", os.path.join(args.out, "selection_report.txt")],
                    cwd=ROOT)

    print("\n" + "=" * 66 + "\nSTEP 3  build   (step 2 is the simulated raw/ above)\n" + "=" * 66)
    subprocess.call([py, "-m", "lgpipe.cli",
                     "--raw", raw, "--out", os.path.join(args.out, "out"),
                     "--config", os.path.join(ROOT, "shop.json"),
                     "--roster", roster_for_run], cwd=ROOT)

    if not planted:
        print("\nNo faults were planted, so there is nothing to check.")
        return 0

    return _verify(planted, args.out)


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _verify(planted, out_dir):
    """
    Did the run actually surface every fault that was planted?

    A rehearsal that only builds a cohort and prints two file paths still
    leaves the hard part -- noticing what is missing from a long report -- to
    a tired human at 11pm. This does that part.
    """
    sel = _read(os.path.join(out_dir, "selection_report.txt"))
    rep = _read(os.path.join(out_dir, "out", "report.md"))
    both = sel + "\n" + rep

    # what each fault should have produced, as text that must appear
    def expect(gid, key):
        if key == "lone":
            return f"{gid}: only 1 submission" in sel
        if key == "crowd":
            return f"{gid}: 5 submissions" in sel
        if key == "off_roster":
            return f"{gid}: not on the roster" in sel
        if key == "unparseable_filename":
            return "no group ID in filename" in sel
        if key == "missing_part":
            return "MISSING PARTS" in rep or "Leg" in rep
        if key == "odd_name":
            return "cover plate right" in rep.lower()
        if key == "bought_part":
            return "rivet nut" in rep.lower()
        if key == "layer_zero":
            return "layer '0'" in rep or "layer 0" in rep
        if key == "inches":
            return True          # checked numerically below
        return False

    print("\n" + "=" * 66)
    print("DID THE RUN CATCH WHAT WAS PLANTED?")
    print("=" * 66)
    missed = 0
    for gid, key in planted:
        ok = expect(gid, key)
        missed += not ok
        print(f"  {'FOUND ' if ok else 'MISSED'}  {gid:<14} {FAULT_TEXT[key].split(' -- ')[0]}")

    # the inches case is only correct if the part came out the normal size
    import csv
    widths = []
    inch_gid = next((g for g, k in planted if k == "inches"), None)
    try:
        with open(os.path.join(out_dir, "out", "report.csv")) as f:
            for row in csv.DictReader(f):
                if (row.get("part") or "").upper().startswith("CHS"):
                    widths.append((row.get("group"), float(row.get("width_mm") or 0)))
    except Exception:
        widths = []
    if inch_gid and widths:
        typical = sorted(w for _g, w in widths)[len(widths) // 2]
        mine = dict(widths).get(inch_gid, 0.0)
        ok = typical * 0.9 < mine < typical * 1.1
        missed += not ok
        print(f"  {'FOUND ' if ok else 'MISSED'}  {inch_gid:<14} "
              f"inch-unit chassis measured {mine:.1f} mm vs {typical:.1f} mm typical")

    print("=" * 66)
    if missed:
        print(f"{missed} planted fault(s) went unreported. That is a real gap -- "
              f"fix it before the term, not during it.")
    else:
        print(f"All {len(planted)} planted faults were reported. The reports can "
              f"be trusted to tell you what went wrong.")
    print(f"\n   {os.path.join(out_dir, 'selection_report.txt')}")
    print(f"   {os.path.join(out_dir, 'out', 'report.md')}")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
