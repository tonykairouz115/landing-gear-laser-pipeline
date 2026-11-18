"""
MakeF3D -- Fusion 360 script: turn the open design (for example one opened
from a .f3z) into a single self-contained .f3d.

It breaks every link to other designs (so all parts live inside this one
file) and exports a .f3d under the name you type. The original file is not
changed: close the design WITHOUT saving afterwards.

INSTALL: Scripts and Add-Ins -> Scripts -> "+" -> Create, name it MakeF3D,
replace its .py with this file.

USE: open the TOP assembly, run MakeF3D, type the group ID (AERO-L1-G12),
pick the folder (C:\\group_recheck\\winners).
"""

import os
import re
import traceback

import adsk.core
import adsk.fusion


def _break_links(design):
    total = 0
    for _ in range(10):                      # nested links: repeat until none
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
        for o in linked:
            try:
                if o.breakLink():
                    done += 1
            except Exception:
                pass
        total += done
        if not done:
            break
    left = 0
    occs = design.rootComponent.allOccurrences
    for i in range(occs.count):
        try:
            if occs.item(i).isReferencedComponent:
                left += 1
        except Exception:
            pass
    return total, left


def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        design = adsk.fusion.Design.cast(app.activeProduct)
        if not design:
            ui.messageBox("Open the top assembly first.")
            return

        rv = ui.inputBox("File name for the .f3d (the group ID, e.g. AERO-L1-G12):",
                         "Make .f3d", "")
        if not rv or rv[1] or not rv[0].strip():
            return
        name = re.sub(r'[<>:"/\\|?*]+', "_", rv[0].strip())
        if name.lower().endswith(".f3d"):
            name = name[:-4]

        dlg = ui.createFolderDialog()
        dlg.title = "Folder to save the .f3d in"
        if dlg.showDialog() != adsk.core.DialogResults.DialogOK:
            return
        path = os.path.join(dlg.folder, name + ".f3d")

        broken, left = _break_links(design)

        opts = design.exportManager.createFusionArchiveExportOptions(path)
        ok = design.exportManager.execute(opts)

        msg = ["Saved:" if ok else "Export FAILED:", path, "",
               "Links broken: %d" % broken]
        if left:
            msg.append("WARNING: %d part(s) are still linked -- those parts may "
                       "be missing from the .f3d." % left)
        msg += ["", "Close this design WITHOUT saving."]
        ui.messageBox("\n".join(msg), "Make .f3d")
    except Exception:
        if ui:
            ui.messageBox("Failed:\n%s" % traceback.format_exc())
