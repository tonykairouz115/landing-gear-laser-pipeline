"""
FingerprintDesigns -- Fusion 360 script for the submission check. OPTIONAL.

Opens every design in the active Fusion project, writes down how it was
built, and closes it WITHOUT saving. Nothing is exported and no student file
is changed.

check_submissions.py reads the result (--fusion-data) for the columns only
Fusion can fill: build history, exact sketch dimensions, geometry, custom
names and saved view. Everything else in the check works without this.

INSTALL
    Fusion -> Utilities -> ADD-INS -> Scripts and Add-Ins -> Scripts -> "+"
    Create a script named FingerprintDesigns, replace the generated .py with
    this file, select it, Run.

BEFORE YOU RUN IT
    1. Make a Fusion project (e.g. "2001-ALL-SUBMISSIONS"). Keep it separate
       from the project the laser pipeline uses.
    2. Upload ALL the students' .f3d files into it, without renaming them.
       The check matches each fingerprint to its student by file name.
    3. Make that project the ACTIVE project in the data panel.
    4. Run this script and pick an empty output folder.

WHAT COMES OUT
    <output>/<design name>.json    one per design
    <output>/_fingerprint_log.csv  OK / FAIL per design
"""

import csv
import json
import os
import re
import traceback

import adsk.core
import adsk.fusion

RECURSE_SUBFOLDERS = True


def _collect_files(folder, out, recurse):
    for i in range(folder.dataFiles.count):
        out.append(folder.dataFiles.item(i))
    if recurse:
        for i in range(folder.dataFolders.count):
            _collect_files(folder.dataFolders.item(i), out, True)
    return out


def _safe(name):
    return re.sub(r'[<>:"/\\|?*]+', "_", name).strip() or "design"


