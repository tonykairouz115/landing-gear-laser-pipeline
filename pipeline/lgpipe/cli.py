"""
Command line entry point.

    python -m lgpipe.cli --raw raw/ --out out/

Expected input layout (produced by the Fusion batch-export add-in):

    raw/
      A-G01/
        Chassie.dxf
        Left Cover.dxf
        Right Cover.dxf
        Nut.dxf
        Leg.dxf
        Stabalizer.dxf
      A-G02/
        ...

Outputs:

    out/
      parts/A-G01/Chassie.dxf     normalised + marked, one part per file
      out/sheets/sheet_01.dxf     nested production sheets
      out/report.csv              per-part validation results
      out/report.md               human-readable summary for the shop
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import re
import sys

from . import config as cfgmod
from . import crosscheck
from . import layers as L
from .nest import make_item, nest, placed_primitives, NestItem
from .process import process_part, write_dxf, MATERIAL_THICKNESS

# Stabilizer link is qty 2 per unit; everything else is qty 1.
# Quantity per assembled unit. The Stabilizer link is the only one that is
# not 1, and getting it wrong means a group is short a part at assembly --
# so every accepted spelling AND the short code has to be listed here.
QUANTITIES = {
    "chassie": 1, "chassis": 1, "chs": 1,
    "left cover": 1, "leftcover": 1, "left_cover": 1, "lcv": 1,
    "right cover": 1, "rightcover": 1, "right_cover": 1, "rcv": 1,
    "nut": 1,
    "leg": 1,
    "stabalizer": 2, "stabilizer": 2, "stabaliser": 2, "stabiliser": 2, "stb": 2,
}

# A group must supply one of each. Either naming is accepted: the full
# component name or the three-letter code.
EXPECTED_SETS = [
    {"chassie", "chassis", "chs"},
    {"left cover", "leftcover", "left_cover", "lcv"},
    {"right cover", "rightcover", "right_cover", "rcv"},
    {"nut"},
    {"leg"},
    {"stabalizer", "stabilizer", "stabaliser", "stabiliser", "stb"},
]
EXPECTED_LABELS = ["Chassie", "Left Cover", "Right Cover", "Nut", "Leg", "Stabalizer"]


# ---------------------------------------------------------- group identity --
#
# What gets laser-marked is the GROUP, never a student. The folder name comes
# from whatever the submitted file was called, and a student's own file is
# quite reasonably named after themselves. That name must not reach the steel:
# the blanks go into a communal bin, several teams collect from it, and a part
# carrying one person's name identifies nothing useful and exposes them for no
# reason.
#
# So the folder name is reduced to the group code before anything is marked.
_DEPTS = ("MAAE", "AERO")
_GSEP = r"[-_.\s]*"
_GROUP_RE = re.compile(
    rf"({'|'.join(_DEPTS)}){_GSEP}(L\s*\d+){_GSEP}G(?:ROUP)?{_GSEP}0*(\d+)",
    re.IGNORECASE)


def canonical_group_id(folder_name: str) -> str | None:
    """
    'MAAE-L1-G07'                 -> 'MAAE-L1-G07'   (unchanged)
    'MAAE-L1-G07_Kairouz_Tony'    -> 'MAAE-L1-G07'   (name stripped)
    'Tony Kairouz maae l1 g7'     -> 'MAAE-L1-G07'
    'landing gear final'          -> None            (caller keeps the folder
                                                      name and flags it)
    """
    # "MAAE2001 L1 G 1" is how Brightspace names the MAAE groups; the course
    # number is not part of the ID.
    name = re.sub(r"(?i)\b(MAAE|AERO)[\s_-]*2001(?!\d)", r"\1", folder_name or "")
    m = _GROUP_RE.search(name)
    if not m:
        return None
    section = re.sub(r"\s+", "", m.group(2)).upper()
    return f"{m.group(1).upper()}-{section}-G{int(m.group(3)):02d}"


def qty_for(part_name: str) -> int:
    return QUANTITIES.get(part_name.strip().lower(), 1)


def load_logo(path: str | None):
    """Load a logo as stroke polylines from a DXF, or return None."""
    if not path:
        return None
    try:
        import ezdxf
        from ezdxf import path as ezpath
        doc = ezdxf.readfile(path)
        strokes = []
        for e in doc.modelspace():
            try:
                p = ezpath.make_path(e)
            except Exception:
                continue
            pts = [(v.x, v.y) for v in p.flattening(0.05)]
            if len(pts) >= 2:
                strokes.append(pts)
        return strokes or None
    except Exception as e:
        print(f"  ! could not load logo from {path}: {e}", file=sys.stderr)
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Landing-gear DXF production pipeline")
    ap.add_argument("--config", default=None,
                    help="shop profile (JSON) holding sheet size, kerf, mark "
                         "style and nesting mode, so a run is one command with "
                         "no decisions to remember. Explicit flags override it.")
    ap.add_argument("--write-config", metavar="PATH", default=None,
                    help="write a starter shop profile and exit")
    ap.add_argument("--test-card", metavar="PATH", default=None,
                    help="write a small DXF carrying one labelled tile per "
                         "machine colour, and exit. Cut it on scrap to find out "
                         "what each colour actually does on that laser before "
                         "committing a real sheet.")
    ap.add_argument("--mark-card", metavar="PATH", default=None,
                    help="write a small DXF carrying the same group ID marked "
                         "in both fonts at every size, and exit. Cut it on "
                         "scrap to find the smallest mark this machine can "
                         "hold and which letter shapes it draws cleanly.")
    ap.add_argument("--marking-annex", action="store_true",
                    help="also write marking.csv and cohort-deviation notes. "
                         "OFF by default: marking is done by hand on the "
                         "assembled unit, not from the blanks. This only exists "
                         "if you ever want the numbers.")
    ap.add_argument("--roster", default=None,
                    help="text file of expected group IDs, one per line. A "
                         "folder whose ID is not on the roster is a hard stop: "
                         "it would be laser-marked with an ID belonging to "
                         "nobody, and nothing downstream could tell.")
    ap.add_argument("--raw", required=False, help="directory of raw per-group DXF folders")
    ap.add_argument("--out", required=False, help="output directory")
    ap.add_argument("--sheet", default=None,
                    help="stock sheet size in mm, WxH (default 1220x2440)")
    ap.add_argument("--mark",
                    choices=("group", "group-code", "group-part",
                             "short", "short-code"),
                    default=None,
                    help="what to laser-mark on each blank. 'group' = the group "
                         "ID alone (MAAE-L1-G07). 'group-code' = plus a "
                         "canonical part code (MAAE-L1-G07 CHS) so a blank can "
                         "be identified in a communal bin -- recommended when "
                         "students collect their own parts. 'group-part' = plus "
                         "the raw component name, which puts student typos on "
                         "the steel. 'short' and 'short-code' are the same two "
                         "with the ID compressed to M1-07: fewer characters "
                         "means fewer stops for the head and a larger mark in "
                         "the same clear area, which is what small blanks need.")
    ap.add_argument("--letters", choices=("hairline", "filled"), default=None,
                    help="for this run only, override how the group ID is "
                         "drawn (normally set per colour on the Settings "
                         "page). 'hairline' = single lines the laser follows. "
                         "'filled' = solid letters with no outline, for the "
                         "laser to ENGRAVE.")
    ap.add_argument("--stroke-pct", type=float, default=None,
                    help="for this run only, the group ID's stroke thickness "
                         "when filled, as a percent of the letter height "
                         "(5-25; default 13).")
    ap.add_argument("--kerf", type=float, default=None,
                    help="pre-compensate the cut paths for this kerf width in "
                         "mm (outer profiles outward, holes inward). Default 0 "
                         "= geometry stays nominal and the MACHINE compensates, "
                         "which is correct for almost every shop. Only set this "
                         "if the laser cannot do it, and then make sure machine "
                         "compensation is OFF -- doing both puts parts a full "
                         "kerf out of tolerance.")
    ap.add_argument("--part-gap", type=float, default=None,
                    help="mm between neighbouring parts on the sheet "
                         "(default 4.0). Set in the DXF, so the laser cuts "
                         "exactly this spacing.")
    ap.add_argument("--sheet-margin", type=float, default=None,
                    help="mm of clear border around the sheet (default 8.0)")
    ap.add_argument("--logo", default=None, help="optional logo DXF (line art)")
    ap.add_argument("--no-nest", action="store_true", help="skip sheet nesting")
    ap.add_argument("--nest-mode", choices=("corner", "compact", "group", "per-group"), default=None,
                    help="corner = compact, grown out of one corner so the "
                         "leftover steel stays one reusable rectangle; "
                         "compact = fewest sheets (relies on ID marking to sort "
                         "parts afterwards); group = keep each group's blanks "
                         "together on shared sheets; per-group = one separate "
                         "sheet per group, for when each team cuts their own")
    args = ap.parse_args(argv)

    if args.write_config:
        cfgmod.write_template(args.write_config)
        print(f"Wrote starter shop profile to {args.write_config}")
        return 0

    # Load the profile before the cards, so a card is cut in the shop's own
    # colours -- a test card in colours the file will never use teaches nothing.
    try:
        cfg = cfgmod.load(args.config)
    except Exception as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2

    if args.test_card:
        from . import testcard
        L.apply_overrides(cfg.get("layers"))
        out = testcard.build(args.test_card)
        print(f"Test card written to {os.path.abspath(out)}\n")
        print(testcard.HOW_TO_READ)
        return 0

    if args.mark_card:
        from . import markcard
        L.apply_overrides(cfg.get("layers"))
        out = markcard.build(args.mark_card)
        print(f"Marking card written to {os.path.abspath(out)}\n")
        print(markcard.HOW_TO_READ)
        return 0

    if not args.raw or not args.out:
        ap.error("--raw and --out are required (unless --write-config)")

    # An explicit flag beats the profile; the profile beats the built-in default.
    for name in ("sheet", "kerf", "mark", "nest_mode",
                 "part_gap", "sheet_margin"):
        if getattr(args, name, None) is None:
            setattr(args, name, cfg[name])
    if args.logo is None:
        args.logo = cfg.get("logo")

    if args.config:
        print(f"Profile {args.config}: {cfgmod.describe(cfg)}\n")

    try:
        sw, sh = (float(v) for v in args.sheet.lower().split("x"))
    except Exception:
        print(f"Bad --sheet value: {args.sheet}", file=sys.stderr)
        return 2

    from .nest import set_spacing
    from .process import set_material
    set_spacing(args.part_gap, args.sheet_margin)
    set_material(cfg.get("material_thickness"))
    L.apply_overrides(cfg.get("layers"))

    from . import strokefont
    chosen = strokefont.set_font(cfg.get("font", "angular"))
    if chosen != str(cfg.get("font", "angular")).strip().lower():
        print(f"Unknown font {cfg.get('font')!r} -- using {chosen}.")

    # Line style per colour comes from the profile (Settings > Laser colours).
    # --letters / --stroke-pct override the group ID row for one run.
    if args.letters or args.stroke_pct is not None:
        over = {"style": args.letters or L.SCHEME["MARK_TEXT"].style}
        if args.stroke_pct is not None:
            over["stroke"] = args.stroke_pct
        L.apply_overrides({"MARK_TEXT": over})
    text_style, text_stroke = L.style_of("MARK_TEXT")
    strokefont.set_stroke(text_stroke / 100.0 if text_style == "filled" else 0.0)
    for role in L.FILLABLE:
        if L.style_of(role)[0] == "filled":
            print(f"{L.SCHEME[role].name}: {L.style_label(role)} -- written as "
                  f"solid shapes for ENGRAVING, not lines.")

    logo = load_logo(args.logo)
    os.makedirs(args.out, exist_ok=True)
    # Sheets and per-part files from the LAST build are removed first. Left in
    # place, a sheet from an earlier run sits beside the new ones and gets
    # listed -- and cut -- with them.
    for sub in ("sheets", "parts"):
        old = os.path.join(args.out, sub)
        if os.path.isdir(old):
            shutil.rmtree(old, ignore_errors=True)

    folders = sorted(
        d for d in os.listdir(args.raw)
        if os.path.isdir(os.path.join(args.raw, d))
    )
    # folder on disk -> what will actually be marked on the steel
    group_of = {}
    renamed, unreadable = [], []
    for d in folders:
        gid = canonical_group_id(d)
        if gid is None:
            group_of[d] = d
            unreadable.append(d)
        else:
            group_of[d] = gid
            if gid != d:
                renamed.append((d, gid))

    # Two folders reducing to the same group ID means that group's parts get
    # cut twice, both sets marked identically, and nobody can tell them apart
    # in the bin. Almost always one folder is a duplicate upload.
    collisions = {}
    for d, gid in group_of.items():
        collisions.setdefault(gid, []).append(d)
    collisions = {g: ds for g, ds in collisions.items() if len(ds) > 1}

    groups = folders
    if not groups:
        print(f"No group folders found in {args.raw}", file=sys.stderr)
        return 2

    roster_problems = []
    off_roster: set[str] = set()
    if args.roster:
        try:
            # Checked on the group IDs the folders reduce to -- the IDs that
            # would be marked -- not on the raw folder names, so a folder
            # called "MAAE-L1-G01_Kairouz" is not a false alarm.
            expected, roster_problems = crosscheck.roster_check(
                sorted(set(group_of[d] for d in groups if d not in unreadable)),
                args.roster)
        except Exception as e:
            print(f"Roster error: {e}", file=sys.stderr)
            return 2
        off_roster = {d for d in groups
                      if d not in unreadable and group_of[d] not in expected}
        if roster_problems:
            print("ROSTER PROBLEMS")
            for pr in roster_problems:
                print(f"   ! {pr}")
            print()

    # Folders that must NOT reach the sheet: a group ID that belongs to nobody
    # (off the roster) or no group ID at all. Their parts would be marked with
    # something no team will recognise, so they are left off and reported.
    left_off = set(unreadable) | off_roster
    if left_off:
        print("!!! LEFT OFF THE SHEET until fixed (their marks would be wrong):")
        for d in sorted(left_off):
            why = "no group ID in the folder name" if d in unreadable \
                else f"'{group_of[d]}' is not on roster.txt"
            print(f"   {d}   ({why})")
        print()

    results = []
    items: list[NestItem] = []
    unexpected_parts: list[tuple[str, list[str]]] = []
    missing_parts: list[tuple[str, list[str]]] = []

    if renamed:
        print("Folder names reduced to the group code for marking "
              "(student names are never marked):")
        for d, gid in renamed:
            print(f"   {d}  ->  {gid}")
        print()
    if collisions:
        print("!!! TWO FOLDERS MARK THE SAME GROUP -- that group would be cut "
              "twice and the duplicates are indistinguishable in the bin:")
        for gid, ds in sorted(collisions.items()):
            print(f"   {gid}")
            for d in sorted(ds):
                print(f"      {d}")
        print("!!! Delete the wrong folder before cutting.\n")

    if unreadable:
        print("!!! No group code found in these folder names:")
        for d in unreadable:
            print(f"   {d}")
        print("!!! Rename them and build again.\n")

    for folder in groups:
        if folder in left_off:
            continue
        gid = group_of[folder]
        gdir = os.path.join(args.raw, folder)
        files = sorted(f for f in os.listdir(gdir) if f.lower().endswith(".dxf"))
        seen = set()
        print(f"[{gid}] {len(files)} part file(s)")

        for fn in files:
            part_name = os.path.splitext(fn)[0]
            seen.add(part_name.strip().lower())
            res, prims = process_part(
                os.path.join(gdir, fn), gid, part_name, (sw, sh), logo,
                mark_style=args.mark, kerf=args.kerf,
            )
            results.append(res)

            status = "OK " if res.ok else "FAIL"
            note = ""
            if res.errors:
                note = " | " + res.errors[0]
            elif res.warnings:
                note = " | " + res.warnings[0]
            mark = (f"mark={res.mark_height:4.2f}mm" if res.mark_placed
                    else "mark=NONE    ")
            print(f"   {status} {part_name:<14} "
                  f"{res.width:6.1f} x {res.height:6.1f} mm  "
                  f"holes={res.n_holes:<3} bends={res.n_bends:<3} "
                  f"{mark}{note}")

            if not res.ok or prims is None:
                continue

            out_part = os.path.join(args.out, "parts", gid, fn)
            write_dxf(prims, out_part)

            for _ in range(qty_for(part_name)):
                items.append(make_item(gid, part_name, prims))

        missing = [EXPECTED_LABELS[i] for i, alts in enumerate(EXPECTED_SETS)
                   if not (alts & seen)]
        if missing:
            print(f"   ! MISSING PARTS: {', '.join(missing)}")
            missing_parts.append((gid, missing))

        # Anything beyond the six. Usually a student who modelled a PURCHASED
        # part (gear, knob, rivet nut) as sheet metal, so Fusion gave it a flat
        # pattern and exported it. It is cut either way -- the pipeline
        # transcribes rather than corrects -- but it is an extra blank in the
        # bucket that nobody is expecting, so it has to be visible here.
        known = set().union(*EXPECTED_SETS)
        extra = sorted(n for n in seen if n not in known)
        if extra:
            print(f"   ! UNEXPECTED PARTS (will still be cut): {', '.join(extra)}")
            unexpected_parts.append((gid, extra))

    # Cohort comparison is assessment, not production. The blanks are cut
    # exactly as submitted either way, so this stays off unless asked for.
    cohort = crosscheck.check(results) if args.marking_annex else None

    sheets = []
    if not args.no_nest and items:
        sheets = nest(items, sw, sh, mode=args.nest_mode)
        for s in sheets:
            prims = []
            for it in s.items:
                prims.extend(placed_primitives(it))
            # Sheet outline for reference only.
            prims.append((L.SCHEME["REF_FRAME"].name,
                          [(0, 0), (sw, 0), (sw, sh), (0, sh), (0, 0)]))
            stem = (s.group_id if s.group_id else f"sheet_{s.index + 1:02d}")
            write_dxf(prims, os.path.join(args.out, "sheets", stem + ".dxf"))
            # A look-at-it copy beside the cut-it copy. See preview.py.
            try:
                from . import preview
                preview.build(prims, os.path.join(args.out, "sheets", stem + ".svg"),
                              sw, sh)
            except Exception as e:
                print(f"   (no preview for {stem}: {e})")
        print(f"\nNested {len(items)} blanks onto {len(sheets)} sheet(s) "
              f"({sw:.0f} x {sh:.0f} mm, mode={args.nest_mode})")
        for s in sheets:
            print(f"   sheet {s.index + 1:02d}: {len(s.items):3d} parts, "
                  f"{s.utilisation() * 100:4.1f}% bbox utilisation")

    if args.kerf > 0:
        print(f"\n*** Geometry is PRE-COMPENSATED for a {args.kerf:.2f} mm kerf.")
        print("*** Machine-side cut compensation MUST be switched off.")
    _write_reports(args.out, results, sheets, (sw, sh), args.kerf,
                   cohort, roster_problems, args.marking_annex,
                   unexpected_parts, bool(cfg.get("_layers_from_profile")),
                   missing_parts, collisions, unreadable, args.mark)

    if not cfg.get("_layers_from_profile"):
        print("\n*** The laser colours are NOT marked as checked on this machine.")
        print("*** Cut the test card on scrap, then tick 'These colours are checked'")
        print("*** on the Settings page.")

    for bad, msg in L.colour_clashes():
        if bad:
            print(f"\n!!! COLOUR CLASH: {msg}")

    if missing_parts:
        n = sum(len(m) for _g, m in missing_parts)
        print(f"\n!!! {n} MISSING PART(S) across {len(missing_parts)} group(s) "
              f"-- see report.md")

    if roster_problems:
        print(f"\n!!! {len(roster_problems)} ROSTER PROBLEM(S) -- see report.md")

    n_fail = sum(1 for r in results if not r.ok)
    n_warn = sum(1 for r in results if r.ok and r.warnings)
    print(f"\n{len(results)} blanks processed | {n_fail} could not be cut | "
          f"{n_warn} with cut warnings")
    print("Every blank is cut exactly as submitted.")
    print(f"Reports written to {os.path.join(args.out, 'report.md')}")
    # 3 = the sheets were built but something must be fixed before cutting
    # (groups left off, or two folders marking the same group). The app and
    # the .bat say so instead of "take these to the laser".
    if left_off or collisions:
        print("\n*** NOT READY TO CUT: fix the problems marked !!! above "
              "(and in report.md), then build again.")
        return 3
    return 1 if n_fail else 0


def _write_marking(out, results, roster_problems):
    """
    Per-group marking annex.

    Parts are cut exactly as submitted -- this file is the record of HOW each
    group's model differed from the class, so the five TAs can mark from
    evidence rather than by eye. One row per part, straight into Excel.
    """
    from collections import defaultdict

    rows = defaultdict(list)
    for r in results:
        rows[r.group_id].append(r)

    bad_ids = set()
    for pr in (roster_problems or []):
        for tok in pr.replace("'", " ").split():
            if "-" in tok and any(c.isdigit() for c in tok):
                bad_ids.add(tok.upper())

    with open(os.path.join(out, "marking.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["group", "roster_ok", "parts_submitted", "part", "cut",
                    "width_mm", "height_mm", "class_w", "class_h",
                    "size_dev_pct", "holes", "class_holes", "holes_delta",
                    "bends", "class_bends", "bends_delta", "observations"])
        for gid in sorted(rows):
            parts = rows[gid]
            submitted = len(parts)
            for r in sorted(parts, key=lambda x: x.part_name):
                d = r.deviation or {}
                w.writerow([
                    gid,
                    "no" if gid.upper() in bad_ids else "yes",
                    submitted,
                    r.part_name,
                    "yes" if r.ok else "NO",
                    f"{r.width:.2f}", f"{r.height:.2f}",
                    d.get("median_w", ""), d.get("median_h", ""),
                    d.get("size_dev_pct", ""),
                    r.n_holes, d.get("class_holes", ""), d.get("holes_delta", ""),
                    r.n_bends, d.get("class_bends", ""), d.get("bends_delta", ""),
                    " ; ".join(r.notes),
                ])


def _write_reports(out, results, sheets, sheet_size, kerf=0.0,
                   cohort=None, roster_problems=None, marking_annex=False,
                   unexpected_parts=None, layers_set=True,
                   missing_parts=None, collisions=None, unreadable=None,
                   mark_style=None):
    os.makedirs(out, exist_ok=True)

    with open(os.path.join(out, "report.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["group", "part", "status", "width_mm", "height_mm",
                    "holes", "bends", "cut_length_mm", "marked", "mark_mm",
                    "mark_text", "errors", "warnings", "observations"])
        for r in results:
            w.writerow([
                r.group_id, r.part_name, "OK" if r.ok else "FAIL",
                f"{r.width:.2f}", f"{r.height:.2f}", r.n_holes, r.n_bends,
                f"{r.cut_length:.1f}", "yes" if r.mark_placed else "no",
                f"{r.mark_height:.2f}", getattr(r, "mark_shown", ""),
                " ; ".join(r.errors), " ; ".join(r.warnings),
                " ; ".join(r.notes),
            ])

    failed = [r for r in results if not r.ok]
    warned = [r for r in results if r.ok and r.warnings]
    total_cut = sum(r.cut_length for r in results if r.ok)

    lines = []
    lines.append("# Landing gear production run\n")
    lines.append(f"Material: {MATERIAL_THICKNESS} mm (16 ga) steel  ")
    lines.append(f"Stock sheet: {sheet_size[0]:.0f} x {sheet_size[1]:.0f} mm\n")
    lines.append(f"- Parts processed: **{len(results)}**")
    lines.append(f"- Failed validation: **{len(failed)}**")
    lines.append(f"- Passed with warnings: **{len(warned)}**")
    lines.append(f"- Total cut length: **{total_cut / 1000:.1f} m**")
    if sheets:
        lines.append(f"- Sheets required: **{len(sheets)}**")
    lines.append("")

    if not layers_set:
        lines.append("> ## \u26a0 THE COLOURS HAVE NOT BEEN CHECKED AGAINST THIS MACHINE\n"
                     ">\n"
                     "> Nobody has ticked **\"These colours are checked on the machine "
                     "with the test card\"** on the Settings page, so the colours below "
                     "are not known to be right for the laser you are about to use. A "
                     "colour is only a label -- what it does is whatever that machine's "
                     "profile says it does.\n"
                     ">\n"
                     "> Cut a test card on scrap first (**Settings -> Test card** in the app, "
                     "or `--test-card`), see which colours cut and which mark, then set them "
                     "on the Settings page and tick that box. This warning disappears once "
                     "it is ticked.\n"
                     ">\n"
                     "> The specific way this goes wrong: a mark colour mapped to a cut "
                     "process slices every part along its bend lines, and a disabled colour "
                     "means no group IDs at all.\n")
    # Some blanks are too cramped for the ID spelled out, so the marker gets
    # the key for whatever short forms actually ended up on the steel.
    packed = re.compile(r"\b[MA]\d-\d{2}\b")
    squeezed = sorted({r.group_id for r in results
                       if getattr(r, "mark_shown", "") and packed.search(r.mark_shown)})
    initial = sorted({r.group_id for r in results
                      if getattr(r, "mark_shown", "")
                      and re.search(r"\b[MA]-L\d\b", r.mark_shown)})

    if mark_style in ("short", "short-code") or squeezed or initial:
        lines.append("## How to read a mark\n")
        if mark_style in ("short", "short-code"):
            lines.append("Marks on this batch use the short form, so the letters "
                         "could be drawn larger on small blanks:\n")
        else:
            lines.append("Most blanks carry the ID spelled out. A blank with no "
                         "room for that gets a shorter form rather than a smaller "
                         "mark — nothing is dropped, only compressed:\n")
        lines.append("| On the steel | Means |")
        lines.append("|---|---|")
        lines.append("| `M` | MAAE |")
        lines.append("| `A` | AERO |")
        lines.append("| first digit | lab section, so `M1` is MAAE L1 |")
        lines.append("| after the dash | group number |")
        lines.append("")
        lines.append("So `MAAE-L1-G07 CHS`, `M-L1-G07 CHS` and `M1-07 CHS` are "
                     "the same group's chassis, written in as much room as that "
                     "blank had. A blank whose ID does not fit that shape at all "
                     "is marked with its full ID, never with a guess.\n")
        if squeezed:
            lines.append("Fully compressed on at least one blank (usually the "
                         "rivet nut): %s\n"
                         % ", ".join(f"`{g}`" for g in squeezed))
        lines.append("`report.csv` has the exact text marked on every blank, in "
                     "the `mark_text` column.\n")

    black = sorted(s.name for s in L.SCHEME.values() if s.rgb == (0, 0, 0))
    if black:
        lines.append("> **%s set to pure black, 0,0,0.** That is the one value a "
                     "DXF cannot state unambiguously: the number for pure black "
                     "is zero, and a reader that checks 'was a colour set?' "
                     "before using it reads zero as 'no' and substitutes its own "
                     "black instead. CorelDRAW's is 26,26,26 (`#1A1A1A`), which "
                     "is what the files that already work at the shop arrive as. "
                     "Set that value on the Settings page rather than 0,0,0.\n"
                     % " and ".join(f"`{b}`" for b in black))

    clashes = L.colour_clashes()
    dangerous = [m for bad, m in clashes if bad]
    if dangerous:
        lines.append("> ## ⚠ TWO ROLES SHARE ONE COLOUR AND MUST NOT\n>")
        for m in dangerous:
            lines.append(f"> - {m}")
        lines.append(">\n> Fix this on the Settings page before cutting. The "
                     "machine has one setting per colour; it cannot run two "
                     "processes from one.\n")

    lines.append("## Machine setup\n")
    lines.append("Map layers to processes once, then save as a template. "
                 "Run in this order:\n")
    lines.append("| Order | Layer | Colour (ACI / RGB) | Process | Lines | Cut side |")
    lines.append("|---|---|---|---|---|---|")
    for spec in L.process_table():
        proc = "**do not output**" if spec.process == "reference" else spec.process
        rgb = "%d,%d,%d" % spec.rgb
        side = f"**{spec.cut_side}**" if spec.cut_side != "-" else "-"
        role = next((k for k, v in L.SCHEME.items() if v is spec), "")
        lines.append(f"| {spec.order if spec.order < 90 else '-'} | `{spec.name}` "
                     f"| {spec.aci} / {rgb} | {proc} | {L.style_label(role)} "
                     f"| {side} |")
    lines.append("")
    if kerf > 0:
        lines.append(f"> **Geometry is PRE-COMPENSATED for a {kerf:.2f} mm kerf.** "
                     f"Outer profiles have been moved outward and holes inward by "
                     f"{kerf/2:.3f} mm. Machine-side cut compensation MUST be "
                     f"switched off, or parts will be a full kerf out.\n")
    else:
        lines.append("> Geometry is NOMINAL. Set cut compensation on the machine "
                     "using the **Cut side** column: the kerf must fall in the "
                     "scrap, so the beam runs outside the line on the outer "
                     "profile and inside it on holes. Uncompensated, parts finish "
                     "half a kerf undersize and every hole half a kerf oversize.\n")
    filled = [r for r in L.FILLABLE if L.style_of(r)[0] == "filled"]
    if filled:
        names = " and ".join(f"`{L.SCHEME[r].name}` ({L.style_label(r)})"
                             for r in filled)
        lines.append(f"> **{names} {'is' if len(filled) == 1 else 'are'} "
                     f"FILLED, for engraving.** Each is a solid shape with no "
                     f"outline, so the laser should ENGRAVE it (the engraving / "
                     f"image settings), not follow it. Every other colour is a "
                     f"hairline and runs as before. If the filled shapes arrive "
                     f"in CorelDRAW as empty outlines or not at all, stop: the "
                     f"fill did not import.\n")
    lines.append("Marks run before cuts so the blank is still fully supported "
                 "by the sheet; interior cuts run before the outer profile so "
                 "the part cannot shift once it is free.\n")

    if roster_problems:
        lines.append("## Roster problems -- FIX BEFORE CUTTING\n")
        lines.append("Groups not on the roster were LEFT OFF the sheet: their "
                     "parts would be marked with a group ID that belongs to "
                     "nobody. Fix the names and build again.\n")
        for pr in roster_problems:
            lines.append(f"- {pr}")
        lines.append("")

    if collisions:
        lines.append("## Two folders mark the same group -- FIX BEFORE CUTTING\n")
        lines.append("Each of these groups would have its seven blanks cut "
                     "twice, and the two sets carry identical marks, so nobody "
                     "can tell them apart in the bin. Usually one folder is a "
                     "duplicate upload. Delete the wrong one and re-run.\n")
        for gid, ds in sorted(collisions.items()):
            lines.append(f"- **{gid}** \u2014 " + ", ".join(f"`{d}`" for d in sorted(ds)))
        lines.append("")

    if unreadable:
        lines.append("## Folder names with no group code -- FIX BEFORE CUTTING\n")
        lines.append("These will be laser-marked exactly as the folder is "
                     "named, which is not a group anyone can look up. Rename "
                     "them to the group code and re-run.\n")
        for d in sorted(unreadable):
            lines.append(f"- `{d}`")
        lines.append("")

    if missing_parts:
        lines.append("## Groups missing a part -- FIX BEFORE CUTTING\n")
        lines.append("These groups have no flat pattern for a part the brief "
                     "requires, so nothing for it will be cut and they cannot "
                     "assemble a working unit.\n")
        lines.append("Two causes, and the export log says which: the component "
                     "is named something the exporter did not recognise "
                     "(`NAME` row), or it was modelled as a folded solid rather "
                     "than sheet metal, so Fusion could not flatten it at all "
                     "(`SKIP` row). The second is a graded failure -- the brief "
                     "requires \"real flat patterns, not just folded solids\" "
                     "(\u00a74.2).\n")
        lines.append("| Group | Missing |")
        lines.append("|---|---|")
        for gid, miss in missing_parts:
            lines.append(f"| `{gid}` | {', '.join(miss)} |")
        lines.append("")

    if unexpected_parts:
        lines.append("## Unexpected parts on the sheet\n")
        lines.append("These groups submitted a flat pattern that is not one of "
                     "the six formed parts. The usual cause is a PURCHASED part "
                     "(bevel gear, hand knob, wheel, rivet nut, fastener) "
                     "modelled as sheet metal, which gives it a flat pattern "
                     "Fusion will happily export.\n")
        lines.append("They are cut anyway -- the pipeline transcribes rather "
                     "than corrects -- so expect these as extra blanks in the "
                     "bucket. Section 2 of the brief says the purchased parts "
                     "are supplied and must not be reverse engineered, so each "
                     "one here is worth a word to the group.\n")
        for gid, extra in unexpected_parts:
            lines.append(f"- **{gid}**: {', '.join(extra)}")
        lines.append("")

    if cohort:
        lines.append("## Cohort consistency\n")
        lines.append("Every group models the same landing gear, so each part "
                     "is checked against the class median. Outliers are listed "
                     "under Warnings below.\n")
        lines.append("| Part | Groups | Median size (mm) | Holes | Bends | Outliers |")
        lines.append("|---|---|---|---|---|---|")
        lines.extend(cohort)
        lines.append("")

    if failed:
        lines.append("## Failed parts -- must be fixed before cutting\n")
        for r in failed:
            lines.append(f"- **{r.group_id} / {r.part_name}**: "
                         + "; ".join(r.errors))
        lines.append("")

    noted = [r for r in results if r.notes]
    if noted:
        lines.append("## Marking observations\n")
        lines.append("Every blank below is cut **exactly as submitted** -- none "
                     "of this is corrected or blocked. It is recorded so the "
                     "marking team can see how each group's model differed from "
                     "the class. Full detail in `marking.csv`.\n")
        by_group = {}
        for r in noted:
            by_group.setdefault(r.group_id, []).append(r)
        for g in sorted(by_group):
            lines.append(f"**{g}**")
            for r in sorted(by_group[g], key=lambda x: x.part_name):
                lines.append(f"- {r.part_name}: " + "; ".join(r.notes))
            lines.append("")

    if warned:
        lines.append("## Warnings\n")
        for r in warned:
            lines.append(f"- **{r.group_id} / {r.part_name}**: "
                         + "; ".join(r.warnings))
        lines.append("")

    if sheets:
        lines.append("## Sheet contents\n")
        for s in sheets:
            lines.append(f"### Sheet {s.index + 1:02d} "
                         f"({len(s.items)} parts, "
                         f"{s.utilisation() * 100:.1f}% utilisation)\n")
            by_group = {}
            for it in s.items:
                by_group.setdefault(it.group_id, []).append(it.part_name)
            for g in sorted(by_group):
                lines.append(f"- `{g}`: {', '.join(sorted(by_group[g]))}")
            lines.append("")

    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    if marking_annex:
        _write_marking(out, results, roster_problems)

    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({
            "material_thickness_mm": MATERIAL_THICKNESS,
            "sheet_mm": list(sheet_size),
            "sheets": len(sheets),
            "parts": [
                {
                    "group": r.group_id, "part": r.part_name, "ok": r.ok,
                    "w": round(r.width, 2), "h": round(r.height, 2),
                    "holes": r.n_holes, "bends": r.n_bends,
                    "marked": r.mark_placed,
                    "mark_mm": round(r.mark_height, 2),
                    "errors": r.errors, "warnings": r.warnings,
                }
                for r in results
            ],
        }, f, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())
