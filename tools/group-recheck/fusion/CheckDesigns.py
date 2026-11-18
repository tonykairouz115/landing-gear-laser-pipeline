"""
CheckDesigns -- Fusion 360 script for the group re-check. READ-ONLY.

Separate from the laser pipeline: it exports nothing and changes no file.
It opens every design in the active Fusion project, checks whether each of
the six parts exists under its brief name AND is sheet metal (Fusion can
flatten it), closes the design WITHOUT saving, and writes two reports.

INSTALL
    Fusion -> Utilities -> ADD-INS -> Scripts and Add-Ins -> Scripts -> "+"
    Create a NEW script named CheckDesigns (do not replace BatchExportProject),
    replace its .py with this file.

RUN
    1. Upload the "candidates" folder (from prepare_candidates.py) into a NEW
       Fusion project, and make that project active.
    2. Run CheckDesigns, pick a folder for the reports.

REPORTS
    check_results.csv   one row per design: each part OK or what is wrong
    best_per_group.csv  per group: the first member, in draw order, whose
                        design has all six parts OK -- or "none"

Then copy that member's ORIGINAL file into the pipeline's "selected" folder,
named with the group ID, and export it with the normal pipeline.
"""

import csv
import os
import re
import traceback

import adsk.core
import adsk.fusion

CODES = ["CHS", "LCV", "RCV", "NUT", "LEG", "STB"]
NAMES = {"CHS": "Chassie", "LCV": "Left Cover", "RCV": "Right Cover",
         "NUT": "Nut", "LEG": "Leg", "STB": "Stabalizer"}

# Same accepted spellings as the pipeline's export script.
_ACCEPTED = {
    "CHS": ["Chassie", "Chassis", "CHS"],
    "LCV": ["Left Cover", "LeftCover", "Cover Left", "LCV"],
    "RCV": ["Right Cover", "RightCover", "Cover Right", "RCV"],
    "NUT": ["Nut", "Nut Clip", "Captive Nut", "Captive Nut Clip", "NUT"],
    "LEG": ["Leg", "LEG"],
    "STB": ["Stabalizer", "Stabilizer", "Stabaliser", "Stabiliser",
            "Stabalizer Link", "Stabilizer Link", "Stabiliser Link", "STB"],
}


def _normalise(name):
    s = (name or "").strip().lower()
    s = re.sub(r"[\s_\-.()\[\]]+", " ", s).strip()
    s = re.sub(r"\s+(?:v\s*\d+|copy|\d+)$", "", s)
    return s.replace(" ", "")


_CODE_OF = {}
for _c, _sp in _ACCEPTED.items():
    for _s in _sp:
        _CODE_OF[_normalise(_s)] = _c

# Bought parts: not reported as "unrecognised names".
_HARDWARE = re.compile(
    r"\d{4,}a\d|screw|bolt|nut\b.*\d|washer|rivet|gear|knob|wheel|bearing|"
    r"shaft|rod|pin|din\b|iso\b|asme|hex|lock|thread|spacer|insert",
    re.IGNORECASE)


def _part_code(name):
    return _CODE_OF.get(_normalise(name))


def _parse_name(fname):
    """('AERO-L1-G03', 2, 'Doe-Jane') from 'AERO-L1-G03__p2__Doe-Jane'."""
    base = os.path.splitext(fname)[0]
    m = re.match(r"^\s*([A-Za-z]+-L\d+-G\d+)__p(\d+)__(.*)$", base)
    if m:
        return m.group(1).upper(), int(m.group(2)), m.group(3)
    m = re.search(r"(MAAE|AERO)(?:2001[AB]?)?[-_\s]*L\s*(\d+)[-_\s]*G\s*0*(\d+)", base, re.I)
    if m:
        return "%s-L%d-G%02d" % (m.group(1).upper(), int(m.group(2)),
                                 int(m.group(3))), 999, base
    return "UNKNOWN", 999, base


def _collect(folder, out):
    for i in range(folder.dataFiles.count):
        out.append(folder.dataFiles.item(i))
    for i in range(folder.dataFolders.count):
        _collect(folder.dataFolders.item(i), out)
    return out


def _is_linked_part(df):
    try:
        return bool(df.hasParentReferences)
    except Exception:
        return False


def _break_links(design):
    for _ in range(10):
        linked = []
        try:
            occs = design.rootComponent.allOccurrences
            for i in range(occs.count):
                try:
                    if occs.item(i).isReferencedComponent:
                        linked.append(occs.item(i))
                except Exception:
                    pass
        except Exception:
            return
        if not linked:
            return
        done = 0
        for o in linked:
            try:
                if o.breakLink():
                    done += 1
            except Exception:
                pass
        if not done:
            return


