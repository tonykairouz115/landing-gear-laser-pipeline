"""
BatchExportProject -- Fusion 360 script for MAAE/AERO 2001.

Walks an ENTIRE Fusion project folder, opens every student submission in turn,
exports all six sheet-metal flat patterns from each, and closes without
saving. One run, a full cohort, no manual opening.

This is the piece that makes the pipeline hands-off. ExportFlatPatterns.py
does one open design; this does the whole cohort.

INSTALL
    Fusion -> Utilities -> ADD-INS -> Scripts and Add-Ins -> Scripts -> "+"
    Create a script named BatchExportProject, replace the generated .py with
    this file, select it, Run.

BEFORE YOU RUN IT
    1. In the Fusion data panel, make a project (e.g. "MAAE2001-SELECTED").
    2. Upload the selected .f3d (or .f3z) files into it, each named for its group.
       A .f3z unpacks into the assembly plus its part designs: that is fine,
       the parts are exported through the assembly and not on their own.
       select_designs.py produces exactly this folder. Files are named:
           MAAE-L1-G01.f3d   AERO-L2-G23.f3d   ...
       The filename before the first "." or "_" becomes the group ID and the
       laser mark. Get this right and nothing downstream needs touching.
    3. Make that project the ACTIVE project in the data panel.
    4. Run this script and pick an output folder.

WHAT COMES OUT
    <output>/MAAE-L1-G01/CHS.dxf      Chassie
    <output>/MAAE-L1-G01/LCV.dxf      Left Cover
    <output>/MAAE-L1-G01/RCV.dxf      Right Cover
    <output>/MAAE-L1-G01/NUT.dxf      Nut
    <output>/MAAE-L1-G01/LEG.dxf      Leg
    <output>/MAAE-L1-G01/STB.dxf      Stabalizer  (cut x2 -- see QUANTITIES)
    <output>/_export_log.csv          one row per component, for triage

    Filenames are the three-letter part codes, matching the laser mark on
    the blank itself. Six files per group; seven blanks, because the
    Stabalizer is quantity 2 and the pipeline doubles it downstream.

    Point the Python post-processor at <output> as its --raw directory.

SAFETY
    Documents are opened and closed WITHOUT saving. Flat patterns this script
    creates are deleted again before closing. Student files are not modified.
"""

import csv
import os
import re
import traceback

import adsk.core
import adsk.fusion

SPLINE_TOLERANCE_CM = 0.001          # 0.01 mm chord tolerance
EXPECTED = ["chassie", "left cover", "right cover", "nut", "leg", "stabalizer"]
# The six parts by CODE -- what completeness is checked against, so a group
# that named its part "Chassis" or "Nut Clip" is not reported missing.
EXPECTED_CODES = {"CHS": "Chassie", "LCV": "Left Cover", "RCV": "Right Cover",
                  "NUT": "Nut", "LEG": "Leg", "STB": "Stabalizer"}
RECURSE_SUBFOLDERS = True

# Component name -> three-letter part code, used as the DXF FILENAME.
#
# The same code is laser-marked on the blank ("MAAE L2 G07 CHS"), so the file
# on disk and the physical part in the bucket carry identical identification.
# When a blank comes back wrong, tracing it to its source DXF is exact rather
# than a guess.
#
# Spellings are deliberately generous: the brief specifies "Chassie" and
# "Stabalizer", but students will type the dictionary spellings, and a file
# that fails to match here is a part that does not get cut.
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
    """
    Reduce a component name to a comparable form.

    Students do not type the brief's spelling exactly. They use underscores,
    drop spaces, and leave version numbers on the end. All of these should
    resolve to the same part:

        "Left Cover"   "Left_Cover"   "LeftCover"
        "Left Cover 1" "Left Cover v2"  "LEFT COVER"

    Punctuation is stripped and a trailing version or copy number is dropped.
    Matching is then EXACT on the result, never a substring -- "Rivet Nut"
    must not resolve to "Nut", because the rivet nut is a purchased part and
    marking it as the captive nut clip would put a bought part on the sheet.
    """
    s = (name or "").strip().lower()
    s = re.sub(r"[\s_\-.()\[\]]+", " ", s).strip()
    s = re.sub(r"\s+(?:v\s*\d+|copy|\d+)$", "", s)   # trailing v2 / copy / 1
    return s.replace(" ", "")


