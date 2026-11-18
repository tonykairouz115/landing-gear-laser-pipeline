"""
check_submissions.py -- who submitted, and which submissions look copied.

Separate from the landing gear pipeline. It reads the Brightspace bulk
download and never changes a file. Nothing it finds is proof: it only lists
the pairs worth opening side by side.

WHAT IT ANSWERS
---------------
1. Who submitted and who didn't. Every name on the class list gets a status:
   submitted, only other files (PDFs but no .f3d/.f3z), or nothing.
2. Which pairs of students look copied. Every pair of students in the class
   is compared, signal by signal, and each signal is its own column (Yes/No or
   a count), so you can see WHY a pair was flagged.

HOW COPYING IS SPOTTED
----------------------
Everyone models the same physical part, so similar shapes are expected and
prove nothing. What gives a copy away is what was left behind by HOW the file
was made. A .f3d file is a zip archive (a .f3z is an archive of .f3d
files, opened one level deeper); this script opens it (no Fusion
needed) and compares what is inside.

Anything that most of the class shares (Fusion's own defaults, a starter file
the course handed out) is ignored automatically: only things that a handful
of students have in common count. See RARE below.

  Strong
    Same file             byte-for-byte identical .f3d
    Shared internal data  identical pieces inside the archive (body geometry
                          and other blobs) that only these files have
    Shared internal IDs   hidden IDs Fusion gives bodies, sketches and
                          features. Two people modelling separately never
                          share them; a copied file keeps them.
  Medium
    Same save stamps      pieces of the archive saved at the exact same second
    Shared exact numbers  decimals like 23.4718 found in both files and almost
                          nowhere else
    Shared rare names     the same unusual names or typos inside the file
    (with Fusion data, see below)
    Same build history    same features in the same order
    All dims identical    every sketch dimension the same as the other file
    Same rare dims        dimension values few others in the class used
    Same geometry         every body's volume and area identical
  Weak (shown, never flag a pair on their own)
    Similar file size, same saved view, submitted within 10 minutes

OPTIONAL: FUSION DATA
---------------------
Build history, dimensions, geometry and saved view need Fusion to read them.
Run fusion/FingerprintDesigns.py on a Fusion project holding the submissions,
then pass its output folder with --fusion-data. Without it those columns say
"-" and everything else still works.

USAGE
    python check_submissions.py --submissions "Brightspace download.zip"
                                --classlist classlist.csv
                                [--fusion-data fingerprints] [--out check]

    --submissions  the bulk download, as the .zip or the unzipped folder
    --classlist    CSV export of the class list (first/last name, student
                   number, and a group column if you have one). Without it
                   the "who didn't submit" part can't be answered.

OUTPUT (in --out, default "submission_check")
    report.html    open in a browser: summary, students, flagged pairs
    students.csv   one row per student
    pairs.csv      every pair with at least one signal, all columns

Standard library only. Python 3.9 or newer.
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime
import difflib
import hashlib
import html
import io
import itertools
import json
import math
import os
import re
import shutil
import sys
import tempfile
import unicodedata
import zipfile

# Something counts as "rare" when at most this share of all submissions has
# it (and never fewer than 3 files). Fusion's defaults sit in nearly every
# file, so they never count; what two or three copiers share does.
RARE_SHARE = 0.05
RARE_MIN = 3

# Pieces inside the archive smaller than this are ignored when comparing:
# tiny ones are too often identical by chance.
MIN_BLOB = 256

# Reading a huge archive in full would be slow and pointless.
MAX_MEMBER_BYTES = 40 * 1024 * 1024
MAX_FILE_BYTES = 300 * 1024 * 1024

# Score per signal, and the score that puts a pair on the list. These are a
# starting point: check them on the first real batch (see "How to read").
FLAG_SCORE = 25
LIKELY_SCORE = 60

DEPTS = ("MAAE", "AERO")

# A .f3z (Fusion archive) is what a student with linked parts submits. It is
# their model as much as a .f3d is.
MODEL_EXTS = (".f3d", ".f3z")

# ---------------------------------------------------------------- names ----

# "<submission id>-<file id> - <Student Name> - <Mon D, YYYY HMM AM> - <file>"
BRIGHTSPACE_RE = re.compile(
    r"^\s*\d+-\d+\s+-\s+(?P<who>.+?)\s+-\s+"
    r"(?P<when>[A-Za-z]{3,9}\.?\s+\d{1,2},\s+\d{4}\s+\d{1,2}:?\d{2}\s*[AaPp][Mm])"
    r"\s+-\s+(?P<rest>.*)$")

SEP = r"[-_.\s]*"
GROUP_RE = re.compile(
    rf"({'|'.join(DEPTS)}){SEP}(L\s*\d+){SEP}G(?:ROUP)?{SEP}0*(\d+)",
    re.IGNORECASE)


def group_id_from(text: str) -> str:
    """MAAE-L2-G07 from any spelling of it, or '' when there is none."""
    text = re.sub(r"(?i)\b(MAAE|AERO)[\s_-]*2001(?!\d)", r"\1", text or "")
    m = GROUP_RE.search(text)
    if not m:
        return ""
    sec = re.sub(r"\s+", "", m.group(2)).upper()
    return f"{m.group(1).upper()}-{sec}-G{int(m.group(3)):02d}"


def norm_name(name: str) -> str:
    """Accents, case, punctuation and spacing removed, for matching names."""
    name = unicodedata.normalize("NFKD", name or "")
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = re.sub(r"[^a-z0-9 ]+", " ", name.lower())
    return re.sub(r"\s+", " ", name).strip()


def parse_brightspace(name: str):
    """(student, submitted at, the student's own file name) or None."""
    m = BRIGHTSPACE_RE.match(name or "")
    if not m:
        return None
    when = re.sub(r"\s+", " ", m.group("when").replace(".", "")).strip()
    when = re.sub(r"(\d{1,2}):?(\d{2}) ?([AaPp][Mm])$",
                  lambda k: f"{int(k.group(1)):02d}{k.group(2)} {k.group(3).upper()}",
                  when)
    for fmt in ("%b %d, %Y %I%M %p", "%B %d, %Y %I%M %p"):
        try:
            return (m.group("who").strip(),
                    datetime.datetime.strptime(when, fmt),
                    m.group("rest"))
        except ValueError:
            pass
    return (m.group("who").strip(), None, m.group("rest"))


# ------------------------------------------------------------ class list ----

def _pick(headers, *wanted):
    low = {h.strip().lower().lstrip("#").strip(): h for h in headers}
    for w in wanted:
        if w in low:
            return low[w]
    for w in wanted:
        for k, h in low.items():
            if w in k:
                return h
    return None


def load_classlist(path: str):
    """
    Students from a Brightspace class list or grades export. The columns are
    found by name, so any export with first/last (or full) name works.
    """
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return []
    h = rows[0].keys()
    first = _pick(h, "first name", "firstname", "given name", "first")
    last = _pick(h, "last name", "lastname", "surname", "family name", "last")
    full = _pick(h, "full name", "student name", "name", "student")
    num = _pick(h, "orgdefinedid", "student number", "student id",
                "student no", "id")
    grp = _pick(h, "group", "groups", "group name", "group category")
    email = _pick(h, "email", "e-mail")
    out = []
    for r in rows:
        if first and last:
            name = f"{r.get(first, '').strip()} {r.get(last, '').strip()}".strip()
        elif full:
            name = r.get(full, "").strip()
            if "," in name:                      # "Last, First"
                a, b = name.split(",", 1)
                name = f"{b.strip()} {a.strip()}"
        else:
            name = ""
        if not name:
            continue
        number = (r.get(num, "") if num else "").strip().lstrip("#")
        out.append({
            "name": name,
            "number": number,
            "email": (r.get(email, "") if email else "").strip(),
            "group": group_id_from(r.get(grp, "") if grp else ""),
            "group_raw": (r.get(grp, "") if grp else "").strip(),
        })
    return out


# ---------------------------------------------------------- submissions ----

def find_files(root: str):
    """Every submitted file, with who sent it and when, if Brightspace says."""
    files = []
    for dirpath, _dirs, names in os.walk(root):
        for fn in names:
            if fn.lower() in ("index.html", "index.htm") or fn.startswith("."):
                continue
            path = os.path.join(dirpath, fn)
            stem, ext = os.path.splitext(fn)
            info = parse_brightspace(stem) or parse_brightspace(
                os.path.basename(dirpath))
            who, when, own = (info if info else ("", None, stem))
            files.append({
                "path": path, "file": fn, "ext": ext.lower(),
                "who": who, "when": when, "own_name": own,
                "group": group_id_from(stem) or group_id_from(
                    os.path.basename(dirpath)),
            })
    return files


# -------------------------------------------------------- fingerprints ----

GUID_RE = re.compile(rb"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                     rb"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
# Long letter-and-digit tokens with no spaces: Fusion's entity tokens and
# other hidden IDs look like this.
TOKEN_RE = re.compile(rb"[A-Za-z0-9+/_=-]{20,200}")
# 4+ decimals: finer than a caliper reading in mm or cm, so two students
# measuring the same edge don't match by chance.
NUMBER_RE = re.compile(rb"(?<![\w.])-?\d{1,6}\.\d{4,17}(?![\w.])")
ASCII_RE = re.compile(rb"[\x20-\x7e]{4,120}")
UTF16_RE = re.compile(rb"(?:[\x20-\x7e]\x00){4,120}")
NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9 _().#-]*")


def _is_id(tok: bytes) -> bool:
    has_d = any(48 <= c <= 57 for c in tok)
    has_a = any(65 <= c <= 90 or 97 <= c <= 122 for c in tok)
    return has_d and has_a


def _round_number(s: str) -> bool:
    """1.000, 2.500, 0.250 -- values anyone could type, not evidence."""
    try:
        v = float(s)
    except ValueError:
        return True
    return abs(v * 100 - round(v * 100)) < 1e-9


def fingerprint(path: str) -> dict:
    fp = {"sha256": "", "size": 0, "zip": False, "blobs": set(),
          "ids": set(), "numbers": set(), "names": set(), "stamps": set(),
          "members": 0, "error": ""}
    try:
        fp["size"] = os.path.getsize(path)
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        fp["sha256"] = h.hexdigest()
    except OSError as e:
        fp["error"] = f"could not read: {e}"
        return fp

    if not zipfile.is_zipfile(path):
        fp["error"] = "not a readable Fusion archive (only the whole-file check applies)"
        return fp
    fp["zip"] = True
    try:
        with zipfile.ZipFile(path) as z:
            _read_archive(z, fp, depth=0, budget=[0])
    except Exception as e:
        fp["error"] = f"archive damaged: {e}"
    return fp


def _read_archive(z, fp: dict, depth: int, budget: list) -> None:
    """
    Fingerprint every piece of an archive. A .f3z holds the assembly and each
    linked part as .f3d files, which are archives themselves, so those are
    opened one level down and compared exactly like a plain .f3d.
    """
    for info in z.infolist():
        if info.is_dir():
            continue
        fp["members"] += 1
        if info.file_size >= MIN_BLOB:
            fp["blobs"].add((info.CRC, info.file_size))
            fp["stamps"].add((info.filename, info.date_time))
        if info.file_size > MAX_MEMBER_BYTES or budget[0] > MAX_FILE_BYTES:
            continue
        try:
            data = z.read(info)
        except Exception:
            continue
        budget[0] += len(data)
        if data[:4] == b"PK\x03\x04":
            # A nested archive: open it if possible. Its raw bytes are
            # compressed data plus the archive's own file list, which every
            # .f3z shares, so they are never scanned as text.
            if depth == 0:
                try:
                    with zipfile.ZipFile(io.BytesIO(data)) as inner:
                        _read_archive(inner, fp, depth + 1, budget)
                except Exception:
                    pass
            continue
        _scan(data, fp)


def _scan(data: bytes, fp: dict) -> None:
    for g in GUID_RE.findall(data):
        fp["ids"].add(g.lower().decode())
    for t in TOKEN_RE.findall(data):
        if _is_id(t) and not GUID_RE.fullmatch(t):
            fp["ids"].add(t.decode())
    for n in NUMBER_RE.findall(data):
        s = n.decode()
        if not _round_number(s):
            fp["numbers"].add(s)
    texts = [m.decode("ascii") for m in ASCII_RE.findall(data)]
    texts += [m.decode("utf-16-le", "ignore") for m in UTF16_RE.findall(data)]
    for t in texts:
        # Names people typed: "Left Cover Hole 2", "Stabalizer_v3", "Chasie".
        # At least 6 characters and 4 letters, starting with a letter, so the
        # short letter runs found by chance in binary data never count.
        for s in NAME_RE.findall(t):
            s = s.strip()
            if (6 <= len(s) <= 80 and sum(c.isalpha() for c in s) >= 4
                    and not TOKEN_RE.fullmatch(s.encode())):
                fp["names"].add(s)


# ------------------------------------------------------ Fusion fingerprints --

def load_fusion(folder: str) -> dict:
    """JSON files from FingerprintDesigns.py, keyed by normalised file name."""
    out = {}
    if not folder or not os.path.isdir(folder):
        return out
    for fn in os.listdir(folder):
        if not fn.lower().endswith(".json"):
            continue
        try:
            with open(os.path.join(folder, fn), encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        # Fusion names carry no extension, and a student's own name may
        # contain dots ("v1.2"), so the name is used whole.
        name = d.get("name") or fn[:-5]
        out[norm_name(name)] = d
        if name.lower().endswith((".f3d", ".f3z")):
            out[norm_name(name[:-4])] = d
    return out


DEFAULT_NAME = re.compile(r"^(sketch|body|component|extrude|fillet|chamfer|"
                          r"hole|flange|bend|unfold|refold|flat pattern|"
                          r"plane|axis|point|mirror|pattern|combine|cut|"
                          r"rectangular pattern|circular pattern|offset plane|"
                          r"construction plane|joint|rigid group|revolve)"
                          r"\s*\d*(\s*\(\d+\))?$", re.IGNORECASE)


def fusion_compare(a: dict, b: dict) -> dict:
    """Signals from two Fusion fingerprints that aren't rarity counts."""
    r = {}
    fa, fb = a.get("features") or [], b.get("features") or []
    if fa and fb:
        ratio = difflib.SequenceMatcher(None, fa, fb, autojunk=False).ratio()
        r["history_pct"] = round(ratio * 100)
    ga, gb = _geom(a), _geom(b)
    r["geom_same"] = bool(ga) and ga == gb
    ca, cb = a.get("camera"), b.get("camera")
    r["view_same"] = bool(ca) and bool(cb) and all(
        abs(x - y) < 1e-4 for x, y in zip(_flat(ca), _flat(cb)))
    return r


def fusion_sets(d: dict) -> dict:
    """
    The Fusion signals that are judged by rarity, like everything else: a
    dimension, a name or a whole build history counts only when few designs
    in the class have it. Two students following the same tutorial steps
    share a build history with half the class, so that alone never counts.
    """
    feats = tuple(d.get("features") or [])
    dims = sorted(_dims(d))
    return {"fdims": set(dims),
            # The whole set of dimensions at once: each caliper reading may be
            # common, but all of them identical is not.
            "dimset": {tuple(dims)} if len(dims) >= 5 else set(),
            "fnames": _custom_names(d),
            "history": {feats} if len(feats) >= 8 else set()}


def _dims(d):
    out = []
    for v in d.get("dimensions") or []:
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        # Whole and half millimetres (20, 12.5) are what anyone would type.
        if abs(v * 2 - round(v * 2)) > 1e-6:
            out.append(round(v, 6))
    return out


def _geom(d):
    out = []
    for g in d.get("bodies_geometry") or []:
        try:
            out.append((round(float(g["volume"]), 6), round(float(g["area"]), 6)))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out)


def _flat(cam):
    out = []
    for k in ("eye", "target", "up"):
        out.extend(cam.get(k) or [])
    return out


def _custom_names(d):
    names = set()
    for k in ("components", "bodies", "sketches", "feature_names"):
        for n in d.get(k) or []:
            n = (n or "").strip()
            if n and not DEFAULT_NAME.match(n):
                names.add(norm_name(n))
    return names


# -------------------------------------------------------------- compare ----

CATS = ("blobs", "ids", "numbers", "names", "stamps",
        "fdims", "dimset", "fnames", "history")


def rare_pairs(fps: dict, limit: int):
    """
    For each category, how many RARE items every pair of students shares.
    An item is rare when at most `limit` submissions contain it.
    """
    shared = {c: collections.Counter() for c in CATS}
    for cat in CATS:
        index = collections.defaultdict(list)
        for sid, fp in fps.items():
            for item in fp[cat]:
                index[item].append(sid)
        for item, who in index.items():
            if 2 <= len(who) <= limit:
                for a, b in itertools.combinations(sorted(who), 2):
                    shared[cat][(a, b)] += 1
    return shared


def score(row: dict) -> int:
    s = 0
    if row["same_file"]:
        s += 100
    if row["blobs"]:
        s += min(60, 40 + 5 * (row["blobs"] - 1))
    if row["ids"]:
        s += min(50, 30 + 2 * (row["ids"] - 1))
    # Two students CAN save in the same couple of seconds, so on its own this
    # stays under the flag line; with copied data or IDs it adds weight.
    if row["stamps"]:
        s += 15
    if row["numbers"] >= 3:
        s += min(25, 15 + row["numbers"] - 3)
    if row["names"] >= 2:
        s += min(20, 10 + row["names"] - 2)
    if row["history"]:
        s += 20
    if row["dimset"]:
        s += 20
    if row["fdims"] >= 3:
        s += min(25, 15 + row["fdims"] - 3)
    if row.get("geom_same"):
        s += 20
    if row["fnames"] >= 2:
        s += min(20, 10 + row["fnames"] - 2)
    # Weak signals: a nudge, never enough alone.
    if row["size_close"]:
        s += 3
    if row.get("view_same"):
        s += 3
    if row["time_close"]:
        s += 2
    return s


def verdict(row: dict) -> str:
    if row["same_file"]:
        return "SAME FILE"
    if row["score"] >= LIKELY_SCORE:
        return "Very likely copied"
    if row["score"] >= FLAG_SCORE:
        return "Look at this"
    return ""


# ---------------------------------------------------------------- main ----

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Who submitted, and which submissions look copied.")
    ap.add_argument("--submissions", required=True,
                    help="Brightspace bulk download: the .zip or its folder")
    ap.add_argument("--classlist", help="class list CSV from Brightspace")
    ap.add_argument("--fusion-data",
                    help="folder of JSON files from FingerprintDesigns.py")
    ap.add_argument("--out", default="submission_check")
    ap.add_argument("--rare", type=float, default=RARE_SHARE,
                    help="share of the class above which a shared item is "
                         "'normal' and ignored (default 0.05 = 5%%)")
    args = ap.parse_args(argv)

    tmp = None
    root = args.submissions
    if os.path.isfile(root) and zipfile.is_zipfile(root):
        tmp = tempfile.mkdtemp(prefix="subcheck_")
        with zipfile.ZipFile(root) as z:
            z.extractall(tmp)
        root = tmp
    if not os.path.isdir(root):
        print(f"Can't find the submissions: {args.submissions}")
        return 2

    try:
        return _run(root, args)
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def _run(root, args) -> int:
    files = find_files(root)
    roster = load_classlist(args.classlist) if args.classlist else []
    notes = []

    # ---- match every file to a student --------------------------------
    students = collections.OrderedDict()          # key -> record
    by_norm = {}
    for s in roster:
        key = norm_name(s["name"])
        if key in students:
            notes.append(f"Two students called {s['name']} on the class list: "
                         "their files can't be told apart by name.")
            continue
        students[key] = dict(s, files=[], on_list=True)
        by_norm[key] = key
        parts = key.split()
        if len(parts) >= 2:                       # "Last First" also matches
            by_norm.setdefault(" ".join(parts[1:] + parts[:1]), key)

    for f in files:
        who = f["who"] or f"(unknown sender) {f['own_name']}"
        key = norm_name(who)
        target = by_norm.get(key)
        if target is None and roster and f["who"]:
            close = difflib.get_close_matches(key, list(students), 1, 0.88)
            if close:
                target = close[0]
                notes.append(f"'{f['who']}' matched to {students[target]['name']} "
                             "on the class list by a close spelling.")
        if target is None:
            target = key
            if target not in students:
                students[target] = {"name": who, "number": "", "email": "",
                                    "group": "", "group_raw": "",
                                    "files": [], "on_list": not roster}
        students[target]["files"].append(f)

    # ---- per student: latest model, status ------------------------------
    for key, s in students.items():
        models = [f for f in s["files"] if f["ext"] in MODEL_EXTS]
        models.sort(key=lambda f: (f["when"] or datetime.datetime.min, f["file"]))
        s["model"] = models[-1] if models else None
        s["attempts"] = len(models)
        s["others"] = [f for f in s["files"] if f["ext"] not in MODEL_EXTS]
        if not s["group"]:
            s["group"] = (s["model"] or {}).get("group", "") if s["model"] else ""
            if not s["group"]:
                gs = {f["group"] for f in s["files"] if f["group"]}
                s["group"] = gs.pop() if len(gs) == 1 else ""
        if not s["files"]:
            s["status"] = "NOT SUBMITTED"
        elif not s["model"]:
            s["status"] = "No Fusion file (other files only)"
        else:
            s["status"] = "Submitted"
        if not s["on_list"]:
            s["status"] += " (not on class list)"
        if s["model"] and roster and s["on_list"]:
            fg = s["model"]["group"]
            if fg and s["group"] and fg != s["group"]:
                notes.append(f"{s['name']}: class list says {s['group']}, "
                             f"file name says {fg}.")

    # ---- fingerprints ------------------------------------------------------
    subs = {k: s for k, s in students.items() if s["model"]}
    print(f"Reading {len(subs)} designs...")
    fps = {}
    for i, (k, s) in enumerate(subs.items(), 1):
        fps[k] = fingerprint(s["model"]["path"])
        if fps[k]["error"]:
            notes.append(f"{s['name']}: {fps[k]['error']}")
        if i % 25 == 0:
            print(f"  {i} of {len(subs)}")

    fusion = load_fusion(args.fusion_data)
    fus_of = {}
    for k, s in subs.items():
        m = s["model"]
        for cand in (os.path.splitext(m["file"])[0], m["own_name"]):
            d = fusion.get(norm_name(cand))
            if d:
                fus_of[k] = d
                break
    if args.fusion_data and not fusion:
        notes.append("No Fusion data found in --fusion-data; those columns are '-'.")
    elif fusion and len(fus_of) < len(subs):
        missing = [subs[k]["name"] for k in subs if k not in fus_of]
        notes.append(f"No Fusion data for {len(missing)} design(s): "
                     + ", ".join(missing[:15]) + (" ..." if len(missing) > 15 else ""))

    for k, fp in fps.items():
        fs = fusion_sets(fus_of[k]) if k in fus_of else {}
        for c in ("fdims", "dimset", "fnames", "history"):
            fp[c] = fs.get(c, set())

    n = len(fps)
    limit = max(RARE_MIN, math.ceil(args.rare * n))
    shared = rare_pairs(fps, limit)

    # ---- every pair ----------------------------------------------------------
    by_hash = collections.defaultdict(list)
    for k, fp in fps.items():
        if fp["sha256"]:
            by_hash[fp["sha256"]].append(k)
    same_file = set()
    for ks in by_hash.values():
        for a, b in itertools.combinations(sorted(ks), 2):
            same_file.add((a, b))

    candidate_pairs = set(same_file)
    for c in CATS:
        candidate_pairs.update(shared[c])
    # Geometry and saved view aren't rarity counts, so with Fusion data every
    # pair is looked at (about 40,000 for a class of 280: a few seconds).
    if fus_of:
        candidate_pairs.update(itertools.combinations(sorted(fus_of), 2))

    rows = []
    for a, b in sorted(candidate_pairs):
        sa, sb = subs[a], subs[b]
        fa, fb = fps[a], fps[b]
        row = {
            "a": sa["name"], "b": sb["name"],
            "group_a": sa["group"], "group_b": sb["group"],
            "same_group": bool(sa["group"]) and sa["group"] == sb["group"],
            "same_file": (a, b) in same_file,
        }
        for c in CATS:
            row[c] = shared[c].get((a, b), 0)
        row["history"] = row["history"] > 0          # one each: Yes/No
        row["dimset"] = row["dimset"] > 0
        big = max(fa["size"], fb["size"], 1)
        row["size_close"] = abs(fa["size"] - fb["size"]) / big <= 0.01
        ta, tb = sa["model"]["when"], sb["model"]["when"]
        row["time_close"] = bool(ta and tb) and abs((ta - tb).total_seconds()) <= 600
        if ta and tb and ta != tb:
            row["first"] = sa["name"] if ta < tb else sb["name"]
        else:
            row["first"] = ""
        if a in fus_of and b in fus_of:
            row.update(fusion_compare(fus_of[a], fus_of[b]))
            row["fusion"] = True
        else:
            row["fusion"] = False
        row["score"] = score(row)
        row["verdict"] = verdict(row)
        # Pairs with only weak signals (file size, view, time) are left out:
        # with Fusion data that would be every pair in the class.
        if (row["same_file"] or row.get("geom_same")
                or any(row[c] for c in CATS)):
            rows.append(row)
    rows.sort(key=lambda r: (-r["score"], r["a"], r["b"]))

    os.makedirs(args.out, exist_ok=True)
    write_students(os.path.join(args.out, "students.csv"), students)
    write_pairs(os.path.join(args.out, "pairs.csv"), rows)
    write_html(os.path.join(args.out, "report.html"), students, rows, notes,
               dict(n=n, limit=limit, roster=bool(roster), fusion=bool(fus_of),
                    source=args.submissions))

    counts = collections.Counter(s["status"] for s in students.values())
    flagged = [r for r in rows if r["verdict"]]
    print()
    for st, c in counts.most_common():
        print(f"  {c:4d}  {st}")
    print(f"  {len(flagged):4d}  pairs flagged "
          f"({sum(1 for r in flagged if r['verdict'] != 'Look at this')} very likely)")
    print(f"\nReport: {os.path.abspath(os.path.join(args.out, 'report.html'))}")
    return 0


# ---------------------------------------------------------------- output ----

PAIR_COLS = [
    ("verdict", "Verdict"), ("score", "Score"),
    ("a", "Student A"), ("b", "Student B"), ("same_group", "Same group"),
    ("first", "Submitted first"),
    ("same_file", "Same file"), ("blobs", "Shared internal data"),
    ("ids", "Shared internal IDs"), ("stamps", "Same save stamps"),
    ("numbers", "Shared exact numbers"), ("names", "Shared rare names"),
    ("history", "Same build history"), ("history_pct", "Build history match %"),
    ("dimset", "All dims identical"), ("fdims", "Same rare dims"), ("geom_same", "Same geometry"),
    ("fnames", "Same custom names (Fusion)"),
    ("size_close", "Similar file size"), ("view_same", "Same saved view"),
    ("time_close", "Submitted within 10 min"),
    ("group_a", "Group A"), ("group_b", "Group B"),
]
FUSION_KEYS = {"history", "history_pct", "fdims", "dimset", "geom_same",
               "fnames", "view_same"}


def _cell(row, key):
    if key in FUSION_KEYS and not row.get("fusion"):
        return "-"
    v = row.get(key, "")
    if isinstance(v, bool):
        return "Yes" if v else "No"
    return v


def write_pairs(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([t for _k, t in PAIR_COLS])
        for r in rows:
            w.writerow([_cell(r, k) for k, _t in PAIR_COLS])


def _student_row(s):
    m = s["model"]
    return [s["name"], s["number"], s["group"] or s["group_raw"], s["status"],
            m["file"] if m else "", m["when"].strftime("%Y-%m-%d %H:%M")
            if m and m["when"] else "",
            s["attempts"], len(s["others"])]


STUDENT_COLS = ["Student", "Student number", "Group", "Status",
                "Latest Fusion file", "Submitted", "Fusion uploads", "Other files"]


def write_students(path, students):
    order = sorted(students.values(),
                   key=lambda s: (s["group"] or "~", s["name"].lower()))
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(STUDENT_COLS)
        for s in order:
            w.writerow(_student_row(s))


CSS = """
body{font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;margin:24px;
color:#1d1d1f;background:#fff}
h1{font-size:22px;margin:0 0 4px} h2{font-size:17px;margin:28px 0 8px}
.muted{color:#666} table{border-collapse:collapse;font-size:12.5px}
th,td{border:1px solid #ddd;padding:4px 7px;text-align:left;vertical-align:top}
th{background:#f3f3f3;position:sticky;top:0} td.y{background:#ffd9d6;font-weight:600}
td.n{color:#aaa} td.c{background:#fff1c9} .tag{display:inline-block;padding:1px 7px;
border-radius:9px;font-weight:600} .likely{background:#c62828;color:#fff}
.look{background:#f9a825;color:#000} .same{background:#000;color:#fff}
.miss{background:#ffd9d6} .wrap{overflow-x:auto} ul{margin:4px 0 0 18px;padding:0}
.k{display:flex;gap:18px;flex-wrap:wrap;margin:10px 0} .k div{background:#f6f6f6;
border-radius:8px;padding:8px 12px} .k b{font-size:20px;display:block}
"""


def write_html(path, students, rows, notes, meta):
    e = html.escape
    counts = collections.Counter(s["status"] for s in students.values())
    flagged = [r for r in rows if r["verdict"]]
    out = [f"<!doctype html><meta charset='utf-8'><title>Submission check</title>"
           f"<style>{CSS}</style>",
           "<h1>Submission check</h1>",
           f"<div class='muted'>{e(str(meta['source']))} &middot; "
           f"{datetime.datetime.now():%Y-%m-%d %H:%M} &middot; "
           f"{meta['n']} designs compared &middot; an item counts as rare when "
           f"{meta['limit']} or fewer designs have it &middot; Fusion data: "
           f"{'yes' if meta['fusion'] else 'no'}</div>",
           "<div class='k'>"]
    for st, c in counts.most_common():
        out.append(f"<div><b>{c}</b>{e(st)}</div>")
    out.append(f"<div><b>{len(flagged)}</b>pairs flagged</div></div>")
    if not meta["roster"]:
        out.append("<p><b>No class list given</b>, so students who submitted "
                   "nothing can't be listed. Add --classlist.</p>")

    out.append("<h2>Flagged pairs</h2>")
    if not flagged:
        out.append("<p>No pair reached the flag score.</p>")
    else:
        out.append("<div class='wrap'><table><tr>"
                   + "".join(f"<th>{e(t)}</th>" for _k, t in PAIR_COLS) + "</tr>")
        for r in flagged:
            out.append("<tr>")
            for k, _t in PAIR_COLS:
                v = _cell(r, k)
                cls = ""
                if k == "verdict":
                    tag = {"SAME FILE": "same", "Very likely copied": "likely",
                           "Look at this": "look"}.get(v, "")
                    out.append(f"<td><span class='tag {tag}'>{e(str(v))}</span></td>")
                    continue
                if v == "Yes" or (isinstance(v, int) and not isinstance(v, bool)
                                  and v > 0 and k in CATS):
                    cls = "y"
                elif v in ("No", 0, "-"):
                    cls = "n"
                if v == "Yes" and k in ("same_group", "size_close", "view_same",
                                        "time_close"):
                    cls = "c"                       # weak or context: yellow
                out.append(f"<td class='{cls}'>{e(str(v))}</td>")
            out.append("</tr>")
        out.append("</table></div>")

    out.append("<h2>Students</h2><div class='wrap'><table><tr>"
               + "".join(f"<th>{e(c)}</th>" for c in STUDENT_COLS) + "</tr>")
    for s in sorted(students.values(),
                    key=lambda s: (s["status"] == "Submitted",
                                   s["group"] or "~", s["name"].lower())):
        cls = " class='miss'" if s["status"] != "Submitted" else ""
        out.append(f"<tr{cls}>" + "".join(f"<td>{e(str(v))}</td>"
                                          for v in _student_row(s)) + "</tr>")
    out.append("</table></div>")

    if notes:
        out.append("<h2>Notes</h2><ul>"
                   + "".join(f"<li>{e(n)}</li>" for n in notes) + "</ul>")

    out.append("""<h2>How to read this</h2><ul>
<li>Nothing here is proof. A flag means: open these two designs side by side.</li>
<li><b>Same file</b>, <b>shared internal data</b> and <b>shared internal IDs</b>
are the strong ones: they come from one file being copied,
not from two people modelling the same part.</li>
<li><b>Save stamps</b>, <b>exact numbers</b>, <b>rare names</b>, <b>build history</b>, <b>dimensions</b>
and <b>geometry</b> are medium: possible by coincidence, unlikely together.</li>
<li><b>File size</b>, <b>saved view</b> and <b>submission time</b> are weak and never
flag a pair on their own.</li>
<li><b>Submitted first</b> is who uploaded earlier. It suggests who had the
original, but doesn't prove it.</li>
<li>The scores are a starting point. On the first real batch, if many unrelated
pairs are flagged, raise the bar (or lower --rare); if a copy you know about is
missed, tell me what it looked like.</li>
<li>Every pair with at least one strong or medium signal is in pairs.csv, all students in students.csv.</li>
</ul>""")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


if __name__ == "__main__":
    sys.exit(main())
