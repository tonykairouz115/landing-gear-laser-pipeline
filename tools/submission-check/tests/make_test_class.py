"""
A fake Brightspace download for testing check_submissions.py.

60 students in 15 groups. Planted:
  copy-exact   Ava Brown gets Ben Clark's file byte for byte (same group)
  copy-resaved Dana Evans re-saves Carl Diaz's design: new file, same insides
               except one name changed (different groups)
  copy-renamed Eli Fox takes Finn Green's design and renames things
  lookalike    Gia Hall and Hugo Ivey (same group) model the same part
               independently: should NOT be flagged
  missing      3 students submit nothing, 1 only a PDF, 1 submits twice,
               1 submitter is not on the class list, 1 accented name
Fusion fingerprints are written for every design, so the Fusion columns
are tested too.
"""

import csv
import datetime
import json
import os
import random
import shutil
import sys
import uuid
import zipfile

OUT = sys.argv[1] if len(sys.argv) > 1 else "test_class"
rng = random.Random(7)

FIRST = ["Ava", "Ben", "Carl", "Dana", "Eli", "Finn", "Gia", "Hugo", "Ivy",
         "Jack", "Kai", "Lena", "Mia", "Noah", "Omar", "Pia", "Quinn", "Rosa",
         "Sam", "Tara"]
LAST = ["Brown", "Clark", "Diaz", "Evans", "Fox", "Green", "Hall", "Ivey",
        "Jones", "King", "Lee", "Moore", "Nash", "Ortiz", "Park", "Quinn",
        "Reid", "Shaw", "Tran", "Usher"]
TEMPLATE_GUIDS = [str(uuid.UUID(int=rng.getrandbits(128))) for _ in range(6)]
MANIFEST = b"FusionManifest v2\n" + b"x" * 600
TRUE_DIMS = [23.47, 12.08, 41.9, 7.15, 30.33, 16.52, 9.87, 55.1]
FEATURES = ["Sketch", "ExtrudeFeature", "FlangeFeature", "FlangeFeature",
            "Sketch", "ExtrudeFeature", "HoleFeature", "HoleFeature",
            "FilletFeature", "FlatPattern"]


def students():
    out, used = [], set()
    i = 0
    for f in FIRST:
        for l in LAST:
            if len(out) == 60:
                return out
            if (f, l) in used or f == l:
                continue
            if rng.random() < 0.85 and i >= 8:
                continue
            used.add((f, l))
            out.append([f, l])
            i += 1
    return out


def design(name_seed, quirks=(), dims=None):
    """Contents of one fake .f3d: what an independent student would make."""
    r = random.Random(name_seed)
    dims = dims or [round(d + r.choice([-0.02, -0.01, 0, 0.01, 0.02]), 2)
                    for d in TRUE_DIMS]
    own_ids = [str(uuid.UUID(int=r.getrandbits(128))) for _ in range(20)]
    tokens = ["".join(r.choice("ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz0123456789")
                      for _ in range(28)) for _ in range(10)]
    names = ["Chassie", "Left Cover", "Right Cover", "Nut", "Leg", "Stabalizer"]
    names += list(quirks)
    props = {
        "template": TEMPLATE_GUIDS,
        "entities": own_ids,
        "tokens": tokens,
        "components": names,
        "values_cm": [repr(d / 10 + r.random() * 1e-9) for d in dims],
    }
    geometry = bytes(r.getrandbits(8) for _ in range(4000))
    preview = bytes(r.getrandbits(8) for _ in range(1500))
    return {"Manifest.dat": MANIFEST,
            "Properties.dat": json.dumps(props, indent=1).encode(),
            "Breps.BlobParts/body1.smb": geometry,
            "Previews/large.png": preview}, dims


def write_f3d(path, members, stamp):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            zi = zipfile.ZipInfo(name, date_time=stamp)
            z.writestr(zi, data)