def _try(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _fingerprint(app, design, name):
    fp = {"name": name, "components": [], "bodies": [], "sketches": [],
          "features": [], "feature_names": [], "dimensions": [],
          "parameters": [], "bodies_geometry": [], "camera": None}

    # Build history: the timeline, in order. Feature TYPES (not names) are
    # what get compared, so renaming features doesn't hide a copied history.
    tl = _try(lambda: design.timeline)
    if tl:
        for i in range(tl.count):
            item = _try(lambda: tl.item(i))
            if not item:
                continue
            ent = _try(lambda: item.entity)
            kind = _try(lambda: ent.objectType, "") if ent else ""
            fp["features"].append(kind.split("::")[-1] if kind else "?")
            fp["feature_names"].append(_try(lambda: item.name, "") or "")

    comps = _try(lambda: design.allComponents)
    for c in range(comps.count if comps else 0):
        comp = comps.item(c)
        fp["components"].append(comp.name)

        for b in range(comp.bRepBodies.count):
            body = comp.bRepBodies.item(b)
            fp["bodies"].append(body.name)
            # Internal units: cm and cm^2 / cm^3. Only compared like for like.
            fp["bodies_geometry"].append({
                "component": comp.name, "body": body.name,
                "volume": _try(lambda: body.volume),
                "area": _try(lambda: body.area),
                "faces": _try(lambda: body.faces.count),
                "edges": _try(lambda: body.edges.count),
            })

        for s in range(comp.sketches.count):
            sk = comp.sketches.item(s)
            fp["sketches"].append(sk.name)
            dims = _try(lambda: sk.sketchDimensions)
            for d in range(dims.count if dims else 0):
                dim = dims.item(d)
                p = _try(lambda: dim.parameter)
                if not p:
                    continue
                unit = _try(lambda: p.unit, "") or ""
                val = _try(lambda: p.value)
                if val is None:
                    continue
                # Lengths in mm, angles in degrees, so "23.47" reads as typed.
                if "deg" in unit or "rad" in unit:
                    val = val * 57.29577951308232
                else:
                    val = val * 10.0
                fp["dimensions"].append(round(val, 6))

    params = _try(lambda: design.allParameters)
    for i in range(params.count if params else 0):
        p = params.item(i)
        fp["parameters"].append({
            "name": _try(lambda: p.name, ""),
            "expression": _try(lambda: p.expression, ""),
            "unit": _try(lambda: p.unit, ""),
            "value": _try(lambda: p.value),
        })

    # The view the file was saved with.
    cam = _try(lambda: app.activeViewport.camera)
    if cam:
        def pt(p):
            return [round(p.x, 4), round(p.y, 4), round(p.z, 4)]
        fp["camera"] = {"eye": pt(cam.eye), "target": pt(cam.target),
                        "up": pt(cam.upVector)}
    return fp


def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface

        project = app.data.activeProject
        if not project:
            ui.messageBox("No active project. Pick the submissions project in "
                          "the data panel first.")
            return
        files = _collect_files(project.rootFolder, [], RECURSE_SUBFOLDERS)
        designs = [f for f in files
                   if f.fileExtension and f.fileExtension.lower() == "f3d"]
        if not designs:
            ui.messageBox("No Fusion designs found in project '{}'."
                          .format(project.name))
            return

        res = ui.messageBox(
            "Project: {}\n\nFound {} design(s).\n\nEach will be opened, read, "
            "and closed WITHOUT saving. This takes a while for a full class.\n"
            "Continue?".format(project.name, len(designs)),
            "Fingerprint designs",
            adsk.core.MessageBoxButtonTypes.OKCancelButtonType)
        if res != adsk.core.DialogResults.DialogOK:
            return

        dlg = ui.createFolderDialog()
        dlg.title = "Choose an empty folder for the fingerprints"
        if dlg.showDialog() != adsk.core.DialogResults.DialogOK:
            return
        out_root = dlg.folder

        log = [["design", "status", "detail"]]
        progress = ui.createProgressDialog()
        progress.isCancelButtonShown = True
        progress.show("Fingerprinting designs", "%v of %m -- %p%",
                      0, len(designs), 0)

        used = set()
        for idx, data_file in enumerate(designs):
            if progress.wasCancelled:
                log.append(["-", "CANCELLED", "stopped by user"])
                break
            progress.progressValue = idx
            progress.message = "{} of {}  --  {}".format(
                idx + 1, len(designs), data_file.name)
            doc = None
            try:
                doc = app.documents.open(data_file, True)
                adsk.doEvents()
                design = adsk.fusion.Design.cast(app.activeProduct)
                if not design:
                    log.append([data_file.name, "FAIL", "not a Fusion design"])
                    continue
                fp = _fingerprint(app, design, data_file.name)
                base = _safe(data_file.name)
                fn, n = base, 2
                while fn.lower() in used:
                    fn, n = "{} ({})".format(base, n), n + 1
                used.add(fn.lower())
                with open(os.path.join(out_root, fn + ".json"), "w",
                          encoding="utf-8") as f:
                    json.dump(fp, f, indent=1)
                log.append([data_file.name, "OK",
                            "{} features, {} bodies".format(
                                len(fp["features"]), len(fp["bodies"]))])
            except Exception as e:
                log.append([data_file.name, "FAIL", str(e)[:180]])
            finally:
                if doc:
                    try:
                        doc.close(False)          # False = do not save
                    except Exception:
                        pass
        progress.hide()

        log_path = os.path.join(out_root, "_fingerprint_log.csv")
        try:
            with open(log_path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(log)
        except Exception:
            log_path = "(could not write log)"

        ok = sum(1 for r in log[1:] if r[1] == "OK")
        ui.messageBox(
            "Done.\n\nFingerprinted: {}\nFailed: {}\n\nFolder: {}\nLog: {}\n\n"
            "Next: run check_submissions.py with\n   --fusion-data \"{}\""
            .format(ok, len(log) - 1 - ok, out_root, log_path, out_root),
            "Fingerprint designs")
    except Exception:
        if ui:
            ui.messageBox("Failed:\n{}".format(traceback.format_exc()))
