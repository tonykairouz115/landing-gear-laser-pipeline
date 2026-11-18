# Setup — one time, about five minutes

You need two programs. You already have one of them.

| | What for | Where |
|---|---|---|
| **Fusion 360** | exporting flat patterns from student files | already installed |
| **Python 3.10+** | the pipeline | python.org — free |

Nothing else. No licences, no accounts, and the pipeline runs offline once
the libraries are installed.

---

## 1. Install Python

**Windows** — download from <https://www.python.org/downloads/> and run the
installer. **On the first screen, tick "Add python.exe to PATH".** It is easy
to miss and it is the one thing that causes trouble later. If you forget it,
re-run the installer and choose Modify.

**macOS** — download from the same place and install. Use the python.org build
rather than the one macOS ships with: the system Python often has no tkinter,
and without tkinter the window cannot open.

Already have Anaconda? That works. Run the launcher from an **Anaconda Prompt**
rather than double-clicking it, so the right Python is on PATH.

## 2. Put this folder somewhere sensible

Unzip so you end up with something like:

```
C:\MAAE2001\lgpipe\
      Landing Gear Pipeline.bat       <- Windows: double-click this
      Landing Gear Pipeline.command   <- macOS: double-click this
      1_select_designs.bat            (scripted alternative, Windows)
      2_UPLOAD_TO_FUSION.txt
      3_run_pipeline.bat              (scripted alternative, Windows)
      SETUP.md
      README.md
      app.py             the window
      shop.default.json  the starter settings. Copied to shop.json the first
                         time the window opens, then never touched again.
      shop.json          appears on first run. The course's real settings -
                         sheet, laser colours, seed, group size. THIS is the
                         file worth keeping; a new download cannot overwrite
                         it, so unzipping an update over this folder is safe.
      roster.txt         the valid group IDs, one per line
      .app_prefs.json    appears after the first run. Only remembers which
                         folders you used on THIS machine; safe to delete,
                         and ignored on any other computer.
      lgpipe\            the code
      fusion\            the two Fusion scripts
```

Avoid OneDrive or Desktop folders that sync — Fusion writing a few hundred
DXFs into a syncing folder is slow and occasionally locks files mid-write.

## 3. Install the Fusion script

In Fusion: **Utilities → ADD-INS → Scripts and Add-Ins → Scripts → green "+"**

Create a script called `BatchExportProject`, then replace the generated `.py`
with `fusion\BatchExportProject.py` from this folder. The generated stub is
about 1 KB; ours is about 13 KB, so the file size tells you the copy landed.

(`ExportFlatPatterns.py` is the single-design version — install it the same way
if you want to test one file at a time.)

---

# Running it — every time

Double-click the launcher for your machine:

```
Landing Gear Pipeline.bat        Windows
Landing Gear Pipeline.command    macOS
```

It checks Python, installs the three libraries the first time, and opens a
window with the steps down the left side. macOS refuses the `.command` on
first open — right-click it, choose Open, then Open again. That happens once.

## Each semester

Open **Each semester** and set three things: the group roster (one ID per
line), the draw seed, and the group size range. Nothing else changes between
one offering of the course and the next.

## 1. Select designs

Import the Brightspace download — the ZIP, as it arrives — and press **Draw the
designs**. You get `selected\` (upload this to Fusion), `drawings\` (the
winners' PDFs), and two records of the draw:

```
selection_log.csv       every candidate, who won, the seed, the timestamp
selection_report.txt    the summary, and anything that needs fixing
```

Re-running does not erase those. The previous pair is renamed with its
timestamp first, so the history of a graded decision accumulates.

## 2. Export in Fusion

Follow `2_UPLOAD_TO_FUSION.txt`, or the same steps shown on that page of the
window. This is the one step no code can do for you — Fusion scripts only run
inside Fusion.

Point the export at the **`raw`** folder inside this directory.

## Before the first real cut: the test card

**Settings → Make a test card** writes `test_card.dxf` — one labelled tile per
colour, about 150 x 110 mm. Cut it on scrap.

```
Tile falls out of the plate      that colour is a THROUGH CUT
Tile marked but still attached   that colour is a MARK
Tile is blank                    that colour is DISABLED
```

Then set the six rows on the Settings page from what you saw, press Save, and
tick **"These colours are checked on the machine with the test card"**. Until
that box is ticked, every build warns that the colours are unchecked. Put the two
reference rows on colours that came back **blank**, so the bend extent lines
cannot be cut by mistake no matter what anyone does to the template later.

The box after each colour is the exact value written into the file. Changing
the colour fills it in; type over it only if this machine keys on a different
value — the number the operator reads off a line that already works. That is
how black is handled: it is written as `#1a1a1a`, not `#000000`, because pure
black's value is zero and some importers read zero as "no colour was set".

## The other card: how the group ID should be marked

**Settings → Make a marking card** writes `marking_card.dxf`, about
183 x 194 mm. Cut it on the same scrap.

