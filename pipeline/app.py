"""
Landing Gear Pipeline -- desktop front end.

WHY THIS EXISTS
---------------
The pipeline was driven by two Windows .bat files. That works for whoever
wrote them and nobody else: it is Windows-only, it exposes no settings, and
when something goes wrong a console window closes before you can read why.

This is the same pipeline behind a window. It runs identically on Windows and
macOS, it shows the settings rather than hiding them in a JSON file, and every
run streams its output into a log you can read, scroll back through, and copy
into an email.

BUILT ON tkinter, which ships inside Python itself. No install, no packaging,
no code signing, no second thing to keep working. The only libraries needed
are the three the pipeline already uses, and the launcher installs those.

WHAT IT DOES NOT DO
-------------------
Step 2 -- the Fusion export -- cannot be automated from here. Fusion scripts
only run inside Fusion. So that step is instructions and a folder button,
honestly labelled, rather than a button that pretends.

    python app.py          (or double-click a launcher)
"""

from __future__ import annotations

import json
import os
import platform
import queue
import shutil
import subprocess
import sys
import threading
import zipfile

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
PREFS = os.path.join(HERE, ".app_prefs.json")
SHOP = os.path.join(HERE, "shop.json")

MAC = platform.system() == "Darwin"

# ---- palette: cold-rolled steel, machine-guard amber -----------------------
INK = "#12161a"
GROUND = "#e9ebed"
PANEL = "#f6f7f8"
EDGE = "#c3c9ce"
MUTED = "#5b6670"
AMBER = "#f2a41c"
GREEN = "#1f7a46"
RED = "#a8342a"
LOGBG = "#12161a"
LOGFG = "#e6e9ea"

STEPS = [
    ("term", "Each semester", "Roster, draw seed, group size - the things that change each term."),
    ("select", "1  Select designs", "Draw one submission per group, at random and on the record."),
    ("fusion", "2  Export in Fusion", "Turn the selected designs into flat-pattern DXFs."),
    ("build", "3  Build the sheet", "Mark, nest and validate every blank for the laser."),
]

# The laser's own colour list, in its order, with the AutoCAD Colour Index and
# true RGB that make each one land on that row of the machine's settings table.
# Naming them the way the machine names them means the layer scheme can be set
# by reading the operator's screen, not by translating colour numbers.
#
# Black is 26,26,26 and not 0,0,0 on purpose. Pure black's RGB value is zero,
# and a reader that checks "was a colour set?" before using it reads zero as
# "no", then substitutes its own black. CorelDRAW's is #1A1A1A, which is what
# the files that already work at the shop arrive as -- so that is what the
# machine's black is keyed to, and what this writes. Any row's exact value can
# be overridden in the box beside it on the Settings page.
MACHINE_COLOURS = [
    ("Black",   7, (26, 26, 26)),
    ("Red",     1, (255, 0, 0)),
    ("Green",   3, (0, 255, 0)),
    ("Yellow",  2, (255, 255, 0)),
    ("Blue",    5, (0, 0, 255)),
    ("Magenta", 6, (255, 0, 255)),
    ("Cyan",    4, (0, 255, 255)),
]
COLOUR_BY_NAME = {n: (a, rgb) for n, a, rgb in MACHINE_COLOURS}
COLOUR_SWATCH = {n: "#%02x%02x%02x" % rgb for n, _a, rgb in MACHINE_COLOURS}

# Internal role -> what it is, in the operator's language.
ROLES = [
    ("CUT_OUTER",  "Outer profile", "through cut, runs last"),
    ("CUT_INNER",  "Holes",         "through cut, before the outer"),
    ("MARK_TEXT",  "Group ID",      "mark, or engrave if filled"),
    ("MARK_BEND",  "Bend lines",    "scribe for the brake"),
    ("REF_EXTENT", "Bend extents",  "reference - must NOT be output"),
    ("REF_FRAME",  "Sheet frame",   "reference - must NOT be output"),
]

# Rows whose lines can be FILLED (engraved) instead of followed, with what
# their thickness box means and its default / allowed range. Every other row
# is locked to hairline: a filled cut outline would engrave the whole part, and
# a reference line must never be output at all. Mirrors lgpipe.layers.FILLABLE.
FILLABLE = {
    "MARK_TEXT": ("% of letter height", 13.0, 5.0, 25.0),
    "MARK_BEND": ("mm wide", 0.5, 0.1, 2.0),
}
ROLE_DEFAULT_COLOUR = {
    "CUT_OUTER": "Red", "CUT_INNER": "Blue", "MARK_TEXT": "Magenta",
    "MARK_BEND": "Green", "REF_EXTENT": "Cyan", "REF_FRAME": "Yellow",
}


def colour_name_for(aci, rgb=None):
    """Closest machine colour name for a stored aci / rgb pair."""
    for name, a, _ in MACHINE_COLOURS:
        if a == aci:
            return name
    return "Red"


def count_f3d(folder):
    n = 0
    for _dp, _dn, fns in os.walk(folder or ""):
        n += sum(1 for f in fns if f.lower().endswith((".f3d", ".f3z")))
    return n


# --------------------------------------------------------------- utilities --