PART_CODES = {}
for _code, _spellings in _ACCEPTED.items():
    for _sp in _spellings:
        PART_CODES[_normalise(_sp)] = _code


def _part_code(component_name):
    """
    Three-letter code for a component, or None if the name is unrecognised.

    Unrecognised names deliberately return None rather than an invented code.
    The full component name is then used as the filename and the row is
    flagged in the log -- a naming problem you can see and chase beats a file
    quietly labelled as something it is not.
    """
    return PART_CODES.get(_normalise(component_name))


# ----------------------------------------------------------------- helpers --

def _safe_name(name):
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    return name.strip()


# ---------------------------------------------------------- group identity --
#
# The laser mark must carry the GROUP, never the student. One design per group
# is manufactured, but the file it came from is one person's submission, and
# that person's name has no business on a part the whole team collects from a
# communal bin -- or on a part another team might pick up by mistake.
#
# So the group ID is extracted, not inherited. Anything that is not the
# department, the lab section and the group number is discarded, wherever in
# the filename it sits.
DEPTS = ("MAAE", "AERO")
_SEP = r"[-_.\s]*"
_STRICT = re.compile(
    r"(" + "|".join(DEPTS) + r")" + _SEP + r"(L\s*\d+)" + _SEP +
    r"G(?:ROUP)?" + _SEP + r"0*(\d+)", re.IGNORECASE)
_LOOSE = re.compile(r"([A-Za-z]+[A-Za-z0-9]*(?:-[A-Za-z0-9]+)*?-[Gg]\s*0*(\d+))")


def _group_id_from(filename):
    """
    'MAAE-L1-G07.f3d'                        -> 'MAAE-L1-G07'
    'MAAE-L1-G07_Kairouz_Tony.f3d'           -> 'MAAE-L1-G07'
    'Tony Kairouz MAAE L1 G7.f3d'            -> 'MAAE-L1-G07'
    '204418 - Tony K - ... - MAAE-L1-G07.f3d'-> 'MAAE-L1-G07'
    'landing gear final.f3d'                 -> 'UNREADABLE-NAME'

    A filename with no group ID in it deliberately does NOT fall back to the
    filename itself. Doing that is how a student's name ends up laser-marked
    on steel. An obviously wrong folder name is noticed; a plausible one is
    not.
    """
    base = os.path.splitext(filename)[0].strip()
    # Brightspace names the MAAE groups "MAAE2001 L1 G 1": drop the course number.
    base = re.sub(r"(?i)\b(MAAE|AERO)[\s_-]*2001(?!\d)", r"\1", base)

    m = _STRICT.search(base)
    if m:
        section = re.sub(r"\s+", "", m.group(2)).upper()
        return "%s-%s-G%02d" % (m.group(1).upper(), section, int(m.group(3)))

    m = _LOOSE.search(base)
    if m:
        whole, number = m.group(1), m.group(2)
        head = whole[:whole.upper().rindex("-G")]
        return "%s-G%02d" % (head.upper(), int(number))

    return "UNREADABLE-NAME"


def _largest_planar_face(body):
    best, best_area = None, -1.0
    for face in body.faces:
        if face.geometry.surfaceType != adsk.core.SurfaceTypes.PlaneSurfaceType:
            continue
        if face.area > best_area:
            best_area, best = face.area, face
    return best