def _largest_planar_face(body):
    best, area = None, -1.0
    for face in body.faces:
        try:
            if face.geometry.surfaceType != adsk.core.SurfaceTypes.PlaneSurfaceType:
                continue
        except Exception:
            continue
        if face.area > area:
            best, area = face, face.area
    return best


def _flattens(comp):
    """'OK' if Fusion can flatten this component, else the reason."""
    try:
        if comp.flatPattern:
            return "OK"
    except Exception:
        pass
    face = None
    for body in comp.bRepBodies:
        try:
            if body.isSolid and body.isVisible:
                face = _largest_planar_face(body)
                if face:
                    break
        except Exception:
            pass
    if face is None:
        return "no visible solid body"
    try:
        fp = comp.createFlatPattern(face)
        try:
            fp.deleteMe()
        except Exception:
            pass
        return "OK"
    except Exception:
        return "not sheet metal"


def _check(design):
    status = {}                       # code -> "OK" or reason
    unknown = []
    for comp in design.allComponents:
        code = _part_code(comp.name)
        if code is None:
            if not _HARDWARE.search(comp.name or "") and comp.bRepBodies.count:
                unknown.append(comp.name)
            continue
        if status.get(code) == "OK":
            continue                  # second copy (the 2nd stabilizer)
        status[code] = _flattens(comp)
    for code in CODES:
        status.setdefault(code, "no part with this name")
    return status, unknown


def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        project = app.data.activeProject
        if not project:
            ui.messageBox("Make the candidates project active first.")
            return
        files = [f for f in _collect(project.rootFolder, [])
                 if f.fileExtension and f.fileExtension.lower() == "f3d"
                 and not _is_linked_part(f)]
        if not files:
            ui.messageBox("No designs in project '%s'." % project.name)
            return
        if ui.messageBox(
                "Project: %s\n\n%d design(s) will be opened, checked and closed "
                "WITHOUT saving. Nothing is exported.\nContinue?"
                % (project.name, len(files)), "Check designs",
                adsk.core.MessageBoxButtonTypes.OKCancelButtonType
        ) != adsk.core.DialogResults.DialogOK:
            return
        dlg = ui.createFolderDialog()
        dlg.title = "Folder for the check reports"
        if dlg.showDialog() != adsk.core.DialogResults.DialogOK:
            return
        out = dlg.folder

        rows = []
        progress = ui.createProgressDialog()
        progress.isCancelButtonShown = True
        progress.show("Checking designs", "%v of %m", 0, len(files), 0)
        for i, df in enumerate(files):
            if progress.wasCancelled:
                break
            progress.progressValue = i
            progress.message = df.name
            group, pick, student = _parse_name(df.name)
            row = {"group": group, "pick": pick, "student": student,
                   "file": df.name}
            doc = None
            try:
                doc = app.documents.open(df, True)
                adsk.doEvents()
                design = adsk.fusion.Design.cast(app.activeProduct)
                if not design:
                    row["result"] = "not a Fusion design"
                else:
                    _break_links(design)
                    status, unknown = _check(design)
                    for c in CODES:
                        row[c] = status[c]
                    bad = [NAMES[c] + ": " + status[c] for c in CODES if status[c] != "OK"]
                    row["result"] = "complete" if not bad else "incomplete"
                    row["problems"] = "; ".join(bad)
                    row["other_components"] = ", ".join(unknown[:8])
            except Exception as e:
                row["result"] = "could not open"
                row["problems"] = str(e)[:150]
            finally:
                if doc:
                    try:
                        doc.close(False)          # never save
                    except Exception:
                        pass
            rows.append(row)
        progress.hide()

        cols = ["group", "pick", "student", "result"] + CODES + \
               ["problems", "other_components", "file"]
        rows.sort(key=lambda r: (r["group"], r["pick"]))
        with open(os.path.join(out, "check_results.csv"), "w", newline="",
                  encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)

        best = []
        for g in sorted({r["group"] for r in rows}):
            members = [r for r in rows if r["group"] == g]
            win = next((r for r in members if r.get("result") == "complete"), None)
            best.append({"group": g,
                         "chosen_pick": win["pick"] if win else "",
                         "chosen_student": win["student"] if win else "NONE COMPLETE",
                         "members_checked": len(members),
                         "file": win["file"] if win else ""})
        with open(os.path.join(out, "best_per_group.csv"), "w", newline="",
                  encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["group", "chosen_pick", "chosen_student",
                                              "members_checked", "file"])
            w.writeheader()
            w.writerows(best)

        n_ok = sum(1 for b in best if b["chosen_pick"] != "")
        ui.messageBox("Checked %d design(s).\n\n%d of %d group(s) have a complete "
                      "design.\n\nReports in:\n%s\n   check_results.csv\n"
                      "   best_per_group.csv"
                      % (len(rows), n_ok, len(best), out), "Check designs")
    except Exception:
        if ui:
            ui.messageBox("Failed:\n%s" % traceback.format_exc())