def load_json(path, fallback=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {} if fallback is None else fallback


def save_json(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


SHOP_STARTER = {
    "sheet": "1219.2x1219.2",
    "material_thickness": 1.52,
    "kerf": 0.0,
    "part_gap": 4.0,
    "sheet_margin": 8.0,
    "mark": "group-code",
    "nest_mode": "corner",
    "font": "angular",
    "colours_confirmed": False,
    "seed": "2026",
    "expect_lo": 3,
    "expect_hi": 4,
    "_comment": "MAAE/AERO 2001. Sheet is 48x48 inch. kerf 0 = the MACHINE "
                "applies cut compensation (outer profile outside the line, "
                "holes inside). Only set a kerf here if the laser cannot do "
                "it, and then switch machine compensation OFF. No 'layers' "
                "block yet, which is deliberate: it makes every run warn "
                "until someone cuts the test card and sets the six colours.",
}


def _ensure_shop():
    """
    shop.json is the course's own file. It holds the sheet size, the seed, and
    above all the six laser colours someone set after cutting a test card -- the
    setting that decides whether the machine marks a bend line or cuts along it.

    So a new copy of this folder must never land on top of it. The download
    carries shop.default.json instead, and the real file is made from it here,
    once. Unzipping an update over an existing folder is then always safe.
    """
    if os.path.exists(SHOP):
        return
    src = os.path.join(HERE, "shop.default.json")
    try:
        if os.path.exists(src):
            shutil.copyfile(src, SHOP)
            return
    except Exception:
        pass
    save_json(SHOP, SHOP_STARTER)


def reveal(path):
    """Open a folder in Explorer / Finder."""
    if not path or not os.path.isdir(path):
        return False
    try:
        if platform.system() == "Windows":
            os.startfile(path)                      # noqa: S606
        elif MAC:
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False


def pack_path(path):
    """
    Store a folder the way it should travel.

    A folder inside the project is stored RELATIVE to it, so the whole
    directory can be copied to another machine, another drive, or handed to
    next year's TA and still open with the right folders. Only a folder the
    user deliberately put somewhere else is stored as an absolute path.
    """
    if not path:
        return ""
    try:
        rel = os.path.relpath(os.path.abspath(path), HERE)
    except ValueError:                       # different drive on Windows
        return os.path.abspath(path)
    return rel if not rel.startswith("..") else os.path.abspath(path)


def looks_absolute(p):
    """
    Absolute on ANY platform, not just this one.

    os.path.isabs is platform-specific: on macOS it says "C:\\Users\\..." is a
    relative path, and joining that to the project folder produces nonsense.
    Since this folder is meant to travel between a Windows laptop and a Mac,
    a foreign absolute path has to be recognised as absolute here.
    """
    if not p:
        return False
    if p.startswith("/") or p.startswith("\\\\"):     # POSIX, or a UNC share
        return True
    return len(p) > 2 and p[1] == ":" and p[2] in "\\/"   # C:\ or C:/


def unpack_path(stored, default_name):
    """
    Resolve a stored folder, falling back when it no longer makes sense.

    A path saved on someone else's computer points at a folder that does not
    exist here. Rather than opening with four dead paths and an error on the
    first click, fall back to the standard folder beside the app.
    """
    fallback = os.path.join(HERE, default_name)
    if not stored:
        return fallback
    if looks_absolute(stored):
        resolved = os.path.normpath(stored)
        # Not on this machine (or not on this operating system) -- start fresh.
        return resolved if os.path.isdir(resolved) else fallback
    return os.path.normpath(os.path.join(HERE, stored))


def shorten(path, n=52):
    if not path:
        return "not set"
    return path if len(path) <= n else "..." + path[-(n - 3):]


# ------------------------------------------------------------------- app ----

class App:
    def __init__(self, root):
        self.root = root
        self.prefs = load_json(PREFS)
        _ensure_shop()
        self.shop = load_json(SHOP, {})
        self.proc = None
        self.q = queue.Queue()
        self.current = "select"

        root.title("Landing Gear Pipeline")
        # As big as the screen comfortably allows, up to 1180 x 860: the colour
        # table and the draw button need the width and height. Smaller screens
        # get the scroll bar.
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry("%dx%d" % (min(1180, max(900, sw - 80)), min(860, max(640, sh - 120))))
        root.minsize(820, 560)
        root.configure(bg=GROUND)

        self._style()
        self._build()
        self._show("select")
        self._pump()
        self.root.after(200, self.check_libs_async)

    # ---- look ----
    def _style(self):
        s = ttk.Style()
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        base = ("Segoe UI", 10) if platform.system() == "Windows" else (
            ("Helvetica Neue", 12) if MAC else ("DejaVu Sans", 10))
        s.configure(".", background=GROUND, foreground=INK, font=base)
        s.configure("TFrame", background=GROUND)
        s.configure("Panel.TFrame", background=PANEL, relief="flat")
        s.configure("TLabel", background=GROUND, foreground=INK)
        s.configure("Panel.TLabel", background=PANEL)
        s.configure("H1.TLabel", font=(base[0], 19, "bold"))
        s.configure("H2.TLabel", font=(base[0], 13, "bold"), background=PANEL)
        s.configure("Sub.TLabel", foreground=MUTED, background=PANEL)
        s.configure("Nav.TLabel", background=GROUND, foreground=MUTED,
                    font=(base[0], 11), padding=(14, 11))
        s.configure("NavOn.TLabel", background=PANEL, foreground=INK,
                    font=(base[0], 11, "bold"), padding=(14, 11))
        s.configure("Path.TLabel", foreground=MUTED, background=PANEL,
                    font=("Consolas" if platform.system() == "Windows" else "Menlo", 9))
        s.configure("TButton", padding=(12, 7))
        s.configure("Go.TButton", padding=(18, 9), font=(base[0], 11, "bold"))
        s.configure("TEntry", fieldbackground="#ffffff")
        s.configure("TCombobox", fieldbackground="#ffffff")

    def _build(self):
        head = ttk.Frame(self.root, padding=(18, 14, 18, 8))
        head.pack(fill="x")
        self.headrow = head
        ttk.Label(head, text="Landing Gear Pipeline", style="H1.TLabel").pack(side="left")
        self.status = ttk.Label(head, text="Ready", foreground=MUTED)
        self.status.pack(side="right")

        # A strip under the title for the one condition that stops everything
        # working: the three libraries not being installed for THIS Python.
        # Telling someone to open a terminal is where a tool stops being usable
        # by the person it was built for, so the fix is a button.
        self.fixbar = ttk.Frame(self.root, padding=(18, 0, 18, 10))
        self.fix_msg = ttk.Label(self.fixbar, text="", foreground=RED,
                                 wraplength=700, justify="left")
        self.fix_msg.pack(side="left", fill="x", expand=True)
        self.fix_btn = ttk.Button(self.fixbar, text="Install them now",
                                  style="Go.TButton", command=self.install_libs)
        self.fix_btn.pack(side="right")

        self.logwrap = ttk.Frame(self.root, padding=(18, 0, 18, 14))
        self.logwrap.pack(side="bottom", fill="x")

        body = ttk.Frame(self.root, padding=(18, 0, 18, 8))
        body.pack(fill="both", expand=True)

        nav = ttk.Frame(body, width=205)
        nav.pack(side="left", fill="y", padx=(0, 14))
        nav.pack_propagate(False)
        self.nav = {}
        for key, label, _ in STEPS:
            lab = ttk.Label(nav, text=label, style="Nav.TLabel", anchor="w")
            lab.pack(fill="x", pady=(0, 2))
            lab.bind("<Button-1>", lambda e, k=key: self._show(k))
            self.nav[key] = lab

        ttk.Separator(nav, orient="horizontal").pack(fill="x", pady=(10, 2))
        lab = ttk.Label(nav, text="Settings", style="Nav.TLabel", anchor="w")
        lab.pack(fill="x", pady=(0, 2))
        lab.bind("<Button-1>", lambda e: self._show("settings"))
        self.nav["settings"] = lab

        ttk.Separator(nav, orient="horizontal").pack(fill="x", pady=10)
        ttk.Button(nav, text="Open output folder",
                   command=lambda: self._reveal(self.out_dir.get())).pack(fill="x", pady=2)
        ttk.Button(nav, text="Open project folder",
                   command=lambda: self._reveal(HERE)).pack(fill="x", pady=2)

        # The step pane scrolls. Settings is taller than a 13" laptop screen
        # once the colour table is on it, and a control you cannot reach is
        # the same as a control that is not there.
        outer = ttk.Frame(body, style="Panel.TFrame")
        outer.pack(side="left", fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=PANEL, highlightthickness=0, bd=0)
        self.canvas = canvas
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        self.pane = ttk.Frame(canvas, style="Panel.TFrame", padding=18)
        window = canvas.create_window((0, 0), window=self.pane, anchor="nw")
        self.pane.bind("<Configure>",
                       lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(window, width=e.width))

        def wheel(e):
            step = -1 if getattr(e, "delta", 0) > 0 or e.num == 4 else 1
            canvas.yview_scroll(step, "units")
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            canvas.bind_all(seq, wheel)
            # Tk's dropdowns change their value when the wheel turns over them,
            # so scrolling the Settings page past the colour table silently
            # stepped colours to the next in the list (Black -> Red, Magenta ->
            # Cyan) and the next save wrote that. The wheel now only scrolls.
            self.root.unbind_class("TCombobox", seq)

        self.frames = {
            "settings": self._page_settings(),     # first: the others read its vars
            "term": self._page_term(),
            "select": self._page_select(),
            "fusion": self._page_fusion(),
            "build": self._page_build(),
        }

        logwrap = self.logwrap
        bar = ttk.Frame(logwrap)
        bar.pack(fill="x", pady=(0, 4))
        ttk.Label(bar, text="Log", foreground=MUTED).pack(side="left")
        ttk.Button(bar, text="Copy", command=self._copy_log).pack(side="right")
        ttk.Button(bar, text="Clear", command=lambda: self._logclear()).pack(side="right", padx=4)

        self.log = tk.Text(logwrap, height=11, bg=LOGBG, fg=LOGFG, bd=0,
                           insertbackground=LOGFG, wrap="word", padx=12, pady=10,
                           font=("Consolas", 9) if platform.system() == "Windows"
                           else ("Menlo", 11) if MAC else ("DejaVu Sans Mono", 9))
        sb = ttk.Scrollbar(logwrap, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set, state="disabled")
        sb.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        self.log.tag_configure("ok", foreground="#6fd39a")
        self.log.tag_configure("bad", foreground="#ff8b7d")
        self.log.tag_configure("note", foreground=AMBER)

        self._say("Landing Gear Pipeline. Pick a step on the left.\n", "note")

    # ---- shared widgets ----
    def _folder_row(self, parent, label, var, hint=""):
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", pady=(0, 10))
        ttk.Label(row, text=label, style="Panel.TLabel", width=18, anchor="w").pack(side="left")
        show = ttk.Label(row, text=shorten(var.get()), style="Path.TLabel", anchor="w")
        show.pack(side="left", fill="x", expand=True, padx=(0, 8))

        def pick():
            start = var.get() if os.path.isdir(var.get()) else HERE
            d = filedialog.askdirectory(initialdir=start, title=label)
            if d:
                var.set(d)
                show.configure(text=shorten(d))

        ttk.Button(row, text="Choose...", command=pick).pack(side="right")
        var.trace_add("write", lambda *_: show.configure(text=shorten(var.get())))
        if hint:
            ttk.Label(parent, text=hint, style="Sub.TLabel", wraplength=560,
                      justify="left").pack(fill="x", pady=(0, 12), anchor="w")
        return row


    def _next_bar(self, parent, target, text):
        """Every step ends by naming the next one. The pipeline has a Fusion
        step in the middle that no code can cross, so the flow is made explicit
        rather than pretending to be one button."""
        bar = ttk.Frame(parent, style="Panel.TFrame")
        bar.pack(fill="x", side="bottom", pady=(16, 0))
        ttk.Separator(bar, orient="horizontal").pack(fill="x", pady=(0, 10))
        ttk.Button(bar, text=text, command=lambda: self._show(target)).pack(side="right")
        return bar


    # ---- per-semester page ----
    def _page_term(self):
        """Everything that changes between one offering of the course and the
        next, in one place. A new TA should be able to start a term by editing
        this page and nothing else."""
        f = ttk.Frame(self.pane, style="Panel.TFrame")
        ttk.Label(f, text="Each semester", style="H2.TLabel").pack(anchor="w")
        ttk.Label(f, text="These three change from one term to the next. Everything else -- sheet "
                          "size, material, laser colours -- stays put until the shop changes "
                          "something.",
                  style="Sub.TLabel", wraplength=580, justify="left").pack(anchor="w", pady=(4, 16))

        top = ttk.Frame(f, style="Panel.TFrame")
        top.pack(fill="x", pady=(0, 16))
        ttk.Label(top, text="Students per group", style="Panel.TLabel").pack(side="left")
        self.expect_lo = tk.StringVar(value=str(self.shop.get("expect_lo", 3)))
        self.expect_hi = tk.StringVar(value=str(self.shop.get("expect_hi", 4)))
        ttk.Spinbox(top, from_=1, to=8, width=3, textvariable=self.expect_lo).pack(side="left", padx=(8, 4))
        ttk.Label(top, text="to", style="Panel.TLabel").pack(side="left")
        ttk.Spinbox(top, from_=1, to=8, width=3, textvariable=self.expect_hi).pack(side="left", padx=(4, 24))
        ttk.Label(top, text="Draw seed", style="Panel.TLabel").pack(side="left")
        self.seed = tk.StringVar(value=str(self.shop.get("seed", "2026")))
        ttk.Entry(top, width=10, textvariable=self.seed).pack(side="left", padx=8)
        ttk.Label(top, text="a new term wants a new seed", style="Sub.TLabel").pack(side="left")

        head = ttk.Frame(f, style="Panel.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text="Group roster", style="Panel.TLabel",
                  font=("Segoe UI" if platform.system() == "Windows" else "Helvetica", 10, "bold")
                  ).pack(side="left")
        self.roster_count = ttk.Label(head, text="", style="Sub.TLabel")
        self.roster_count.pack(side="right")
        ttk.Label(f, text="One group ID per line, exactly as it will appear in the file names and on "
                          "the steel. Lines starting with # are notes. Any folder whose ID is not on "
                          "this list is stopped before it reaches the laser.",
                  style="Sub.TLabel", wraplength=580, justify="left").pack(anchor="w", pady=(2, 8))

        box = ttk.Frame(f, style="Panel.TFrame")
        box.pack(fill="both", expand=True)
        self.roster_text = tk.Text(
            box, height=12, wrap="none", bd=1, relief="solid", padx=8, pady=6,
            font=("Consolas", 10) if platform.system() == "Windows"
            else ("Menlo", 11) if MAC else ("DejaVu Sans Mono", 9))
        rsb = ttk.Scrollbar(box, command=self.roster_text.yview)
        self.roster_text.configure(yscrollcommand=rsb.set)
        rsb.pack(side="right", fill="y")
        self.roster_text.pack(side="left", fill="both", expand=True)
        self.roster_text.insert("1.0", self._read_roster())
        self.roster_text.bind("<KeyRelease>", lambda e: self._count_roster())

        row = ttk.Frame(f, style="Panel.TFrame")
        row.pack(anchor="w", pady=(12, 0))
        ttk.Button(row, text="Save roster", style="Go.TButton",
                   command=self._save_roster).pack(side="left", padx=(0, 8))
        ttk.Button(row, text="Reload from file",
                   command=self._reload_roster).pack(side="left")

        self._count_roster()
        self._next_bar(f, "select", "Next: select designs  \u2192")
        return f

    def _read_roster(self):
        path = os.path.join(HERE, "roster.txt")
        try:
            with open(path) as fh:
                return fh.read()
        except Exception:
            return "# One group ID per line, e.g.\nAERO-L1-G01\nMAAE-L2-G07\n"

    def _count_roster(self):
        if not hasattr(self, "roster_count"):
            return
        n = len([l for l in self.roster_text.get("1.0", "end").splitlines()
                 if l.strip() and not l.strip().startswith("#")])
        self.roster_count.configure(text="%d group%s" % (n, "" if n == 1 else "s"))

    def _reload_roster(self):
        self.roster_text.delete("1.0", "end")
        self.roster_text.insert("1.0", self._read_roster())
        self._count_roster()

    def _save_roster(self):
        text = self.roster_text.get("1.0", "end").rstrip() + "\n"
        ids = [l.strip() for l in text.splitlines()
               if l.strip() and not l.strip().startswith("#")]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        try:
            with open(os.path.join(HERE, "roster.txt"), "w") as fh:
                fh.write(text)
        except Exception as e:
            messagebox.showerror("Could not save the roster", str(e))
            return
        self._count_roster()
        self._remember()
        self._save_shop_quiet()
        self._say("Roster saved \u2014 %d group%s.\n" % (len(ids), "" if len(ids) == 1 else "s"), "ok")
        if dupes:
            self._say("   listed twice: %s\n" % ", ".join(dupes), "bad")
        self.status.configure(text="Roster saved")

    # ---- settings page ----
    def _page_settings(self):
        f = ttk.Frame(self.pane, style="Panel.TFrame")
        ttk.Label(f, text="Settings", style="H2.TLabel").pack(anchor="w")
        ttk.Label(f, text="Everything here is saved to shop.json beside the app. That file is what "
                          "the pipeline reads, so next year's TA gets these same settings without "
                          "knowing they exist.",
                  style="Sub.TLabel", wraplength=580, justify="left").pack(anchor="w", pady=(4, 14))

        self.sheet = tk.StringVar(value=str(self.shop.get("sheet", "1219.2x1219.2")))
        self.kerf = tk.StringVar(value=str(self.shop.get("kerf", 0.0)))
        self.mark = tk.StringVar(value=str(self.shop.get("mark", "group-code")))
        self.nestmode = tk.StringVar(value=str(self.shop.get("nest_mode", "corner")))
        self.font = tk.StringVar(value=str(self.shop.get("font", "angular")))
        self.thick = tk.StringVar(value=str(self.shop.get("material_thickness", 1.52)))
        self.gap = tk.StringVar(value=str(self.shop.get("part_gap", 4.0)))
        self.margin = tk.StringVar(value=str(self.shop.get("sheet_margin", 8.0)))

        grid = ttk.Frame(f, style="Panel.TFrame")
        grid.pack(fill="x", pady=(0, 4))

        def cell(r, c, text):
            ttk.Label(grid, text=text, style="Panel.TLabel").grid(
                row=r, column=c, sticky="w", pady=4, padx=(0, 10))

        cell(0, 0, "Sheet  W x H mm")
        ttk.Entry(grid, textvariable=self.sheet, width=16).grid(row=0, column=1, sticky="w")
        cell(0, 2, "Material mm")
        ttk.Entry(grid, textvariable=self.thick, width=10).grid(row=0, column=3, sticky="w")
        cell(1, 0, "Kerf mm")
        ttk.Entry(grid, textvariable=self.kerf, width=16).grid(row=1, column=1, sticky="w")
        cell(1, 2, "Gap between parts mm")
        ttk.Entry(grid, textvariable=self.gap, width=10).grid(row=1, column=3, sticky="w")
        cell(2, 0, "Clear border mm")
        ttk.Entry(grid, textvariable=self.margin, width=16).grid(row=2, column=1, sticky="w")
        cell(3, 0, "Mark")
        ttk.Combobox(grid, textvariable=self.mark, width=14, state="readonly",
                     values=("group", "group-code", "group-part",
                             "short", "short-code")).grid(row=3, column=1, sticky="w")
        cell(3, 2, "Nesting")
        ttk.Combobox(grid, textvariable=self.nestmode, width=12, state="readonly",
                     values=("corner", "compact", "group", "per-group")).grid(row=3, column=3, sticky="w")
        cell(4, 0, "Letter shapes")
        ttk.Combobox(grid, textvariable=self.font, width=14, state="readonly",
                     values=("angular", "standard")).grid(row=4, column=1, sticky="w")
        ttk.Label(grid, text="angular draws with long straight strokes only",
                  style="Sub.TLabel").grid(row=4, column=2, columnspan=2, sticky="w")

        ttk.Label(f, text="Kerf 0 means the geometry stays nominal and the machine compensates. Set "
                          "a real kerf only if machine compensation is switched off, or parts finish "
                          "a full kerf out.",
                  style="Sub.TLabel", wraplength=580, justify="left").pack(anchor="w", pady=(6, 16))

        # ---- layer colours ----
        ttk.Label(f, text="Laser colours", style="Panel.TLabel",
                  font=("Segoe UI" if platform.system() == "Windows" else "Helvetica", 10, "bold")
                  ).pack(anchor="w")
        ttk.Label(f, text="Set these to match the colours enabled in the laser's own settings file. "
                          "Two rows may share a colour. Put the two reference rows on colours the "
                          "machine has switched OFF, so they cannot be cut by accident.",
                  style="Sub.TLabel", wraplength=580, justify="left").pack(anchor="w", pady=(2, 10))

        lay = ttk.Frame(f, style="Panel.TFrame")
        lay.pack(fill="x")
        stored = self.shop.get("layers", {}) or {}
        self.layer_colour = {}
        self.layer_swatch = {}
        self.layer_hex = {}
        self.layer_style = {}
        self.layer_stroke = {}
        self.stroke_entry = {}
        self._stroke_memory = {}
        for col, head in ((2, "Colour"), (3, "Exact value"), (4, "Lines"), (5, "Thickness")):
            ttk.Label(lay, text=head, style="Sub.TLabel").grid(row=0, column=col, sticky="w")
        # A profile saved by the version with one global "Letters" setting:
        # show it on the Group ID row, which is where it lives now.
        legacy = str(self.shop.get("letters", "")).strip().lower()
        for i, (role, label, what) in enumerate(ROLES, start=1):
            over = stored.get(role, {}) or {}
            name = colour_name_for(over.get("aci"), over.get("rgb")) if over.get("aci") is not None \
                else ROLE_DEFAULT_COLOUR[role]
            var = tk.StringVar(value=name)
            self.layer_colour[role] = var
            rgb = over.get("rgb")
            hexv = ("#%02x%02x%02x" % tuple(int(c) for c in rgb[:3])) if rgb and len(rgb) >= 3 \
                else COLOUR_SWATCH.get(name, "#000000")
            hvar = tk.StringVar(value=hexv)
            self.layer_hex[role] = hvar

            ttk.Label(lay, text=label, style="Panel.TLabel", width=14, anchor="w").grid(
                row=i, column=0, sticky="w", pady=3)
            sw = tk.Label(lay, text="  ", bg=hexv, relief="solid", bd=1, width=3)
            sw.grid(row=i, column=1, padx=(0, 8))
            self.layer_swatch[role] = sw
            box = ttk.Combobox(lay, textvariable=var, width=9, state="readonly",
                               values=[n for n, _a, _r in MACHINE_COLOURS])
            box.grid(row=i, column=2, sticky="w", padx=(0, 8))
            hbox = ttk.Entry(lay, textvariable=hvar, width=8)
            hbox.grid(row=i, column=3, sticky="w", padx=(0, 10))
            box.bind("<<ComboboxSelected>>",
                     lambda e, r=role: self._colour_picked(r))
            hvar.trace_add("write", lambda *_a, r=role: self._sync_swatch(r))

            # Lines: hairline (followed) or filled (engraved). Only the rows in
            # FILLABLE may be filled; the rest are shown locked to hairline.
            style = str(over.get("style") or (legacy if role == "MARK_TEXT" else "")
                        ).strip().lower()
            if role not in FILLABLE or style != "filled":
                style = "hairline"
            svar = tk.StringVar(value=style)
            self.layer_style[role] = svar
            sbox = ttk.Combobox(lay, textvariable=svar, width=8,
                                state="readonly" if role in FILLABLE else "disabled",
                                values=("hairline", "filled"))
            sbox.grid(row=i, column=4, sticky="w", padx=(0, 8))
            unit = {"MARK_TEXT": "% of height", "MARK_BEND": "mm"}.get(role, "")
            if role in FILLABLE:
                val = over.get("stroke")
                if val is None and role == "MARK_TEXT" and legacy:
                    val = self.shop.get("stroke_pct")
                self._stroke_memory[role] = "%g" % float(val if val else FILLABLE[role][1])
            tvar = tk.StringVar(value="")
            self.layer_stroke[role] = tvar
            tbox = ttk.Entry(lay, textvariable=tvar, width=5)
            tbox.grid(row=i, column=5, sticky="w", padx=(0, 4))
            self.stroke_entry[role] = tbox
            ttk.Label(lay, text=unit, style="Sub.TLabel").grid(row=i, column=6, sticky="w",
                                                              padx=(0, 12))
            sbox.bind("<<ComboboxSelected>>", lambda e, r=role: self._style_picked(r))
            self._style_picked(role)
            ttk.Label(lay, text=what, style="Sub.TLabel").grid(row=i, column=7, sticky="w")

        ttk.Label(f, text="The box after each colour is the exact value written into the "
                          "file. Changing the colour fills it in; type over it only if this "
                          "machine keys on a different value — the number the operator "
                          "reads off a line that already works.",
                  style="Sub.TLabel", wraplength=580, justify="left").pack(anchor="w",
                                                                          pady=(10, 0))
        ttk.Label(f, text="Lines: hairline = a single line the laser follows. filled = a solid "
                          "shape with no outline, which the laser ENGRAVES instead (sweeps "
                          "across it). Only the group ID and the bend lines can be filled; "
                          "cuts and reference lines are always hairlines. Thickness is only "
                          "asked for when a row is filled: for the group ID it is a percent of "
                          "the letter height (13 is a good start), for bend lines a width in mm.",
                  style="Sub.TLabel", wraplength=580, justify="left").pack(anchor="w",
                                                                          pady=(8, 0))
        self.colours_ok = tk.BooleanVar(value=bool(self.shop.get("colours_confirmed", False)))
        ttk.Checkbutton(f, text="These colours are checked on the machine with the test card",
                        variable=self.colours_ok).pack(anchor="w", pady=(10, 0))
        ttk.Label(f, text="Until this is ticked, every build warns that the colours have not "
                          "been checked. Untick it if the laser's settings change.",
                  style="Sub.TLabel", wraplength=580, justify="left").pack(anchor="w")

        row = ttk.Frame(f, style="Panel.TFrame")
        row.pack(anchor="w", pady=(18, 0))
        ttk.Button(row, text="Save to shop.json", style="Go.TButton",
                   command=self._save_shop).pack(side="left", padx=(0, 8))
        ttk.Button(row, text="Open shop.json", command=self._open_shop).pack(side="left", padx=(0, 8))
        ttk.Button(row, text="Show in folder", command=lambda: self._reveal(HERE)).pack(side="left")

        ttk.Separator(f, orient="horizontal").pack(fill="x", pady=(18, 12))
        ttk.Label(f, text="Don't know what the colours do on that machine yet? Cut this on a "
                          "piece of scrap. One labelled tile per colour \u2014 whichever tiles "
                          "fall out are cuts, whichever are marked but attached are marks, and "
                          "whichever come back blank are switched off.",
                  style="Sub.TLabel", wraplength=580, justify="left").pack(anchor="w", pady=(0, 8))
        cards = ttk.Frame(f, style="Panel.TFrame")
        cards.pack(anchor="w")
        ttk.Button(cards, text="Make a colour test card",
                   command=self._test_card).pack(side="left", padx=(0, 8))
        ttk.Button(cards, text="Make a marking card",
                   command=self._mark_card).pack(side="left")

        ttk.Label(f, text="The marking card answers the other question: how to mark the group ID "
                          "so it reads cleanly. Left column: hairline letters (line mark). Right "
                          "column: the same letters filled, for engraving. Each in two letter "
                          "shapes at six sizes, plus a row of filled thicknesses at 2.5 mm. Cut "
                          "it, find the best line you can read without squinting, then set the "
                          "Group ID row's Lines and Thickness, and Letter shapes, to match.",
                  style="Sub.TLabel", wraplength=580, justify="left").pack(anchor="w", pady=(10, 0))
        return f

    def _mark_card(self):
        path = os.path.join(HERE, "marking_card.dxf")
        missing = self.check_libs(announce=False)
        if missing:
            messagebox.showerror("Not ready yet",
                                 "The card needs these and they are not installed "
                                 "for this Python:\n\n    %s\n\nPress 'Install them "
                                 "now' at the top of the window." % ", ".join(missing))
            return
        if not self._save_shop():
            return      # a setting was rejected; don't run on the old ones
        try:
            from lgpipe import markcard, layers as L
            L.apply_overrides((self.shop or {}).get("layers"))
            markcard.build(path)
        except Exception as e:
            messagebox.showerror("Could not build the marking card", str(e))
            return
        self._say("\nMarking card written to %s\n" % path, "ok")
        self._say(markcard.HOW_TO_READ + "\n")

    def _test_card(self):
        path = os.path.join(HERE, "test_card.dxf")
        try:
            from lgpipe import testcard
            testcard.build(path)
        except Exception as e:
            messagebox.showerror("Could not build the test card", str(e))
            return
        self._say("\nTest card written to %s\n" % path, "ok")
        self._say(testcard.HOW_TO_READ + "\n")
        self.status.configure(text="Test card ready")

    # ---- page 1 ----
    def _page_select(self):
        f = ttk.Frame(self.pane, style="Panel.TFrame")
        ttk.Label(f, text="Select one design per group", style="H2.TLabel").pack(anchor="w")
        ttk.Label(f, text="The brief says one design per group is selected at random and that design "
                          "is the one marked and manufactured. This draws it reproducibly and writes "
                          "down every candidate it considered.",
                  style="Sub.TLabel", wraplength=580, justify="left").pack(anchor="w", pady=(4, 14))

        self.sub_dir = tk.StringVar(value=unpack_path(self.prefs.get("submissions"), "submissions"))
        self.sel_dir = tk.StringVar(value=unpack_path(self.prefs.get("selected"), "selected"))
        self._folder_row(f, "Submissions", self.sub_dir,
                         "Everything downloaded from Brightspace. Subfolders are fine, and the "
                         "drawing PDFs are ignored — only .f3d / .f3z files count.")
        self._folder_row(f, "Selected designs", self.sel_dir,
                         "The winners land here, renamed to the bare group ID. Upload this whole "
                         "folder to Fusion.")

        bring = ttk.Frame(f, style="Panel.TFrame")
        bring.pack(fill="x", pady=(0, 6))
        ttk.Button(bring, text="Import Brightspace ZIP...",
                   command=self.import_zip).pack(side="left", padx=(0, 8))
        ttk.Button(bring, text="Add .f3d / .f3z files...",
                   command=self.import_files).pack(side="left", padx=(0, 12))
        self.sub_count = ttk.Label(bring, text="", style="Sub.TLabel")
        self.sub_count.pack(side="left")
        self.sub_dir.trace_add("write", lambda *_: self._refresh_count())

        self.draw_summary = ttk.Label(f, text="", style="Sub.TLabel")
        self.draw_summary.pack(anchor="w", pady=(12, 12))

        self.btn_select = ttk.Button(f, text="Draw the designs", style="Go.TButton",
                                     command=self.run_select)
        self.btn_select.pack(anchor="w")
        self._next_bar(f, "fusion", "Next: export in Fusion  \u2192")
        return f

    # ---- page 2 ----
    def _page_fusion(self):
        f = ttk.Frame(self.pane, style="Panel.TFrame")
        ttk.Label(f, text="Export the flat patterns in Fusion", style="H2.TLabel").pack(anchor="w")
        ttk.Label(f, text="This step cannot run from here. Fusion scripts only run inside Fusion, "
                          "so this is the one manual step — about five minutes of your time, "
                          "then it runs unattended.",
                  style="Sub.TLabel", wraplength=580, justify="left").pack(anchor="w", pady=(4, 14))

        steps = [
            ("Install once", "Fusion → Utilities → ADD-INS → Scripts and Add-Ins → "
                             "Scripts → + → Create, named BatchExportProject. Replace the "
                             "generated .py with the one in this folder's fusion directory."),
            ("Upload", "Make a Fusion project for the term, upload everything from the selected "
                       "folder, and make that project active in the data panel."),
            ("Run", "Scripts and Add-Ins → BatchExportProject → Run. Choose the raw folder "
                    "below as the output."),
            ("Check", "Open _export_log.csv in the raw folder. Rows marked MISSING or NAME are "
                      "groups to chase. SKIP rows for gears, knobs and fasteners are expected."),
        ]
        for title, text in steps:
            row = ttk.Frame(f, style="Panel.TFrame")
            row.pack(fill="x", pady=(0, 9), anchor="w")
            ttk.Label(row, text=title, style="Panel.TLabel", width=12, anchor="nw",
                      font=("Segoe UI" if platform.system() == "Windows" else "Helvetica", 10, "bold")
                      ).pack(side="left", anchor="n")
            ttk.Label(row, text=text, style="Sub.TLabel", wraplength=470,
                      justify="left").pack(side="left", anchor="w")

        btns = ttk.Frame(f, style="Panel.TFrame")
        btns.pack(anchor="w", pady=(10, 0))
        ttk.Button(btns, text="Open selected folder",
                   command=lambda: self._reveal(self.sel_dir.get())).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="Open Fusion scripts folder",
                   command=lambda: self._reveal(os.path.join(HERE, "fusion"))).pack(side="left")
        self._next_bar(f, "build", "Next: build the sheet  \u2192")
        return f

    # ---- page 3 ----
    def _page_build(self):
        f = ttk.Frame(self.pane, style="Panel.TFrame")
        ttk.Label(f, text="Build the production sheet", style="H2.TLabel").pack(anchor="w")
        ttk.Label(f, text="Classifies every blank's geometry, marks it with the group ID, nests the "
                          "cohort onto stock and validates the lot. Blanks are cut exactly as "
                          "submitted — nothing is corrected.",
                  style="Sub.TLabel", wraplength=580, justify="left").pack(anchor="w", pady=(4, 14))

        self.raw_dir = tk.StringVar(value=unpack_path(self.prefs.get("raw"), "raw"))
        self.out_dir = tk.StringVar(value=unpack_path(self.prefs.get("out"), "out"))
        self._folder_row(f, "Flat patterns", self.raw_dir)
        self._folder_row(f, "Output", self.out_dir)

        self.build_summary = ttk.Label(f, text="", style="Sub.TLabel",
                                       wraplength=580, justify="left")
        self.build_summary.pack(anchor="w", pady=(2, 4))
        ttk.Button(f, text="Change settings",
                   command=lambda: self._show("settings")).pack(anchor="w", pady=(0, 16))

        row = ttk.Frame(f, style="Panel.TFrame")
        row.pack(anchor="w")
        self.btn_build = ttk.Button(row, text="Build the sheet", style="Go.TButton",
                                    command=self.run_build)
        self.btn_build.pack(side="left", padx=(0, 8))
        ttk.Button(row, text="Open report", command=self._open_report).pack(side="left")
        self._next_bar(f, "settings", "Settings  \u2192")
        return f

    # ---- the three libraries ----
    def missing_libs(self):
        """
        Ask a FRESH interpreter, not this one. Two reasons: a library installed
        a moment ago is not importable inside a process that already started,
        and this is the same interpreter, started the same way, that will run
        the pipeline -- so its answer is the one that matters.
        """
        out = []
        for mod in ("ezdxf", "shapely", "numpy"):
            try:
                r = subprocess.run(
                    [sys.executable, "-c", "import %s" % mod],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=60,
                    creationflags=(subprocess.CREATE_NO_WINDOW
                                   if platform.system() == "Windows" else 0),
                )
                if r.returncode != 0:
                    out.append(mod)
            except Exception:
                out.append(mod)
        return out

    def check_libs_async(self, announce=True):
        """
        Startup check, off the main thread, so the window opens instantly even
        on a slow machine where three interpreter launches take a moment.
        """
        def worker():
            missing = self.missing_libs()
            self.root.after(0, lambda: self._apply_libs(missing, announce))
        threading.Thread(target=worker, daemon=True).start()

    def check_libs(self, announce=True):
        """
        Show or hide the fix-it strip. Called before a build and after an
        install, where the answer is needed immediately.

        The check runs against the interpreter that will run the pipeline --
        app.py launches it with sys.executable. A machine with two Pythons can
        have the libraries in the other one and still fail here, which is
        exactly the confusing case this makes obvious.
        """
        return self._apply_libs(self.missing_libs(), announce)

    def _apply_libs(self, missing, announce=True):
        if missing:
            self.fix_msg.configure(
                text="Not ready yet: %s %s not installed for this Python "
                     "(%s). Everything else works; the build step needs them."
                     % (", ".join(missing),
                        "is" if len(missing) == 1 else "are",
                        os.path.basename(sys.executable)))
            self.fixbar.pack(after=self.headrow, fill="x")
            self.fix_btn.configure(state="normal")
            if announce:
                self._say("Missing: %s. Press 'Install them now'.\n"
                          % ", ".join(missing), "bad")
        else:
            self.fixbar.pack_forget()
        return missing

    def install_libs(self):
        """
        Two attempts. The plain one works on a normal Windows, macOS or Anaconda
        Python. Some Linux and Homebrew Pythons refuse it -- they are "externally
        managed" and want the packages kept out of the system folder -- so the
        second attempt installs into this user's own folder instead.
        """
        attempt = getattr(self, "_lib_attempt", 0)
        args = [sys.executable, "-m", "pip", "install", "ezdxf", "shapely", "numpy"]
        if attempt:
            args += ["--user", "--break-system-packages"]
        self.fix_btn.configure(state="disabled")
        self._spawn(args,
                    "Installing the three libraries"
                    + (" (second attempt, into your own folder)" if attempt else ""),
                    lambda code: self._after_install(code))

    def _after_install(self, code):
        still = self.check_libs(announce=False)
        if not still:
            self._lib_attempt = 0
            self._say("Installed. The build step will work now.\n", "ok")
            return
        if getattr(self, "_lib_attempt", 0) == 0:
            self._lib_attempt = 1
            self._say("That Python will not install into its system folder. "
                      "Trying again into your own folder.\n", "note")
            self.root.after(300, self.install_libs)
            return
        self._say("Still missing: %s.\n" % ", ".join(still), "bad")
        self._say("Close the window and run the launcher instead "
                  "(Landing Gear Pipeline.bat), or install from an "
                  "Anaconda Prompt with:  pip install ezdxf shapely numpy\n",
                  "bad")
        self.fix_btn.configure(state="normal")

    # ---- bringing files in ----
    def _refresh_count(self):
        if not hasattr(self, "sub_count"):
            return
        d = self.sub_dir.get()
        if not os.path.isdir(d):
            self.sub_count.configure(text="folder does not exist yet")
            return
        n = count_f3d(d)
        self.sub_count.configure(
            text="%d Fusion file%s in the submissions folder" % (n, "" if n == 1 else "s"))

    def import_zip(self):
        """Take the Brightspace download as it arrives and unpack it in place.

        Brightspace hands you one ZIP whose entries carry its own id, the
        student's name and a timestamp wrapped around the filename the student
        typed. Nothing here renames or reorganises any of that -- the selector
        reads the group ID wherever it ends up. This only saves you extracting
        into the right folder by hand.
        """
        z = filedialog.askopenfilename(
            title="Brightspace download", initialdir=os.path.expanduser("~"),
            filetypes=[("ZIP archives", "*.zip"), ("All files", "*.*")])
        if not z:
            return
        dest = self.sub_dir.get()
        try:
            os.makedirs(dest, exist_ok=True)
            added = skipped = 0
            with zipfile.ZipFile(z) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    base = os.path.basename(info.filename)
                    if not base or base.lower() == "index.html":
                        skipped += 1
                        continue
                    target = os.path.join(dest, info.filename.replace("\\", "/"))
                    os.makedirs(os.path.dirname(target) or dest, exist_ok=True)
                    with zf.open(info) as src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out)
                    added += 1
        except zipfile.BadZipFile:
            messagebox.showerror("Not a ZIP", "That file is not a ZIP archive.")
            return
        except Exception as e:
            messagebox.showerror("Could not unpack", str(e))
            return
        self._refresh_count()
        self._say("Unpacked %d file%s into the submissions folder (%d ignored).\n"
                  % (added, "" if added == 1 else "s", skipped), "ok")
        self._say("%d of them are Fusion files.\n" % count_f3d(dest))

    def import_files(self):
        paths = filedialog.askopenfilenames(
            title="Fusion submissions", initialdir=os.path.expanduser("~"),
            filetypes=[("Fusion files", "*.f3d *.f3z"), ("All files", "*.*")])
        if not paths:
            return
        dest = self.sub_dir.get()
        os.makedirs(dest, exist_ok=True)
        n = 0
        for src in paths:
            try:
                shutil.copy2(src, os.path.join(dest, os.path.basename(src)))
                n += 1
            except Exception as e:
                self._say("Could not copy %s: %s\n" % (os.path.basename(src), e), "bad")
        self._refresh_count()
        self._say("Copied %d file%s into the submissions folder.\n"
                  % (n, "" if n == 1 else "s"), "ok")

    def _refresh_summary(self):
        if not hasattr(self, "build_summary"):
            return
        self.build_summary.configure(
            text="Sheet %s mm  \u00b7  material %s mm  \u00b7  kerf %s mm  \u00b7  gap %s mm  "
                 "\u00b7  border %s mm  \u00b7  mark %s  \u00b7  %s  \u00b7  nesting %s"
                 % (self.sheet.get(), self.thick.get(), self.kerf.get(),
                    self.gap.get(), self.margin.get(), self.mark.get(),
                    "group ID %s" % (("filled %s%%" % self.layer_stroke["MARK_TEXT"].get())
                                     if self.layer_style["MARK_TEXT"].get() == "filled"
                                     else "hairline"),
                    self.nestmode.get()))

    def _open_shop(self):
        if not os.path.exists(SHOP):
            self._save_shop()
        try:
            if platform.system() == "Windows":
                os.startfile(SHOP)                  # noqa: S606
            elif MAC:
                subprocess.Popen(["open", "-t", SHOP])
            else:
                subprocess.Popen(["xdg-open", SHOP])
        except Exception:
            messagebox.showinfo("shop.json", SHOP)

    # ---- navigation ----
    def _show(self, key):
        for k, lab in self.nav.items():
            lab.configure(style="NavOn.TLabel" if k == key else "Nav.TLabel")
        for k, fr in getattr(self, "frames", {}).items():
            fr.pack_forget()
        if hasattr(self, "frames"):
            self.frames[key].pack(fill="both", expand=True)
        # Every page opens at its top. Without this a page shorter than the
        # last one opens scrolled past its own end and looks blank.
        if hasattr(self, "canvas"):
            self.canvas.update_idletasks()
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
            self.canvas.yview_moveto(0)
        self.current = key
        if key == "select":
            self._refresh_count()
            if hasattr(self, "draw_summary"):
                self.draw_summary.configure(
                    text="Groups of %s-%s  \u00b7  seed %s  \u00b7  %s on the roster    "
                         "(change these under Each semester)"
                         % (self.expect_lo.get(), self.expect_hi.get(), self.seed.get(),
                            self.roster_count.cget("text")))
        elif key == "term":
            self._count_roster()
        elif key == "build":
            self._refresh_summary()

    # ---- logging ----
    def _say(self, text, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", text, tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def _logclear(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _copy_log(self):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.log.get("1.0", "end"))
            self.status.configure(text="Log copied")
        except Exception:
            pass

    def _reveal(self, path):
        if not reveal(path):
            messagebox.showinfo("Not there yet",
                                "That folder does not exist yet:\n\n%s" % (path or "(not set)"))

    def _open_report(self):
        p = os.path.join(self.out_dir.get(), "report.md")
        if not os.path.exists(p):
            messagebox.showinfo("No report yet", "Build the sheet first — the report is written "
                                                 "at the end of a run.")
            return
        try:
            if platform.system() == "Windows":
                os.startfile(p)                     # noqa: S606
            elif MAC:
                subprocess.Popen(["open", p])
            else:
                subprocess.Popen(["xdg-open", p])
        except Exception:
            messagebox.showinfo("Report", p)

    # ---- settings ----
    @staticmethod
    def parse_hex(text, fallback=(0, 0, 0)):
        """'#1a1a1a' or '1a1a1a' or '26,26,26' -> (r, g, b). Junk -> fallback."""
        s = (text or "").strip().lstrip("#")
        if "," in s:
            try:
                parts = [int(p) for p in s.split(",")]
                if len(parts) == 3 and all(0 <= p <= 255 for p in parts):
                    return tuple(parts)
            except ValueError:
                pass
            return fallback
        if len(s) == 3:
            s = "".join(c * 2 for c in s)
        if len(s) == 6:
            try:
                return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
            except ValueError:
                pass
        return fallback

    def _colour_picked(self, role):
        """Dropdown changed: refill the exact value with that colour's own."""
        name = self.layer_colour[role].get()
        self.layer_hex[role].set(COLOUR_SWATCH.get(name, "#000000"))

    def _sync_swatch(self, role):
        rgb = self.parse_hex(self.layer_hex[role].get(), None)
        if rgb is not None:
            try:
                self.layer_swatch[role].configure(bg="#%02x%02x%02x" % rgb)
            except Exception:
                pass

    def _save_shop(self):
        shop = dict(self.shop)
        shop["sheet"] = self.sheet.get().strip()
        shop["mark"] = self.mark.get()
        shop["nest_mode"] = self.nestmode.get()
        shop["font"] = self.font.get()
        # The global Letters setting moved onto the colour rows.
        shop.pop("letters", None)
        shop.pop("stroke_pct", None)
        for key, var, cast in (("kerf", self.kerf, float),
                               ("material_thickness", self.thick, float),
                               ("part_gap", self.gap, float),
                               ("sheet_margin", self.margin, float)):
            try:
                shop[key] = cast(var.get())
            except ValueError:
                messagebox.showerror("Check that number",
                                     "%s should be a number, not %r." % (key, var.get()))
                return False
        layers = {}
        for role, label, _what in ROLES:
            name = self.layer_colour[role].get()
            aci, rgb = COLOUR_BY_NAME.get(name, COLOUR_BY_NAME["Red"])
            rgb = self.parse_hex(self.layer_hex[role].get(), rgb)
            entry = {"name": role, "aci": aci, "rgb": list(rgb), "style": "hairline"}
            if role in FILLABLE and self.layer_style[role].get() == "filled":
                unit, _default, lo, hi = FILLABLE[role]
                raw = self.layer_stroke[role].get().strip()
                try:
                    val = float(raw)
                except ValueError:
                    messagebox.showerror("Check that number",
                                         "%s thickness should be a number (%s), not %r."
                                         % (label, unit, raw))
                    return False
                if not lo <= val <= hi:
                    messagebox.showerror("Check that number",
                                         "%s thickness should be between %g and %g %s. "
                                         "%g is outside that." % (label, lo, hi, unit, val))
                    return False
                entry["style"] = "filled"
                entry["stroke"] = val
            elif role in FILLABLE:
                # Remember the number for next time the row is set to filled.
                try:
                    entry["stroke"] = float(self._stroke_memory.get(role, FILLABLE[role][1]))
                except ValueError:
                    pass
            layers[role] = entry
        shop["layers"] = layers
        shop["colours_confirmed"] = bool(self.colours_ok.get())
        shop["seed"] = self.seed.get().strip() or "2026"
        shop["expect_lo"] = self.expect_lo.get().strip() or "3"
        shop["expect_hi"] = self.expect_hi.get().strip() or "4"

        save_json(SHOP, shop)
        self.shop = shop
        self._remember()
        self._refresh_summary()
        self.status.configure(text="Settings saved to shop.json")
        self._say("Settings saved to shop.json\n", "ok")
        self._colour_warnings()
        return True

    def _style_picked(self, role):
        """Hairline rows have no thickness: grey the box out and empty it."""
        box = self.stroke_entry[role]
        var = self.layer_stroke[role]
        if role in FILLABLE and self.layer_style[role].get() == "filled":
            box.configure(state="normal")
            if not var.get().strip():
                var.set(self._stroke_memory.get(role, "%g" % FILLABLE[role][1]))
        else:
            if role in FILLABLE and var.get().strip():
                self._stroke_memory[role] = var.get().strip()
            var.set("")
            box.configure(state="disabled")

    # Which roles the machine must run DIFFERENTLY. Two roles on one colour is
    # one machine setting, so a cut sharing with a mark or with a reference is
    # not a style choice -- it is the sheet frame getting cut, or the bend lines
    # getting cut, depending on which way the shop sets that colour.
    ROLE_PROCESS = {"CUT_OUTER": "cut", "CUT_INNER": "cut",
                    "MARK_TEXT": "mark", "MARK_BEND": "mark",
                    "REF_EXTENT": "reference", "REF_FRAME": "reference"}

    def _colour_warnings(self):
        # Black is the one colour a DXF cannot state unambiguously. Its RGB
        # value is zero, and a reader that treats "zero" as "nothing was set"
        # falls back to its own idea of black -- CorelDRAW's is 26,26,26, not
        # 0,0,0, and a laser profile matching on exact colour does not
        # recognise it. Every other colour has a non-zero value and survives.
        pure_black = [lbl for role, lbl, _ in ROLES
                      if self.parse_hex(self.layer_hex[role].get()) == (0, 0, 0)]
        if pure_black:
            self._say("   %s set to pure black (0,0,0). That is the one value a "
                      "DXF cannot state unambiguously — a reader that checks "
                      "'was a colour set?' sees zero, reads it as 'no', and "
                      "substitutes its own black. Use #1a1a1a, which is what the "
                      "files that already work at the shop arrive as.\n"
                      % " and ".join(pure_black), "bad")
        clash = {}
        for role, label, _ in ROLES:
            clash.setdefault(self.layer_colour[role].get(), []).append((role, label))
        for colour, who in sorted(clash.items()):
            if len(who) < 2:
                continue
            labels = " and ".join(lbl for _r, lbl in who)
            procs = sorted({self.ROLE_PROCESS.get(r, "?") for r, _l in who})
            if len(procs) == 1:
                self._say("   %s carries %s \u2014 the machine runs them "
                          "identically.\n" % (colour, labels), "note")
            else:
                self._say("   PROBLEM: %s carries %s, and those must run "
                          "DIFFERENTLY (%s). One colour is one machine "
                          "setting \u2014 give them separate colours.\n"
                          % (colour, labels, " vs ".join(procs)), "bad")

    def _save_shop_quiet(self):
        """Persist the semester settings without the chatter _save_shop makes."""
        try:
            shop = dict(self.shop)
            shop["seed"] = self.seed.get().strip() or "2026"
            shop["expect_lo"] = self.expect_lo.get().strip() or "3"
            shop["expect_hi"] = self.expect_hi.get().strip() or "4"
            save_json(SHOP, shop)
            self.shop = shop
        except Exception:
            pass

    def _remember(self):
        # .app_prefs.json holds WHERE things are on this machine. What the
        # course decided -- seed, group size, colours, sheet -- lives in
        # shop.json, which travels with the project.
        save_json(PREFS, {
            "_comment": "Folder locations for this machine only. Safe to delete; "
                        "the app rebuilds it. Course settings live in shop.json.",
            "submissions": pack_path(self.sub_dir.get()),
            "selected": pack_path(self.sel_dir.get()),
            "raw": pack_path(self.raw_dir.get()),
            "out": pack_path(self.out_dir.get()),
        })

    # ---- running ----
    def _busy(self, on, what=""):
        state = "disabled" if on else "normal"
        self.btn_select.configure(state=state)
        self.btn_build.configure(state=state)
        self.status.configure(text=(what or "Working...") if on else "Ready")

    def _spawn(self, args, label, done=None):
        if self.proc is not None:
            return
        self._remember()
        self._say("\n%s\n%s\n" % (label, "-" * len(label)), "note")
        self._busy(True, label)

        def worker():
            try:
                # UTF-8 both ways: a student name like "Nguyễn" printed by the
                # child would otherwise crash it on a Windows code page.
                env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
                p = subprocess.Popen(
                    args, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, encoding="utf-8", errors="replace", env=env,
                    creationflags=(subprocess.CREATE_NO_WINDOW
                                   if platform.system() == "Windows" else 0),
                )
                self.proc = p
                for line in p.stdout:
                    self.q.put(("line", line))
                p.wait()
                self.q.put(("done", (p.returncode, label, done)))
            except FileNotFoundError:
                self.q.put(("line", "Could not start Python. Is it installed?\n"))
                self.q.put(("done", (2, label, None)))
            except Exception as e:
                self.q.put(("line", "%s\n" % e))
                self.q.put(("done", (2, label, None)))

        threading.Thread(target=worker, daemon=True).start()

    def _pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "line":
                    low = payload.lower()
                    tag = "bad" if ("!" in payload or "error" in low or "problem" in low) else (
                        "ok" if payload.strip().startswith("OK") or " ok " in low else None)
                    self._say(payload, tag)
                else:
                    code, label, done = payload
                    self.proc = None
                    self._busy(False)
                    if code == 0:
                        self._say("%s finished cleanly.\n" % label, "ok")
                    elif code == 3:
                        self._say("%s finished, but it is NOT ready to cut: some groups were "
                                  "left off the sheet or need renaming. See the lines marked "
                                  "!!! above and report.md.\n" % label, "bad")
                    else:
                        self._say("%s finished with problems — read the lines above.\n" % label,
                                  "bad")
                    if done:
                        done(code)
        except queue.Empty:
            pass
        self.root.after(90, self._pump)

    def run_select(self):
        sub = self.sub_dir.get()
        if not os.path.isdir(sub):
            messagebox.showerror("No submissions folder",
                                 "There is no folder at:\n\n%s\n\nPut the student .f3d files "
                                 "there first." % sub)
            return
        args = [sys.executable, "select_designs.py",
                "--submissions", sub,
                "--out", self.sel_dir.get(),
                "--drawings", os.path.join(HERE, "drawings"),
                "--seed", self.seed.get().strip() or "2026",
                "--expect", "%s-%s" % (self.expect_lo.get().strip() or "3",
                                       self.expect_hi.get().strip() or "4")]
        roster = os.path.join(HERE, "roster.txt")
        if os.path.exists(roster):
            args += ["--roster", roster]

        def after(code):
            n = len([f for f in os.listdir(self.sel_dir.get())
                     if f.lower().endswith((".f3d", ".f3z"))]) if os.path.isdir(self.sel_dir.get()) else 0
            if n:
                self._say("\n%d design%s ready to upload to Fusion.\n"
                          % (n, "" if n == 1 else "s"), "note")
            if code == 0:
                self._show("fusion")

        self._spawn(args, "Drawing one design per group", after)

    def run_build(self):
        missing = self.check_libs(announce=False)
        if missing:
            messagebox.showerror(
                "Not ready yet",
                "The build needs these and they are not installed for this "
                "Python:\n\n    %s\n\nPress 'Install them now' at the top "
                "of the window." % ", ".join(missing))
            return
        raw = self.raw_dir.get()
        if not os.path.isdir(raw):
            messagebox.showerror("No flat patterns",
                                 "There is no folder at:\n\n%s\n\nRun the Fusion export first "
                                 "(step 2)." % raw)
            return
        if not self._save_shop():
            return      # a setting was rejected; don't run on the old ones
        args = [sys.executable, "-m", "lgpipe.cli",
                "--raw", raw, "--out", self.out_dir.get(), "--config", SHOP]
        roster = os.path.join(HERE, "roster.txt")
        if os.path.exists(roster):
            args += ["--roster", roster]

        def after(code):
            sheets = os.path.join(self.out_dir.get(), "sheets")
            if os.path.isdir(sheets):
                names = sorted(n for n in os.listdir(sheets) if n.endswith(".dxf"))
                if names and code == 3:
                    self._say("\nSheets built WITHOUT the groups above. Fix those and build "
                              "again before cutting:\n", "bad")
                elif names:
                    self._say("\nTake these to the laser:\n", "note")
                if names:
                    for n in names:
                        self._say("    %s\n" % os.path.join(sheets, n))
                    self._say("Read out/report.md before cutting.\n", "note")

        self._spawn(args, "Building the production sheet", after)


def main():
    root = tk.Tk()
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (app._remember(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