def _set_mm_units(opts):
    """DistanceUnits member spelling has moved between releases; try each."""
    for owner in (adsk.fusion, adsk.core):
        enum = getattr(owner, "DistanceUnits", None)
        if enum is None:
            continue
        for spelling in ("MillimeterDistanceUnits", "MillimetersDistanceUnits",
                         "MillimeterDistanceUnit"):
            val = getattr(enum, spelling, None)
            if val is not None:
                try:
                    opts.units = val
                    return True
                except Exception:
                    pass
    return False


def _collect_files(folder, out, recurse):
    for i in range(folder.dataFiles.count):
        out.append(folder.dataFiles.item(i))
    if recurse:
        for i in range(folder.dataFolders.count):
            _collect_files(folder.dataFolders.item(i), out, True)
    return out



# ------------------------------------------------------------- .f3z support --
#
# A student whose parts were built as separate designs and inserted into the
# assembly can only export a .f3z (a Fusion archive). Uploaded to Fusion, a
# .f3z unpacks into the assembly PLUS one design per linked part. Two things
# follow, and both are handled here:
#
#   1. The part designs must not be exported as groups of their own. They are
#      named "Chassie", "Leg" ... with no group ID, and each is already inside
#      the assembly. A design that another design references is skipped.
#   2. In the assembly the parts are LINKED, and a linked component is
#      read-only, so Fusion cannot make its flat pattern. The links are broken
#      in memory first. The document is closed without saving, exactly as for
#      every other file, so nothing changes in Fusion or in the student's file.

def _is_linked_part(data_file):
    """True if another design references this one (a part of a .f3z)."""
    try:
        return bool(data_file.hasParentReferences)
    except Exception:
        return False          # older Fusion: treat as a normal design


def _parent_name(data_file):
    try:
        refs = data_file.parentReferences
        if refs and refs.count:
            return refs.item(0).name
    except Exception:
        pass
    return "an assembly"


def _break_links(design):
    """Make every linked component local, in memory. Returns how many."""
    broken = 0
    for _ in range(10):                      # nested links: repeat until none
        linked = []
        try:
            occs = design.rootComponent.allOccurrences
            for i in range(occs.count):
                occ = occs.item(i)
                try:
                    if occ.isReferencedComponent:
                        linked.append(occ)
                except Exception:
                    pass
        except Exception:
            break
        if not linked:
            break
        progress = 0
        for occ in linked:
            try:
                if occ.breakLink():
                    broken += 1
                    progress += 1
            except Exception:
                pass
        if not progress:
            break
    return broken

# ------------------------------------------------------------ per-document --


def _flat_of(comp):
    """
    The component's existing flat pattern, or None.

    Reading comp.flatPattern RAISES (RuntimeError: InternalValidationError)
    on some components instead of returning None -- typically a bought part,
    a linked component, or one whose sheet metal state Fusion considers
    invalid. One such component used to abort the whole export part-way
    through the list, so the parts after it were never written.
    """
    try:
        return comp.flatPattern
    except Exception:
        return None

