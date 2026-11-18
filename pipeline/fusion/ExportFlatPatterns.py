"""
ExportFlatPatterns -- Fusion 360 script for MAAE/AERO 2001.

Batch-exports every sheet-metal flat pattern in a design to DXF, with the
export options set correctly for downstream laser processing.

INSTALL
    Fusion -> Utilities tab -> ADD-INS -> Scripts and Add-Ins -> Scripts ->
    the green "+" -> create a script named ExportFlatPatterns, then replace
    the generated .py with this file. Select it and press Run.

WHAT IT DOES
    * Finds every component that has sheet-metal geometry.
    * Creates a flat pattern where one does not already exist, picking the
      largest planar face as the stationary face.
    * Exports each flat pattern to  <output>/<GROUP-ID>/<CODE>.dxf
      where CODE is CHS / LCV / RCV / NUT / LEG / STB -- the same
      code laser-marked on the blank. An unrecognised component
      name keeps its own name and is listed in the summary.
    * Leaves the design as it found it (any flat pattern it created is
      removed again, so students' files are not modified).

WHY THESE EXPORT OPTIONS
    isCenterLinesExported  = True
        Bend centre lines are what the laser scribes so the brake operator
        knows where to fold. Without them the shop is guessing.

    isExtentLinesExported  = False
        Bend EXTENT lines are the two lines either side of the bend. They are
        useful on a drawing and useless on a blank -- and because they are
        open chains sitting inside the profile, they are geometrically
        indistinguishable from bend centre lines. Suppressing them here is
        what keeps the downstream classifier unambiguous.

    isSplineConvertedToPolyline = True
        Many laser controllers either refuse SPLINE entities or approximate
        them badly. Converting at export is safer than hoping.

    units = millimetres
        The post-processor detects and corrects inch files anyway, but there
        is no reason to make it guess.
"""

import os
import re
import traceback

import adsk.core
import adsk.fusion

# ---------------------------------------------------------------- settings --
SPLINE_TOLERANCE_CM = 0.001   # 0.01 mm chord tolerance
CLEANUP_CREATED_FLAT_PATTERNS = True


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


DEPTS = ("MAAE", "AERO")
_SEP = r"[-_.\s]*"
_STRICT = re.compile(
    r"(" + "|".join(DEPTS) + r")" + _SEP + r"(L\s*\d+)" + _SEP +
    r"G(?:ROUP)?" + _SEP + r"0*(\d+)", re.IGNORECASE)


def _clean_group_id(text):
    """
    Reduce whatever was typed to the canonical group ID.

    The mark identifies the GROUP. One person's design is manufactured, but
    the part belongs to the team and is collected from a shared bin, so the
    submitter's name has no place on it.
    """
    t = (text or "").strip()
    # Brightspace names the MAAE groups "MAAE2001 L1 G 1": drop the course number.
    t = re.sub(r"(?i)\b(MAAE|AERO)[\s_-]*2001(?!\d)", r"\1", t)
    m = _STRICT.search(t)
    if m:
        section = re.sub(r"\s+", "", m.group(2)).upper()
        return "%s-%s-G%02d" % (m.group(1).upper(), section, int(m.group(3)))
    return _safe_name(t).upper()


def _largest_planar_face(body):
    """Pick the biggest planar face as the flat-pattern stationary face."""
    best = None
    best_area = -1.0
    for face in body.faces:
        geom = face.geometry
        if geom.surfaceType != adsk.core.SurfaceTypes.PlaneSurfaceType:
            continue
        area = face.area
        if area > best_area:
            best_area = area
            best = face
    return best


def _sheet_metal_bodies(comp):
    """Solid bodies in this component that look like sheet metal."""
    out = []
    for body in comp.bRepBodies:
        if body.isSolid and body.isVisible:
            out.append(body)
    return out


def _safe_name(name):
    bad = '<>:"/\\|?*'
    for ch in bad:
        name = name.replace(ch, "_")
    return name.strip()


def _set_mm_units(opts):
    """
    Force millimetres on the export options.

    The `units` property takes a DistanceUnits enum, whose member spelling has
    not been stable across Fusion releases. Rather than pin one name and break
    on next year's update, try the known spellings in order. If all fail the
    export still succeeds in the design's own units -- the post-processor
    reads $INSUNITS and rescales -- so this is a nicety, not a dependency.
    """
    for owner, member in (
        (adsk.fusion, "DistanceUnits"),
        (adsk.core, "DistanceUnits"),
    ):
        enum = getattr(owner, member, None)
        if enum is None:
            continue
        for spelling in ("MillimeterDistanceUnits", "MillimetersDistanceUnits",
                         "MillimeterDistanceUnit", "mm"):
            val = getattr(enum, spelling, None)
            if val is not None:
                try:
                    opts.units = val
                    return True
                except Exception:
                    pass
    return False



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
    except Exception as e:
        _READ_ERRORS.append((comp.name, str(e)[:120]))
        return None


_READ_ERRORS = []

