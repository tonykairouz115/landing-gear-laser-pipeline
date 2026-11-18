r"""
prepare_candidates.py -- step 1 of the group re-check. Separate from the
laser pipeline: it only READS (your export log, your submissions folder) and
writes one new folder, "candidates". Nothing in the pipeline is changed.

For every group that did not export six good parts, it copies EVERY member's
submission into  candidates\  named

    AERO-L1-G03__p2__Smith-Kodi.f3d     (group, draw order, student)

using the same draw order as selection_log.csv (seed 2026).

    python prepare_candidates.py --export-log "C:\MAAE2001 with f3z\lgpipe\raw\_export_log.csv"
                                 --submissions "C:\MAAE2001 with f3z\lgpipe\submissions"
                                 --roster "C:\MAAE2001 with f3z\lgpipe\roster.txt"

Step 2: upload  candidates\  to a NEW Fusion project and run CheckDesigns.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import select_designs as S  # noqa: E402

CODES = ["CHS", "LCV", "RCV", "NUT", "LEG", "STB"]
PART_NAMES = {"CHS": "Chassie", "LCV": "Left Cover", "RCV": "Right Cover",
              "NUT": "Nut", "LEG": "Leg", "STB": "Stabalizer"}
BAD_STATUSES = {"MISSING", "FAIL", "NAME", "DUPLICATE"}


def read_log(path):
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        return list(csv.DictReader(f))


def group_status(rows):
    """{folder: {"ok": set(codes), "problems": [text]}} from an export log."""
    out = {}
    for r in rows:
        g = (r.get("group") or "").strip()
        if not g or g == "-":
            continue
        st = out.setdefault(g, {"ok": set(), "problems": []})
        comp, status, detail = r.get("component", ""), r.get("status", ""), r.get("detail", "")
        if status == "OK":
            code = _code_of(comp)
            if code:
                st["ok"].add(code)
        elif status == "MISSING":
            st["problems"].append("%s missing (not sheet metal, or named wrong)" % comp)
        elif status == "NAME":
            st["problems"].append("part named '%s' (not a brief name)" % comp)
        elif status == "FAIL":
            st["problems"].append("%s: Fusion export failed (%s)" % (comp, detail[:60]))
    return out


_ACCEPTED = {
    "CHS": ["chassie", "chassis", "chs"],
    "LCV": ["left cover", "leftcover", "cover left", "lcv"],
    "RCV": ["right cover", "rightcover", "cover right", "rcv"],
    "NUT": ["nut", "nut clip", "captive nut", "captive nut clip"],
    "LEG": ["leg"],
    "STB": ["stabalizer", "stabilizer", "stabaliser", "stabiliser",
            "stabalizer link", "stabilizer link", "stabiliser link", "stb"],
}


def _code_of(name):
    n = re.sub(r"\s*\(\d+\)\s*$", "", (name or "").strip().lower())
    n = re.sub(r"\s+v\d+$", "", n)
    n = re.sub(r"[\s_-]+", " ", n).strip()
    n = re.sub(r"\s+\d+$", "", n)
    for code, names in _ACCEPTED.items():
        if n in names:
            return code
    return None


# ------------------------------------------------------------------ prepare --

def prepare(args):
    log = read_log(args.export_log)
    status = group_status(log)
    roster = S.load_roster(args.roster)
    groups = sorted(roster) if roster else sorted(status)
    bad = []
    for g in groups:
        st = status.get(g)
        if st is None:
            bad.append(g)                      # not exported at all
        elif not set(CODES) <= st["ok"]:
            # Missing a part. A group that HAS all six but also exported an
            # extra, oddly named file is complete: 'pick' clears the extra.
            bad.append(g)
    print("Groups to re-check: %d" % len(bad))
    for g in bad:
        print("   ", g)

    found, _unparsed = S.find_submissions(args.submissions)
    by_group = {}
    for gid, path, stem in found:
        by_group.setdefault(gid, []).append((path, stem))

    os.makedirs(args.out, exist_ok=True)
    for f in os.listdir(args.out):
        if f.lower().endswith((".f3d", ".f3z")):
            os.remove(os.path.join(args.out, f))

    rows = [["group", "pick", "student", "submitted", "copied_as", "original_file"]]
    for g in bad:
        cands, _sup = S.latest_per_student(by_group.get(g, []))
        if not cands:
            rows.append([g, "", "", "", "", "no submissions"])
            continue
        for rank, (path, stem) in enumerate(S.draw_order(cands, g, args.seed), 1):
            who, when = S._who(path, stem)
            safe = re.sub(r"[^A-Za-z0-9]+", "-", who or "unknown").strip("-")
            ext = os.path.splitext(path)[1].lower()
            name = "%s__p%d__%s%s" % (g, rank, safe, ext)
            shutil.copy2(path, os.path.join(args.out, name))
            rows.append([g, rank, who, when, name, os.path.basename(path)])
    with open(os.path.join(args.out, "candidates.csv"), "w", newline="",
              encoding="utf-8-sig") as f:
        csv.writer(f).writerows(rows)
    n = sum(1 for r in rows[1:] if r[4])
    print("\nCopied %d candidate file(s) to %s" % (n, os.path.abspath(args.out)))
    print("Next: upload that folder to a NEW Fusion project, open that project, "
          "and run the CheckDesigns script.")
    return 0


def _free(path):
    if not os.path.exists(path):
        return path
    n = 2
    while os.path.exists("%s (%d)" % (path, n)):
        n += 1
    return "%s (%d)" % (path, n)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p = ap
    p.add_argument("--export-log", default=os.path.join("raw", "_export_log.csv"))
    p.add_argument("--submissions", default="submissions")
    p.add_argument("--roster", default="roster.txt")
    p.add_argument("--seed", default=S.DEFAULT_SEED)
    p.add_argument("--out", default="candidates")
    args = ap.parse_args(argv)
    return prepare(args)


if __name__ == "__main__":
    sys.exit(main())