def fusion_json(folder, fname, dims, seed, features=FEATURES, names=(),
                camera=None, geom=None):
    r = random.Random(seed)
    d = {"name": fname,
         "components": ["Chassie", "Left Cover", "Right Cover", "Nut", "Leg",
                        "Stabalizer"] + list(names),
         "bodies": ["Body1"] * 6, "sketches": ["Sketch1", "Sketch2"],
         "features": list(features), "feature_names": [],
         "dimensions": dims,
         "bodies_geometry": geom or [{"volume": round(sum(dims) * 0.37 + r.random(), 6),
                                      "area": round(sum(dims) * 2.1 + r.random(), 6)}],
         "camera": camera or {"eye": [r.random(), 1, 1], "target": [0, 0, 0],
                              "up": [0, 0, 1]}}
    with open(os.path.join(folder, fname + ".json"), "w") as f:
        json.dump(d, f)
    return d


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    sub = os.path.join(OUT, "download")
    fus = os.path.join(OUT, "fingerprints")
    os.makedirs(sub)
    os.makedirs(fus)

    people = students()
    people[5] = ["Zoë", "Nguyễn"]                     # accented
    groups = {}
    roster = []
    for i, (f, l) in enumerate(people):
        dept = "MAAE" if i < 28 else "AERO"
        g = f"{dept}-L{1 + (i // 4) % 3}-G{(i // 4) + 1:02d}"
        groups[(f, l)] = g
        roster.append([f"1013{i:05d}", f"#{f.lower()}{l.lower()}", l, f,
                       f"{f.lower()}@cmail.carleton.ca", g.replace("-", " ")])
    with open(os.path.join(OUT, "classlist.csv"), "w", newline="",
              encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["OrgDefinedId", "Username", "Last Name", "First Name",
                    "Email", "Group"])
        w.writerows(roster)

    base = datetime.datetime(2026, 11, 12, 9, 0)
    sid = [100000]

    def submit(person, members, own, when, stamp, fdims=None, **fj):
        sid[0] += 1
        who = " ".join(person)
        ts = when.strftime("%b %-d, %Y %-I%M %p")
        fname = f"{sid[0]}-{sid[0] + 7} - {who} - {ts} - {own}"
        write_f3d(os.path.join(sub, fname + ".f3d"), members,
                  stamp)
        if fdims is not None:
            fusion_json(fus, fname, fdims, fname, **fj)
        return fname

    by_name = {" ".join(p): p for p in people}
    planted = {"Ava Brown", "Ben Clark", "Carl Diaz", "Dana Evans",
               "Eli Fox", "Finn Green", "Gia Hall", "Hugo Ivey"}
    # Planted people must be in the class list: put them first.
    order = [p for p in people if " ".join(p) not in planted]
    skip = {" ".join(p) for p in order[20:23]}               # submit nothing
    pdf_only = " ".join(order[23])
    twice = " ".join(order[24])

    def stamp(i):
        t = base + datetime.timedelta(minutes=37 * i)
        return (t.year, t.month, t.day, t.hour, t.minute, t.second)

    i = 0
    for p in order:
        name = " ".join(p)
        i += 1
        if name in skip:
            continue
        when = base + datetime.timedelta(hours=i)
        own = f"{groups[tuple(p)]}_{p[1]}"
        if name == pdf_only:
            path = os.path.join(sub, f"{sid[0]}-1 - {name} - "
                                f"{when.strftime('%b %-d, %Y %-I%M %p')} - drawings.pdf")
            open(path, "wb").write(b"%PDF-1.4 fake")
            sid[0] += 1
            continue
        m, d = design(name)
        if name == twice:
            old, _ = design(name + "old")
            submit(p, old, own, when - datetime.timedelta(days=1), stamp(i + 100))
        submit(p, m, own, when, stamp(i), fdims=d)

    # A submitter not on the class list.
    m, d = design("stranger")
    submit(["Walk", "In"], m, "MAAE-L1-G01_In", base, stamp(300), fdims=d)

    # Planted pairs. Names may not be on the generated list: add them.
    def ensure(name, g):
        if name not in by_name:
            f, l = name.split()
            by_name[name] = [f, l]
            groups[(f, l)] = g
            roster.append([f"1019{len(roster):05d}", f"#{f.lower()}", l, f,
                           "", g.replace("-", " ")])
        return by_name[name]

    ben = ensure("Ben Clark", "MAAE-L2-G09")
    ava = ensure("Ava Brown", "MAAE-L2-G09")
    carl = ensure("Carl Diaz", "AERO-L1-G10")
    dana = ensure("Dana Evans", "AERO-L3-G12")
    finn = ensure("Finn Green", "MAAE-L1-G04")
    eli = ensure("Eli Fox", "MAAE-L3-G06")
    gia = ensure("Gia Hall", "AERO-L2-G14")
    hugo = ensure("Hugo Ivey", "AERO-L2-G14")
    with open(os.path.join(OUT, "classlist.csv"), "w", newline="",
              encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["OrgDefinedId", "Username", "Last Name", "First Name",
                    "Email", "Group"])
        w.writerows(roster)

    t = base + datetime.timedelta(days=-1)
    # 1. exact copy: same bytes (zip written with the same stamp)
    m, d = design("ben-original", quirks=["Chasie bracket v2"])
    submit(ben, m, "MAAE-L2-G09_Clark", t, stamp(500), fdims=d,
           names=["Chasie bracket v2"])
    f1 = sorted(os.listdir(sub))[-1]
    # copy Ben's bytes for Ava (Brightspace renames it)
    src = [x for x in os.listdir(sub) if "Ben Clark" in x][0]
    sid[0] += 1
    when = t + datetime.timedelta(hours=20)
    dst = (f"{sid[0]}-{sid[0] + 7} - Ava Brown - "
           f"{when.strftime('%b %-d, %Y %-I%M %p')} - MAAE-L2-G09_Brown.f3d")
    shutil.copy(os.path.join(sub, src), os.path.join(sub, dst))
    jf = json.load(open(os.path.join(fus, src[:-4] + ".json")))
    jf["name"] = dst[:-4]
    json.dump(jf, open(os.path.join(fus, dst[:-4] + ".json"), "w"))

    # 2. re-saved copy: same insides, one name changed, new stamps
    m, d = design("carl-original", quirks=["Rt cover flange A"])
    submit(carl, m, "AERO-L1-G10_Diaz", t, stamp(600), fdims=d,
           names=["Rt cover flange A"],
           camera={"eye": [3, 2, 1], "target": [0, 0, 0], "up": [0, 0, 1]},
           geom=[{"volume": 12.345678, "area": 98.765432}])
    props = json.loads(m["Properties.dat"])
    props["components"][0] = "Chassis"
    m2 = dict(m, **{"Properties.dat": json.dumps(props, indent=1).encode()})
    submit(dana, m2, "AERO-L3-G12_Evans", t + datetime.timedelta(days=1),
           stamp(650), fdims=d, names=["Rt cover flange A"],
           camera={"eye": [3, 2, 1], "target": [0, 0, 0], "up": [0, 0, 1]},
           geom=[{"volume": 12.345678, "area": 98.765432}])

    # 3. renamed copy: IDs and geometry kept, names and preview changed
    m, d = design("finn-original", quirks=["LG leg final"])
    submit(finn, m, "MAAE-L1-G04_Green", t, stamp(700), fdims=d,
           names=["LG leg final"])
    props = json.loads(m["Properties.dat"])
    props["components"] = ["Chassie", "Left Cover", "Right Cover", "Nut",
                           "Leg", "Stabalizer", "My leg"]
    m3 = dict(m, **{"Properties.dat": json.dumps(props, indent=1).encode(),
                    "Previews/large.png": bytes(rng.getrandbits(8)
                                                for _ in range(1500))})
    submit(eli, m3, "MAAE-L3-G06_Fox", t + datetime.timedelta(hours=30),
           stamp(720), fdims=d, names=["My leg"])

    # 4. independent lookalikes in one group: same true part, own work
    for who, seed in ((gia, "gia"), (hugo, "hugo")):
        m, d = design(seed)
        submit(who, m, f"AERO-L2-G14_{who[1]}", t + datetime.timedelta(hours=2),
               stamp(800 if seed == "gia" else 801), fdims=d)

    # Brightspace adds this; the checker must ignore it.
    open(os.path.join(sub, "index.html"), "w").write("<html></html>")
    shutil.make_archive(os.path.join(OUT, "download"), "zip", sub)
    print("written", OUT)


if __name__ == "__main__":
    main()