def _export_document(design, out_root, group_id, log):
    """Export every flat pattern in `design`. Returns list of exported names."""
    export_mgr = design.exportManager
    gdir = os.path.join(out_root, group_id)
    os.makedirs(gdir, exist_ok=True)

    exported = []
    codes_done = set()
    created_here = []

    for comp in design.allComponents:
        name = _safe_name(comp.name)
        if not name:
            continue

        flat = _flat_of(comp)
        if flat is None:
            face = None
            for body in comp.bRepBodies:
                if not (body.isSolid and body.isVisible):
                    continue
                face = _largest_planar_face(body)
                if face:
                    break
            if face is None:
                continue                      # not a sheet-metal component
            try:
                flat = comp.createFlatPattern(face)
                created_here.append(comp)
            except Exception:
                log.append([group_id, name, "SKIP",
                            "no flat pattern and Fusion could not make one -- if this is one of "
                            "the six parts, create its flat pattern by hand and re-run"])
                continue

        if flat is None:
            continue

        code = _part_code(comp.name)
        if code and code in codes_done:
            # Two components that both mean the same part ("Nut" and "Nut
            # Clip"): the second would overwrite the first's file.
            log.append([group_id, name, "SKIP",
                        "a second component that is also the %s (%s) -- only "
                        "the first was exported; check which one is right"
                        % (EXPECTED_CODES[code], code)])
            continue
        path = os.path.join(gdir, (code or name) + ".dxf")
        if code is None:
            log.append([group_id, name, "NAME",
                        "component name not recognised -- exported as '%s.dxf'; "
                        "expected one of Chassie / Left Cover / Right Cover / "
                        "Nut / Leg / Stabalizer" % name])
        try:
            opts = export_mgr.createDXFFlatPatternExportOptions(path, flat)
            opts.isCenterLinesExported = True      # bend scribes -> MARK_BEND
            opts.isExtentLinesExported = False     # keeps bend lines unambiguous
            opts.isSplineConvertedToPolyline = True
            opts.convertToPolylineTolerance = SPLINE_TOLERANCE_CM
            _set_mm_units(opts)

            if export_mgr.execute(opts):
                exported.append(name)
                if code:
                    codes_done.add(code)
                log.append([group_id, name, "OK", ""])
            else:
                log.append([group_id, name, "FAIL", "export returned false"])
        except Exception as e:
            log.append([group_id, name, "FAIL", str(e)[:180]])

    # Leave the student's design exactly as we found it.
    for comp in created_here:
        try:
            if _flat_of(comp):
                _flat_of(comp).deleteMe()
        except Exception:
            pass

    for code in sorted(set(EXPECTED_CODES) - codes_done):
        log.append([group_id, EXPECTED_CODES[code], "MISSING",
                    "no component recognised as the %s (%s) was exported -- "
                    "check its name, or that it is sheet metal"
                    % (EXPECTED_CODES[code], code)])

    return exported, codes_done


# ---------------------------------------------------------------- entry pt --