def run(context):
    _READ_ERRORS.clear()
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        design = adsk.fusion.Design.cast(app.activeProduct)
        if not design:
            ui.messageBox("Open a Fusion design first.")
            return

        # --- group id -------------------------------------------------------
        rv = ui.inputBox(
            "Group ID for this design.\n\n"
            "Use the exact form  SECTION-Gnn, e.g.  L2-G07\n"
            "This becomes the folder name AND the laser mark on every part.",
            "Group ID", "MAAE-L1-G01",
        )
        if not rv or rv[1]:
            return
        # Whatever is typed, only the group code reaches the steel. Typing
        # "MAAE-L1-G07 Tony" is a natural thing to do and must not put a
        # student's name on a part.
        group_id = _clean_group_id(rv[0])
        if not group_id:
            return

        # --- output folder --------------------------------------------------
        dlg = ui.createFolderDialog()
        dlg.title = "Choose the RAW export folder (the pipeline's --raw)"
        if dlg.showDialog() != adsk.core.DialogResults.DialogOK:
            return
        out_root = os.path.join(dlg.folder, group_id)
        os.makedirs(out_root, exist_ok=True)

        export_mgr = design.exportManager

        # A design opened from a .f3z has its parts LINKED, and a linked part
        # is read-only, so Fusion cannot flatten it. Make them local in memory
        # first. Close the design WITHOUT saving afterwards, as always.
        links_broken = 0
        for _ in range(10):
            linked = []
            occs = design.rootComponent.allOccurrences
            for i in range(occs.count):
                try:
                    if occs.item(i).isReferencedComponent:
                        linked.append(occs.item(i))
                except Exception:
                    pass
            if not linked:
                break
            done = 0
            for occ in linked:
                try:
                    if occ.breakLink():
                        done += 1
                except Exception:
                    pass
            links_broken += done
            if not done:
                break

        exported = []
        codes_done = set()
        skipped = []
        created_here = []

        for comp in design.allComponents:
            name = _safe_name(comp.name)
            if not name:
                continue

            flat = _flat_of(comp)

            if flat is None:
                bodies = _sheet_metal_bodies(comp)
                if not bodies:
                    # Bought parts land here too, so only the six are reported.
                    if _part_code(comp.name):
                        skipped.append(f"{name}: no flat pattern and no visible "
                                       f"solid body to make one from -- is it hidden?")
                    continue
                face = None
                for b in bodies:
                    face = _largest_planar_face(b)
                    if face:
                        break
                if face is None:
                    skipped.append(f"{name}: no planar face to flatten from")
                    continue
                try:
                    flat = comp.createFlatPattern(face)
                    created_here.append(comp)
                except Exception as e:
                    skipped.append(f"{name}: not sheet metal / cannot flatten -- if this IS "
                                   f"one of the six parts, open it, create its flat "
                                   f"pattern by hand (Sheet Metal > Flat Pattern), "
                                   f"then run this again")
                    continue

            if flat is None:
                skipped.append(f"{name}: no flat pattern")
                continue

            code = _part_code(comp.name)
            if code and code in codes_done:
                skipped.append(f"{name}: a second component that is also the "
                               f"{code} -- only the first was exported")
                continue
            if code is None:
                skipped.append(f"{name}: NAME not recognised -- exported under "
                               f"its own name, not a part code")
            path = os.path.join(out_root, (code or name) + ".dxf")
            try:
                opts = export_mgr.createDXFFlatPatternExportOptions(path, flat)
                opts.isCenterLinesExported = True
                opts.isExtentLinesExported = False
                opts.isSplineConvertedToPolyline = True
                opts.convertToPolylineTolerance = SPLINE_TOLERANCE_CM
                _set_mm_units(opts)

                if export_mgr.execute(opts):
                    exported.append(name)
                    if code:
                        codes_done.add(code)
                else:
                    skipped.append(f"{name}: export returned false")
            except Exception as e:
                skipped.append(f"{name}: {e}")

        if CLEANUP_CREATED_FLAT_PATTERNS:
            for comp in created_here:
                try:
                    if _flat_of(comp):
                        _flat_of(comp).deleteMe()
                except Exception:
                    pass

        msg = [f"Group {group_id}", f"Exported to:\n{out_root}", ""]
        if not exported and _READ_ERRORS:
            msg += ["Fusion refused to report a flat pattern for %d component(s)"
                    % len(_READ_ERRORS),
                    "(first: %s -- %s)." % _READ_ERRORS[0],
                    "This usually means the open design was left in a bad state",
                    "by an earlier run that stopped part-way. Close this design",
                    "WITHOUT saving, open it again, and re-run the script.", ""]
        msg.append(f"Exported {len(exported)} flat pattern(s):")
        for n in exported:
            msg.append(f"   - {n}")
        if skipped:
            msg.append("")
            msg.append(f"Skipped {len(skipped)}:")
            for s in skipped:
                msg.append(f"   - {s}")

        expected = {"CHS": "Chassie", "LCV": "Left Cover", "RCV": "Right Cover",
                    "NUT": "Nut", "LEG": "Leg", "STB": "Stabalizer"}
        missing = [expected[c] for c in sorted(set(expected) - codes_done)]
        if missing:
            msg.append("")
            msg.append("WARNING -- expected parts not exported: "
                       + ", ".join(sorted(missing)))
            msg.append("Check the component names match the project brief.")

        if links_broken:
            msg.append("")
            msg.append("%d linked part(s) were made local so they could be "
                       "flattened (a .f3z design)." % links_broken)
            msg.append("Close this design WITHOUT saving.")

        ui.messageBox("\n".join(msg), "Flat pattern export")

    except Exception:
        if ui:
            ui.messageBox("Failed:\n{}".format(traceback.format_exc()))
