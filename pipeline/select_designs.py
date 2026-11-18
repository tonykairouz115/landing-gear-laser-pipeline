"""
select_designs.py -- pick one submission per group, at random, defensibly.

WHY THIS EXISTS
---------------
The project brief says one design per group is "selected at random" and that
design is the one marked and manufactured. That is a graded decision. Doing it
by hand across a full cohort leaves no record of how any choice was made, and the
first student who asks "why wasn't mine picked?" has no answer to point at.

So the draw is done here instead:

  * REPRODUCIBLE. The same submissions and the same seed always produce the
    same winners. Re-run it a month later in front of the student and the
    identical name comes out.
  * BLIND. The choice depends only on the filename, never on file size, date,
    or who submitted first. No ordering advantage exists.
  * LOGGED. Every candidate considered, the winner, the seed and a timestamp
    go into selection_log.csv.
  * PER-GROUP SEEDED. Each group's draw is seeded from (seed, group id), so a
    late submission in G12 changes G12's winner and nothing else. A single
    global shuffle would reshuffle every group -- which would mean a late
    submission silently changed some other group's outcome.

WHAT IT PRODUCES
----------------
    selected/          the winners, renamed MAAE-L2-G07.f3d (or .f3z)
                       nothing else -- upload this whole folder to Fusion
    drawings/          the winner's PDFs etc., one subfolder per group
    selection_log.csv  one row per submission: group, draw order (1 = made,
                       2, 3 ... = backups if it fails the check), student,
                       time, file
    selection_report.txt  human-readable summary and problem list

USAGE
    python select_designs.py --submissions submissions --seed 2026

The default seed is fixed in this file. Record it. Do not change it after the
draw has been announced -- changing the seed changes every winner.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import os
import random
import re
import shutil
import sys

# Fixed default. Changing this changes every winner, so it lives in the source
# rather than being generated fresh on each run.
DEFAULT_SEED = "2026"

SUBMISSION_EXT = ".f3d"
# A student whose parts are linked from other designs can only export a .f3z
# (a Fusion archive). It holds the same sheet metal, so it is a valid entry in
# the draw; BatchExportProject handles the links when it is uploaded.
SUBMISSION_EXTS = (".f3d", ".f3z")

# Course codes and lab-section shape. Change these if the course is renumbered.
DEPTS = ("MAAE", "AERO")
SECTION = r"L\s*\d+"

# The tolerant pattern: the real convention, but accepting whatever separator
# the student actually typed, and "Group" as well as "G".
#
#   MAAE-L1-G07   MAAE_L1_G07   MAAE L1 G07   MAAE.L1.Group07   maae l1 g7
#
# It is anchored on the department code, which is what makes it safe to search
# anywhere in a name. A looser pattern that allowed spaces as separators would
# happily read "... 1030 AM - MAAE L1 G07" as the group "AM-MAAE-L1-G07",
# because "AM" is a plausible token followed by a plausible separator. Naming
# the departments removes that whole class of false positive.
SEP = r"[-_.\s]*"
STRICT_RE = re.compile(
    rf"({'|'.join(DEPTS)}){SEP}({SECTION}){SEP}G(?:ROUP)?{SEP}0*(\d+)",
    re.IGNORECASE)

# Finds the group ID ANYWHERE in a name, not just at the start.
#
# This is deliberate. A Brightspace bulk download does not hand you the file
# the student uploaded -- it prefixes an ID number, the student's name and a
# timestamp, so what lands on disk looks more like:
#
#   123456-78901 - Tony Kairouz - Sep 14, 2026 1030 AM - MAAE-L2-G07_Tony.f3d
#
# Anchoring to the start of the name would fail on every single file in a real
# download. Searching finds the group ID wherever the platform buried it.
#
# The pattern needs a "-G<digits>" preceded by at least one hyphenated token,
# which is specific enough that submission IDs, dates and student names do not
# accidentally match it.
GROUP_RE = re.compile(r"([A-Za-z]+[A-Za-z0-9]*(?:-[A-Za-z0-9]+)*?-[Gg]\s*0*(\d+))")

# Group numbers are normalised to two digits so that G7, G07 and G007 all land
# in the same bucket. Without this a student who dropped the leading zero would
# form a phantom one-person "group" and the real group would be short a member,
# both reported as problems that are really the same file.
GROUP_PAD = 2


# --------------------------------------------------------------- discovery --

def group_id_from(text: str) -> str | None:
    """
    Group ID from a filename stem or folder name, or None if none is present.

    Normalises case and zero-padding, so every spelling of the same group
    converges on one canonical ID:

        MAAE-L2-G07_Kairouz_Tony            -> MAAE-L2-G07
        1234-567 - T Kairouz - maae-l2-g7   -> MAAE-L2-G07
        AERO-L1-G3                          -> AERO-L1-G03
    """
    text = (text or "").strip()
    # Brightspace calls the MAAE groups "MAAE2001 L1 G 1", and students copy
    # that. The course number is not part of the ID: MAAE2001 -> MAAE.
    # Lecture letters too: "AERO2001B-L1-G11" -> AERO-L1-G11.
    text = re.sub(r"(?i)\b(MAAE|AERO)[\s_-]*2001(?:[AB](?![A-Za-z0-9]))?(?!\d)",
                  r"\1", text)

    # First choice: the real convention, however the student punctuated it.
    m = STRICT_RE.search(text)
    if m:
        dept, section, number = m.group(1), m.group(2), m.group(3)
        section = re.sub(r"\s+", "", section).upper()
        section = "L%d" % int(section[1:])          # L01 -> L1
        return f"{dept.upper()}-{section}-G{int(number):0{GROUP_PAD}d}"

    # Fallback: a hyphenated code with some other department prefix, so the
    # tool still works if the course is renumbered and nobody updates DEPTS.
    # Hyphen-only here on purpose -- see the note on STRICT_RE.
    m = GROUP_RE.search(text)
    if not m:
        return None
    whole, number = m.group(1), m.group(2)
    head = whole[:whole.upper().rindex("-G")]
    return f"{head.upper()}-G{int(number):0{GROUP_PAD}d}"


def find_submissions(root: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    """
    Walk `root` for .f3d files.

    Returns (good, unparsed) where good is a list of
    (group_id, absolute path, filename stem) and unparsed is a list of paths
    whose filename gave no group ID.

    Recurses, because Brightspace bulk downloads often put each student in
    their own subfolder.
    """
    good: list[tuple[str, str, str]] = []
    unparsed: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if not fn.lower().endswith(SUBMISSION_EXTS):
                continue
            stem = os.path.splitext(fn)[0]
            path = os.path.join(dirpath, fn)

            # Filename first. If the student left the ID out of the filename,
            # fall back to the folder they were downloaded into -- Brightspace
            # names those after the student and submission, and some course
            # setups put the group ID there instead.
            gid = group_id_from(stem)
            if not gid:
                gid = group_id_from(os.path.basename(dirpath))

            if gid:
                good.append((gid, path, stem))
            else:
                unparsed.append(path)
    return good, unparsed


# Brightspace's bulk download names every file (or the folder it sits in)
#     "<submission id>-<file id> - <Student Name> - <Mon D, YYYY HMM AM> - <file>"
# which is enough to tell who submitted it and when.
BRIGHTSPACE_RE = re.compile(
    # Brightspace writes "Last First 101234567- Nov 13, 2026 1126 PM": the
    # student number is glued to the dash, so the space before it is optional.
    r"^\s*\d+-\d+\s+-\s+(?P<who>.+?)\s*-\s+"
    r"(?P<when>[A-Za-z]{3,9}\.?\s+\d{1,2},\s+\d{4}\s+\d{1,2}:?\d{2}\s*[AaPp][Mm])"
    # A file name continues with " - <student's file>"; a submission FOLDER
    # name ends right after the time. Both are accepted.
    r"(?:\s+-\s+|\s*$)")


def submitted_by(stem: str, dirpath: str = ""):
    """
    (student name, submitted-at datetime) from a Brightspace file or folder
    name, or None when the name does not carry them (files added by hand).
    """
    for text in (stem, os.path.basename(dirpath or "")):
        m = BRIGHTSPACE_RE.match(text or "")
        if not m:
            continue
        when = re.sub(r"\s+", " ", m.group("when").replace(".", "")).strip()
        when = re.sub(r"(\d{1,2}):?(\d{2}) ?([AaPp][Mm])$",
                      lambda k: f"{int(k.group(1)):02d}{k.group(2)} {k.group(3).upper()}",
                      when)
        for fmt in ("%b %d, %Y %I%M %p", "%B %d, %Y %I%M %p"):
            try:
                return (m.group("who").strip().lower(),
                        datetime.datetime.strptime(when, fmt))
            except ValueError:
                continue
    return None


def latest_per_student(cands: list[tuple[str, str]]):
    """
    Keep one file per student: the most recent submission.

    Brightspace keeps every attempt unless the assignment is set to keep only
    the latest, so a student who uploaded twice would otherwise hold two
    tickets in the draw -- and the superseded version could win. Files whose
    names carry no student and time (added by hand) are all kept.

    Returns (kept, dropped) where dropped is [(path, stem, kept_stem)].
    """
    newest: dict[str, tuple[datetime.datetime, str, str, int]] = {}
    kept_plain = []
    for path, stem in cands:
        meta = submitted_by(stem, os.path.dirname(path))
        if meta is None:
            kept_plain.append((path, stem))
            continue
        who, when = meta
        # Same submission, several Fusion files (the assembly plus each part
        # uploaded separately): the largest file is the assembly, since it
        # holds every part.
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        prev = newest.get(who)
        if prev is None or (when, size, stem) > (prev[0], prev[3], prev[2]):
            newest[who] = (when, path, stem, size)
    kept = kept_plain + [(p, st) for _w, p, st, _z in newest.values()]
    kept_paths = {p for p, _s in kept}
    dropped = []
    for path, stem in cands:
        if path not in kept_paths:
            meta = submitted_by(stem, os.path.dirname(path))
            winner = newest[meta[0]][2] if meta else ""
            dropped.append((path, stem, winner))
    return kept, dropped


def _core(stem: str) -> str:
    """
    The student's own filename, with any platform prefix stripped.

    Brightspace prepends "<id> - <Student Name> - <timestamp> - " to every
    file, and the id differs per file, so two files from the SAME submission
    share no common prefix. What they do share is the tail -- the name the
    student actually typed. Comparing tails rather than whole names is what
    lets a student's drawings be matched to their model.
    """
    return stem.rsplit(" - ", 1)[-1].strip().lower()


def companions(f3d_path: str, stem: str) -> list[str]:
    """
    Other files belonging to the same submission -- the drawing PDFs.

    Three cases, because bulk downloads come in all of these shapes:

      1. Per-student subfolder. If this .f3d is the ONLY .f3d in its folder,
         everything else in that folder is part of the same submission.
      2. Flat folder, clean names. Anything whose stem starts with this one.
      3. Flat folder, Brightspace names. Anything whose STRIPPED name starts
         with this one's stripped name (see _core).
    """
    folder = os.path.dirname(f3d_path)
    try:
        entries = [e for e in os.listdir(folder)
                   if os.path.isfile(os.path.join(folder, e))]
    except OSError:
        return []

    f3ds = [e for e in entries if e.lower().endswith(SUBMISSION_EXTS)]
    sole_f3d = len(f3ds) == 1

    out = []
    for e in entries:
        p = os.path.join(folder, e)
        if os.path.abspath(p) == os.path.abspath(f3d_path):
            continue
        if e.lower().endswith(SUBMISSION_EXTS):
            continue                       # never copy another student's model
        other = os.path.splitext(e)[0]
        if (sole_f3d
                or other.startswith(stem)
                or _core(other).startswith(_core(stem))):
            out.append(p)
    return sorted(out)


# ----------------------------------------------------------------- roster ---

def load_roster(path: str | None) -> set[str] | None:
    """Expected group IDs, one per line. '#' comments and blanks ignored."""
    if not path:
        return None
    if not os.path.exists(path):
        raise FileNotFoundError(f"Roster not found: {path}")
    ids = set()
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                ids.add(line.upper())
    return ids


# ------------------------------------------------------------------ draw ----

def draw(candidates: list[tuple[str, str]], group_id: str, seed: str) -> tuple[str, str]:
    """
    Pick one (path, stem) for this group.

    Candidates are sorted by stem first, so the result never depends on the
    order the filesystem happened to return -- that is what makes the draw
    reproducible on a different machine.
    """
    ordered = sorted(candidates, key=lambda c: c[1].lower())
    rng = random.Random(f"{seed}|{group_id}")
    return ordered[rng.randrange(len(ordered))]


def draw_order(candidates: list[tuple[str, str]], group_id: str,
               seed: str) -> list[tuple[str, str]]:
    """
    Every candidate in draw order: the winner first (exactly the one draw()
    picks), then the backups, also in a random order from the same seed.

    If the winner turns out unusable (a part not in sheet metal, an
    incomplete assembly), the next name on this list is used instead. That
    keeps the fallback random and reproducible: nobody chooses it by hand.
    """
    ordered = sorted(candidates, key=lambda c: c[1].lower())
    rng = random.Random(f"{seed}|{group_id}")
    first = rng.randrange(len(ordered))
    rest = ordered[:first] + ordered[first + 1:]
    rng.shuffle(rest)
    return [ordered[first]] + rest


def _who(path: str, stem: str) -> tuple[str, str]:
    """(student name, submitted at) for the log, '' when the name has none."""
    info = submitted_by(stem, os.path.dirname(path))
    if not info:
        return "", ""
    name, when = info
    name = re.sub(r"\s*\d{6,}\s*$", "", name)      # drop the student number
    return name.title(), when.strftime("%Y-%m-%d %H:%M")


def _archive(path: str) -> str | None:
    """Move an existing file aside, stamped, before it is overwritten."""
    if not path or not os.path.exists(path):
        return None
    root, ext = os.path.splitext(path)
    stamp = datetime.datetime.fromtimestamp(
        os.path.getmtime(path)).strftime("%Y%m%d-%H%M%S")
    dest = f"{root}_{stamp}{ext}"
    n = 1
    while os.path.exists(dest):
        dest = f"{root}_{stamp}_{n}{ext}"
        n += 1
    try:
        os.replace(path, dest)
        return dest
    except OSError:
        return None


# ------------------------------------------------------------------ main ----

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Pick one .f3d submission per group, at random and on the "
                    "record, ready for the Fusion batch exporter.")
    ap.add_argument("--submissions", default="submissions",
                    help="folder of student .f3d files (searched recursively)")
    ap.add_argument("--out", default="selected",
                    help="folder for the winning .f3d files (upload this to Fusion)")
    ap.add_argument("--drawings", default="drawings",
                    help="folder for the winners' PDFs; '-' to skip copying them")
    ap.add_argument("--roster", default=None,
                    help="roster.txt of expected group IDs, for cross-checking")
    ap.add_argument("--seed", default=DEFAULT_SEED,
                    help=f"random seed (default {DEFAULT_SEED}); "
                         "changing it changes every winner")
    ap.add_argument("--expect", default="3-4",
                    help="expected submissions per group: a single number, or a "
                         "range like 3-4. Real cohorts are ragged -- a group of "
                         "three is normal, not a problem, and flagging it as one "
                         "trains you to ignore the problem list.")
    ap.add_argument("--log", default="selection_log.csv")
    ap.add_argument("--report", default="selection_report.txt")
    args = ap.parse_args(argv)

    try:
        parts = str(args.expect).replace(" ", "").split("-")
        lo_expect = int(parts[0])
        hi_expect = int(parts[-1])
        if lo_expect > hi_expect:
            lo_expect, hi_expect = hi_expect, lo_expect
    except ValueError:
        print(f"  [X] --expect must be a number or a range like 3-4, not {args.expect!r}")
        return 2

    if not os.path.isdir(args.submissions):
        print(f"  [X] No submissions folder: {args.submissions}")
        return 2

    found, unparsed = find_submissions(args.submissions)
    if not found and not unparsed:
        print(f"  [X] No .f3d or .f3z files under {args.submissions}")
        return 2

    # Bucket by group.
    by_group: dict[str, list[tuple[str, str]]] = {}
    for gid, path, stem in found:
        by_group.setdefault(gid, []).append((path, stem))

    roster = load_roster(args.roster)
    stamp = datetime.datetime.now().isoformat(timespec="seconds")

    os.makedirs(args.out, exist_ok=True)
    # Clear the previous draw's winners. A group whose winner was a .f3d last
    # time and a .f3z this time would otherwise have BOTH files in the folder
    # that is uploaded to Fusion, and be cut twice. They are copies: the
    # originals stay in the submissions folder, and the draw is reproducible.
    cleared = 0
    for old_f in os.listdir(args.out):
        old_p = os.path.join(args.out, old_f)
        if os.path.isfile(old_p) and old_f.lower().endswith(SUBMISSION_EXTS):
            os.remove(old_p)
            cleared += 1
    copy_drawings = args.drawings != "-"
    if copy_drawings:
        os.makedirs(args.drawings, exist_ok=True)

    # One row per submission. "pick" is the draw order: 1 is the design that
    # is manufactured, 2, 3 ... are the backups, in order, if it fails the
    # check. Sort or filter by group_id in Excel to see each group at a glance.
    rows = [["group_id", "pick", "student", "submitted", "picked",
             "candidate_file", "seed", "drawn_at"]]
    problems: list[str] = []
    notes: list[str] = []
    winners: list[tuple[str, str, list[str]]] = []

    for gid in sorted(by_group):
        cands, superseded = latest_per_student(by_group[gid])
        for path, stem, newer in superseded:
            notes.append(f"{gid}: an earlier upload was left out of the draw "
                         f"({stem}); the same student's newer one ({newer}) "
                         f"is in it instead")
            who, when = _who(path, stem)
            rows.append([gid, "superseded", who, when, "",
                         os.path.basename(path), args.seed, stamp])

        if roster is not None and gid not in roster:
            # A group ID that is not a real group (no lab section, a section
            # that does not exist, a typo in the course code) is not drawn.
            # Its files are listed in the log so nobody wonders where they went.
            notes.append(
                f"{gid}: not a group on the roster -- not drawn "
                f"({len(cands)} file(s), naming convention not followed)")
            for path, stem in cands:
                who, when = _who(path, stem)
                rows.append([gid, "not drawn: group ID not on roster", who, when,
                             "", os.path.basename(path), args.seed, stamp])
            continue

        n = len(cands)
        if not (lo_expect <= n <= hi_expect):
            word = "only " if n < lo_expect else ""
            want = str(lo_expect) if lo_expect == hi_expect else f"{lo_expect}-{hi_expect}"
            problems.append(
                f"{gid}: {word}{n} submission{'s' if n != 1 else ''}, "
                f"expected {want}")

        order = draw_order(cands, gid, args.seed)
        win_path, win_stem = order[0]
        assert (win_path, win_stem) == draw(cands, gid, args.seed)
        names = [(_who(p_, s_)[0] or s_) for p_, s_ in order]
        winners.append((gid, win_stem, names))

        for rank, (path, stem) in enumerate(order, 1):
            who, when = _who(path, stem)
            rows.append([gid, rank, who, when, "YES" if rank == 1 else "",
                         os.path.basename(path), args.seed, stamp])

        # The winner, renamed to the bare group ID. That name is what the
        # Fusion exporter turns into the group's laser mark.
        win_ext = os.path.splitext(win_path)[1].lower() or SUBMISSION_EXT
        dest = os.path.join(args.out, f"{gid}{win_ext}")
        if win_ext == ".f3z":
            notes.append(f"{gid}: the selected design is a .f3z (Fusion archive). "
                         f"Upload it like the others; the export script breaks "
                         f"its links and exports through the assembly.")
        shutil.copy2(win_path, dest)

        if copy_drawings:
            extras = companions(win_path, win_stem)
            if extras:
                gdir = os.path.join(args.drawings, gid)
                os.makedirs(gdir, exist_ok=True)
                for e in extras:
                    shutil.copy2(e, os.path.join(gdir, os.path.basename(e)))
            else:
                notes.append(f"{gid}: no drawing files found beside {win_stem}")

    for p in unparsed:
        problems.append(
            f"no group ID in filename: {os.path.relpath(p, args.submissions)}")

    if roster is not None:
        for missing in sorted(roster - set(by_group)):
            problems.append(f"{missing}: on the roster but NOTHING submitted")

    # The log is the audit record of a graded decision, so a re-run must not
    # quietly erase the previous one. Re-running with the same submissions and
    # seed reproduces the same winners and the archive is just a duplicate; but
    # a re-run AFTER new submissions arrived changes winners, and that is
    # exactly the case where the earlier record matters.
    kept = [k for k in (_archive(args.log), _archive(args.report)) if k]

    with open(args.log, "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows(rows)

    # ---- report ----
    lines = [
        "Landing gear project -- random design selection",
        "=" * 47,
        f"drawn at   {stamp}",
        f"seed       {args.seed}",
        f"source     {os.path.abspath(args.submissions)}",
        f"expecting  {lo_expect}-{hi_expect} submissions per group"
        if lo_expect != hi_expect else f"expecting  {lo_expect} per group",
        "",
        f"{len(found)} submissions found across {len(by_group)} groups",
        f"{len(winners)} designs selected -> {os.path.abspath(args.out)}",
        "",
        "Selected design per group",
        "-" * 47,
    ]
    for gid, stem, names in winners:
        lines.append(f"  {gid:<14} {names[0]}")
        if len(names) > 1:
            lines.append(f"  {'':<14}   backups, in order: {', '.join(names[1:])}")
    lines += ["", "If a selected design fails the check (a part not in sheet metal,",
              "an incomplete assembly), use that group's next backup and note why.",
              "selection_log.csv has every submission with its draw order."]
    if problems:
        lines += ["", f"PROBLEMS ({len(problems)}) -- fix before exporting",
                  "-" * 47]
        lines += [f"  ! {p}" for p in problems]
    if cleared:
        notes.append(f"{cleared} winner file(s) from the previous draw were removed "
                     f"from {os.path.basename(os.path.abspath(args.out))}/ first")
    if notes:
        lines += ["", f"Notes ({len(notes)})", "-" * 47]
        lines += [f"  - {n}" for n in notes]
    lines += ["", "This draw is reproducible: same submissions + same seed",
              "= same winners, on any machine, at any time.", ""]

    text = "\n".join(lines)
    with open(args.report, "w", encoding="utf-8") as f:
        f.write(text)

    print(text)
    if kept:
        print("  previous draw kept as:")
        for k in kept:
            print(f"     {os.path.basename(k)}")
    print(f"  log     {os.path.abspath(args.log)}")
    print(f"  report  {os.path.abspath(args.report)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