def run(context):
    ui = None
    docs_opened = []
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface

        project = app.data.activeProject
        if not project:
            ui.messageBox("No active project. Pick the submissions project in "
                          "the data panel first.")
            return

        files = _collect_files(project.rootFolder, [], RECURSE_SUBFOLDERS)
        all_designs = [f for f in files
                       if f.fileExtension and f.fileExtension.lower() == "f3d"]
        # Part designs unpacked from a .f3z are exported through their
        # assembly, not on their own (see _is_linked_part).
        linked_parts = [f for f in all_designs if _is_linked_part(f)]
        designs = [f for f in all_designs if not _is_linked_part(f)]

        if not designs:
            ui.messageBox(
                "No Fusion designs found in project '{}'.\n\n"
                "Upload the selected .f3d / .f3z files into that project first."
                .format(project.name))
            return

        preview = "\n".join(
            "   {}  ->  {}".format(d.name, _group_id_from(d.name))
            for d in designs[:12])
        more = "\n   ... and {} more".format(len(designs) - 12) \
            if len(designs) > 12 else ""

        res = ui.messageBox(
            "Project: {}\n\n"
            "Found {} design(s). Group IDs will be read from the file names:\n\n"
            "{}{}\n\n"
            "{}"
            "Each will be opened, exported, and closed WITHOUT saving.\n"
            "Continue?".format(project.name, len(designs), preview, more,
                               ("{} part design(s) from .f3z uploads will be "
                                "exported through their assembly, not on their "
                                "own.\n\n".format(len(linked_parts))
                                if linked_parts else "")),
            "Batch flat pattern export",
            adsk.core.MessageBoxButtonTypes.OKCancelButtonType)
        if res != adsk.core.DialogResults.DialogOK:
            return

        dlg = ui.createFolderDialog()
        dlg.title = "Choose the RAW output folder (the pipeline's --raw)"
        if dlg.showDialog() != adsk.core.DialogResults.DialogOK:
            return
        out_root = dlg.folder

        log = [["group", "component", "status", "detail"]]
        for f in linked_parts:
            log.append(["-", f.name, "PART",
                        "part of a .f3z, exported through '{}'".format(_parent_name(f))])
        ok_groups, bad_groups = [], []

        progress = ui.createProgressDialog()
        progress.isCancelButtonShown = True
        progress.show("Exporting flat patterns", "%v of %m -- %p%",
                      0, len(designs), 0)

        # Two files can resolve to the SAME group ID -- two unreadable names
        # both becoming UNREADABLE-NAME, or two students' files for one group
        # uploaded by mistake. Writing both into one folder would silently
        # replace the first group's parts with the second's, and the folder
        # would look perfectly normal afterwards. So a collision gets its own
        # folder and a loud row in the log.
        seen_ids = {}

        for idx, data_file in enumerate(designs):
            if progress.wasCancelled:
                break
            progress.progressValue = idx
            progress.message = ("{} of {}  --  {}"
                                .format(idx + 1, len(designs), data_file.name))

            base_id = _group_id_from(data_file.name)
            n = seen_ids.get(base_id, 0)
            seen_ids[base_id] = n + 1
            if n:
                group_id = "{}--DUPLICATE-{}".format(base_id, n + 1)
                log.append([group_id, "-", "DUPLICATE",
                            "'{}' also resolves to {}. Both were exported to "
                            "separate folders -- delete the wrong one before "
                            "building the sheet, or that group gets cut twice."
                            .format(data_file.name, base_id)])
            else:
                group_id = base_id

            doc = None
            try:
                doc = app.documents.open(data_file, True)
                docs_opened.append(doc)
                adsk.doEvents()

                design = adsk.fusion.Design.cast(app.activeProduct)
                if not design:
                    log.append([group_id, "-", "FAIL", "not a Fusion design"])
                    bad_groups.append(group_id)
                    continue

                n_links = _break_links(design)
                if n_links:
                    log.append([group_id, "-", "LINKS",
                                "{} linked part(s) made local in memory so their "
                                "flat patterns could be made (.f3z upload; the file "
                                "is not saved)".format(n_links)])
                exported, codes = _export_document(design, out_root, group_id, log)
                (ok_groups if set(EXPECTED_CODES) <= codes
                 else bad_groups).append(group_id)

            except Exception as e:
                log.append([group_id, "-", "FAIL", str(e)[:180]])
                bad_groups.append(group_id)
            finally:
                if doc:
                    try:
                        doc.close(False)          # False = do not save
                    except Exception:
                        pass

        progress.hide()

        log_path = os.path.join(out_root, "_export_log.csv")
        try:
            with open(log_path, "w", newline="") as f:
                csv.writer(f).writerows(log)
        except Exception:
            log_path = "(could not write log)"

        n_fail = sum(1 for r in log[1:] if r[2] in ("FAIL", "MISSING", "SKIP"))
        msg = [
            "Batch export finished.",
            "",
            "Output folder:  {}".format(out_root),
            "Log:            {}".format(log_path),
            "",
            "Designs processed:      {}".format(len(ok_groups) + len(bad_groups)),
            "Complete (6 parts):     {}".format(len(ok_groups)),
            "Incomplete or failed:   {}".format(len(bad_groups)),
            "Component-level issues: {}".format(n_fail),
        ]
        if bad_groups:
            msg += ["", "Needs attention:"]
            msg += ["   " + g for g in bad_groups[:25]]
            if len(bad_groups) > 25:
                msg.append("   ... and {} more".format(len(bad_groups) - 25))
        msg += ["", "Next step:",
                "   Press 'Build the sheet' in the Landing Gear Pipeline window,",
                "   or run 3_run_pipeline.bat. (By hand:",
                "   python -m lgpipe.cli --raw \"{}\" --out out "
                "--config shop.json --roster roster.txt)".format(out_root)]

        ui.messageBox("\n".join(msg), "Batch flat pattern export")

    except Exception:
        if ui:
            ui.messageBox("Failed:\n{}".format(traceback.format_exc()))