```
LEFT column    LINE MARK   hairline letters: the laser follows each stroke
RIGHT column   ENGRAVE     the same letters filled solid: the laser should
                           sweep across them (its engraving settings)
each column    ANGULAR and STANDARD letter shapes, six sizes, the short form
BOTTOM         filled letters at 1.6 mm (what the nut clip gets), four
               thicknesses (10-20%)
```

Find the best line you can read at arm's length without squinting. Then set
three things on the Settings page from what you saw:

```
Group ID row, Lines       hairline (left column won) or filled (right column won)
Group ID row, Thickness   the thickness row that looked best   (filled only)
Letter shapes             angular or standard   (the block it was in)
```

If the filled letters come into CorelDRAW as empty outlines, or do not
appear at all, the fill did not survive the import. Stop there and say so,
rather than filling them by hand: the real sheets would need the same.

A gantry draws a long stroke in one smooth move and a short one by stepping —
a little across, a little up — which is what makes small text look like a
staircase. `angular` never asks for a stroke shorter than 30% of the cap
height, so there is nothing to step.

The short form rows are the same ID shortened, drawn at the size it would get
in the space the small lines occupy. If those read where the full ID does
not, the text is too long for the smallest parts, not the machine's fault.

Until that box is ticked, every run prints a warning and puts a banner at
the top of `report.md`. That warning is the difference between a good sheet and
every blank cut along its own bend lines.

## 3. Build the sheet

Press **Build the sheet**. It prints the paths of the sheets to take to the
laser. Then read `out\report.md` before going to the shop: it lists anything
that could not be cut, the machine setup table, and which groups' parts are on
which sheet.

Each sheet also gets a `.svg` beside it. Double-click it and it opens in your
browser — white paper, real colours, every mark legible. That is for checking
the sheet looks right before you carry it anywhere; it is not a file the laser
uses. A CAD viewer will do the same job but on whatever background it prefers,
and eDrawings prefers black, which is the colour your cut lines are — so they
vanish, which looks alarming and means nothing.

## 4. Copy the sheets to a USB

Only these go to the laser:

```
out\sheets\sheet_01.dxf
out\sheets\sheet_02.dxf
```

Not the `.svg` files — those are the previews, for looking at only.

Nothing else leaves your computer. The DXF is self-contained — it carries the
geometry itself and references no other file.

---

## Rehearsing a whole term before it happens

```
python tests/rehearse.py --problems --run
```

Builds a complete fake cohort — every group on the roster, 3 or 4 members
each, Brightspace-shaped filenames, and a different set of measurements per
group — then runs the selector and the build on it and checks the reports
caught everything it deliberately broke.

`--groups 69` rehearses a larger cohort than you have, to see what a bigger
year would do. Takes about a minute.

Two things it cannot do. It writes stand-in `.f3d` files, because only Fusion
can write real ones — that is fine, since the selector only ever reads
filenames. And it simulates the Fusion export by generating the `raw` folder
directly. Test that one step separately with `ExportFlatPatterns` on a single
real assembly.

Everything else is the real code on realistic input.

---

## If something goes wrong

**"Python is not recognised"** — PATH was not ticked during install. Re-run
the Python installer, choose Modify, tick it. With Anaconda, use the Anaconda
Prompt instead.

**"This Python has no tkinter"** (usually macOS) — install Python from
python.org rather than using the one that came with the system.

**"No module named ezdxf"** — the window was opened directly instead of
through the launcher, so the three libraries were never installed for that
Python. A red line appears across the top of the window with an **Install
them now** button; press it and it fixes itself. It installs into whichever
Python is running the window, which is the one the build will use, so this
also sorts out a machine with two Pythons on it. If it still refuses, close
the window and use the launcher, or run
`pip install ezdxf shapely numpy` from an Anaconda Prompt.

**"No raw folder found"** — the Fusion export has not run yet, or it wrote
somewhere else. Check where you pointed it.

**The colours come into CorelDRAW wrong** — first check `out\report.md`. The
**Machine setup** table lists the colour the file was actually written with,
role by role. If that table says something different from what you set, the
sheet was built before the settings were saved: press **Save to shop.json**,
build again, and look at the table before going to the shop. If the table
matches what you see on screen, the file is right and the mapping at the
machine is what needs changing.

Every line in the sheet carries its own colour, on the object itself, so
nothing has to be reselected and recoloured by hand after importing. If
CorelDRAW is set to a CMYK document, set it to RGB before importing —
converting to CMYK and back leaves a colour close to but not exactly the pure
value the laser profile matches on.

**A part is listed as `MISSING`** — that group named a component wrong, or
modelled it as a solid instead of sheet metal. Check `raw\<group>\` to see
what actually exported.

**A part is listed as `UNEXPECTED`** — a purchased part was modelled as sheet
metal, so it has a flat pattern and got cut. Harmless to the other parts, but
worth a word to that group.

**A roster problem** — a submitted file is named wrong, so its parts would be
marked with an ID belonging to nobody. Fix the file name and re-run; the run
still completes for everyone else.
