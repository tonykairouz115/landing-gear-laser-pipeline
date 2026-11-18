# Landing-gear laser production pipeline

MAAE/AERO 2001 — Reverse Engineering Project: Retractable Landing Gear Mechanism.

Turns a cohort of Fusion 360 sheet-metal models into laser-ready production
DXFs with every blank automatically cut-marked, bend-marked and ID-marked,
nested onto stock, and validated — with no manual layer clicking in the shop.

## The problem it solves

Seven blanks per group — the Stabalizer is qty 2 — so a cohort of N groups is
**7N flat patterns**. Each needs the outer profile cut, the holes cut, the bend
lines scribed, and the group ID marked. Done by hand in the laser software that
is several hundred clicks, every one of them a chance to mismark a part that
then gets assembled into the wrong team's landing gear.

The cohort size lives in `roster.txt` and nowhere else. This file deliberately
quotes no count: the real numbers for a given run are printed by that run and
written into `selection_report.txt` and `out/report.md`.

## How it works

    L2-G07_Lastname.f3d   x N       every student submits their own model
          |                           (3-4 per group), Fusion files, never DXFs
          v                           1_select_designs.bat
    select_designs.py                 - one winner per group, fixed seed
          |                           - reproducible, blind, order-independent
          v                           - logs every candidate considered
    L2-G07.f3d      one per group   the manufactured designs
          |                           uploaded into one Fusion project folder
          v                           2_UPLOAD_TO_FUSION.txt
    BatchExportProject.py             ONE run. Opens each design, exports all
          |                           six flat patterns, closes without saving.
          v                           Bend centre lines on, extent lines off,
    raw/L2-G07/CHS.dxf ...            splines polylined, millimetres.
                                      Filenames are the part codes, matching
                                      the laser mark on the blank itself.
          |
          v                           3_run_pipeline.bat
    lgpipe.cli                        - classify geometry (not layer names)
          |                           - remap to the shop layer scheme
          v                           - inject ID + logo in a computed safe zone
    out/parts/L2-G07/*.dxf            - Stabalizer duplicated to qty 2
    out/sheets/sheet_01.dxf           - validate, nest, report
    out/report.md
          |
          v
    laser: load DXF, apply saved colour template once, run

### 0. What students submit

**The `.f3d`. Never a DXF.**

If each group exports their own DXFs you inherit a different set of export
settings, and no way to tell whether they modelled true sheet metal or faked
the folds as a solid — which the DXF cannot show you but the Fusion file can.
Take the `.f3d` and the export settings become yours, identical every time.

Submission naming, enforced at the point of upload:

```
L2-G07_Lastname_Firstname.f3d      individual submission, for marking
L2-G07.f3d                         the randomly selected design, for manufacture
```

The group ID is read from the filename up to the first `_` or space, so
`L2-G07_Kairouz_Tony.f3d` and `L2-G07.f3d` both resolve to `L2-G07`. That
string becomes the folder name, the laser mark on all seven blanks, and the
key every downstream report is sorted by. It is the only thing students have
to get right.

### Running it

Double-click the launcher for your machine:

```
Landing Gear Pipeline.bat        Windows
Landing Gear Pipeline.command    macOS
```

It checks Python, installs the three libraries the first time, and opens a
window with the three steps down the left, the settings on the right, and a
log underneath. Same app on both platforms; no packaging, no signing, nothing
to keep up to date. macOS will refuse the `.command` on first open -- right-
click it once, choose Open, then Open again.

The numbered `.bat` files still work for scripted runs on Windows, and every
step is a plain command if you prefer a terminal. The window is just a nicer
way in.

### 1. Select (on your PC)

```
Landing Gear Pipeline            step 1
or: python select_designs.py --submissions submissions
```

The brief says one design per group is "selected at random", and that design is
the one marked and manufactured. That is a graded decision, so the draw is made
on the record rather than by hand:

* **Reproducible** — same submissions and same seed give the same winners, on
  any machine, at any time. Re-run it in front of a student months later and
  the identical name comes out.
* **Blind** — the draw depends only on the filename. Not file size, not date,
  not who submitted first. Candidates are sorted before drawing, so filesystem
  order cannot influence the result.
* **Per-group seeded** — each group's draw is seeded from `(seed, group id)`, so
  a late submission in G12 changes G12's winner and nothing else. A single
  global shuffle would silently reshuffle every other group.
* **Logged** — `selection_log.csv` records every candidate considered, who won,
  the seed and a timestamp.

It also triages what the export would otherwise choke on: a group with three or
five submissions, a filename with no parsable group ID, a group ID not on the
roster, a roster group that submitted nothing.

Output is `selected/` — nothing but the winning `.f3d` files, renamed to the
bare group ID and ready to upload to Fusion — plus `drawings/`, the winner's
PDFs kept beside them so the drawings the unit was built from are at hand when
it comes back.

The seed is fixed in the source. Changing it changes every winner, so it is
deliberately not something a re-run can do by accident.

### 2. Export (in Fusion)

Two scripts, same export settings:

**`fusion/BatchExportProject.py`** — the production path. Walks an entire
Fusion project folder, opens each submission, exports all six flat patterns,
closes without saving. Run once for the whole cohort. Writes `_export_log.csv`
listing every component it exported, skipped, or could not find.

**`fusion/ExportFlatPatterns.py`** — single open design, prompts for the group
ID. For testing and one-off re-runs.

Both create flat patterns where missing and delete the ones they created, so
student files come back unmodified. Most students will have made their own —
the brief requires "real flat patterns, not just folded solids" (§4.2) — in
which case theirs is used and the export settings are still ours.

Files are written under the three-letter part code — `CHS.dxf`, `LCV.dxf`,
`RCV.dxf`, `NUT.dxf`, `LEG.dxf`, `STB.dxf` — the same code laser-marked on the
blank, so a part in the bucket and its source file carry identical
identification. A component whose name is not recognised keeps its full name
and is flagged `NAME` in the log rather than being given an invented code.

### 3. Process (anywhere with Python)

```
pip install ezdxf shapely numpy
python -m lgpipe.cli --config shop.json --raw raw/ --out out/
```

**Settle the settings once, in a profile.** Every run decision — sheet size,
kerf, mark style, nesting — lives in a JSON profile written once and reused
forever, so a run is one command with nothing to remember. `--write-config
shop.json` writes a starter. Next year's TA runs the same line without
knowing what a kerf is.

```json
{
  "sheet": "1219.2x1219.2",
  "kerf": 0,
  "mark": "group-code",
  "nest_mode": "corner",
  "material_thickness": 1.52
}
```

An explicit flag on the command line still overrides the profile, for one-off
experiments.

Options:

| Flag | Default | Notes |
|---|---|---|
| `--sheet WxH` | `1220x2440` | stock sheet in mm |
| `--mark` | `group-code` | `group` marks section and group only (`L2-G07`); `group-code` appends the part code (`L2-G07 CHS`); `group-part` appends the raw component name |
| `--nest-mode` | `corner` | `corner` = compact, grown out of one corner so the offcut stays one rectangle; `compact` = fewest sheets; `group` = keep each group's blanks together; `per-group` = one sheet per group |
| `--kerf MM` | `0` | pre-compensate cut paths (see **Kerf compensation** below); `0` = machine compensates |
| `--part-gap MM` | `4.0` | steel left between neighbouring blanks |
| `--sheet-margin MM` | `8.0` | clear border around the whole sheet |
| `--logo FILE.dxf` | none | line-art logo, marked under the ID |
| `--config FILE` | none | shop profile holding all of the above |
| `--roster FILE` | none | expected group IDs; flags a folder whose ID would mark a nonexistent group |
| `--marking-annex` | off | extra per-group deviation CSV; not needed for production |
| `--no-nest` | off | per-part files only |

Exit code is non-zero if any part failed validation, so it drops into CI or a
marking script unchanged.

### 4. Cut

The shop maps layers to processes **once** and saves it as a template:

| Order | Layer | ACI | RGB | Process | Cut side |
|---|---|---|---|---|---|
| 1 | `MARK_TEXT` | 6 | 255,0,255 | low-power mark | — |
| 2 | `MARK_BEND` | 3 | 0,255,0 | low-power scribe | — |
| 3 | `CUT_INNER` | 5 | 0,0,255 | through cut | **inside** |
| 4 | `CUT_OUTER` | 1 | 255,0,0 | through cut | **outside** |
| — | `REF_EXTENT` | 8 | 128,128,128 | **do not output** | — |
| — | `REF_FRAME` | 8 | 128,128,128 | **do not output** | — |

Both reference layers share a colour on purpose: they are one setting to
disable rather than two to remember, and it leaves the black index free, since
black is a common cut colour and a sheet frame sharing it would cut the sheet
in half. If any two roles that must run differently end up on one colour, the
run says so on screen and puts a banner at the top of `report.md`.

Every entity carries its colour on itself — colour index and true RGB — as
well as sitting on a correctly-coloured layer. Leaving entities `BYLAYER`
would be tidier, but then the colour lives only in the layer table and the
importer has to resolve it; CorelDRAW, which drives a lot of laser shops, does
that badly, and the geometry arrives in a default colour that matches nothing
in the machine profile. Writing it on the object removes the lookup.

**Changing any of this.** Sheet size, material thickness, part spacing, and
the layer names and colours all live in `shop.json` — nothing needs editing in
the code:

```json
{
  "sheet": "1219.2x1219.2",
  "material_thickness": 1.52,
  "part_gap": 4.0,
  "sheet_margin": 8.0,
  "layers": {
    "CUT_OUTER": { "name": "CUT",     "aci": 1, "rgb": [255, 0, 0] },
    "CUT_INNER": { "name": "CUT_HOLE","aci": 5, "rgb": [0, 0, 255] },
    "MARK_BEND": { "name": "SCRIBE",  "aci": 3, "rgb": [0, 255, 0] },
    "MARK_TEXT": { "name": "ENGRAVE", "aci": 6, "rgb": [255, 0, 255] }
  }
}
```

The keys on the left (`CUT_OUTER`, `MARK_BEND`, …) are internal roles and never
change — they are what the classifier assigns. The `name`, `aci` and `rgb` are
what actually get written into the DXF, so if the shop already has a template
built around their own names and colours, match theirs rather than making them
rebuild it. Omit any key to keep the default.

**Kerf compensation.** The beam removes a slot of material centred on the path
it follows. Uncompensated, every part finishes half a kerf undersize and every
hole half a kerf oversize. So the kerf must always fall in the scrap: the beam
runs **outside** the line on the outer profile and **inside** it on holes.

Set this on the machine, per layer — that is what the separate `CUT_OUTER` and
`CUT_INNER` layers are for, and it is the correct place for it because the real
kerf depends on that machine's power, speed and material. The DXF ships nominal
geometry, which is also what makes it checkable against the drawing.

If the shop genuinely cannot set it machine-side, `--kerf 0.15` pre-offsets the
paths in the file instead. Do **not** do both — compensating twice puts parts a
full kerf out of tolerance. When `--kerf` is used the report says so in bold at
the top.

Order is deliberate: marks first while the blank is still held by the sheet,
interior cuts before the outer profile so the part cannot shift once free.

Every entity carries the layer name, the ACI index *and* an explicit RGB
value, because different front-ends key off different things — LightBurn maps
by colour, most CAM maps by layer name. One export works on either.

## Reference model requirements

The pipeline reads `component.flatPattern`. A part modelled as a folded solid
has no flat pattern, so **it exports nothing at all** — silently, per
component. This is the single most common way a submission fails, and it is
worth stating in the assignment in exactly these terms.

What a submission must have:

| Requirement | Why |
|---|---|
| Sheet Metal workspace, not Solid | Only sheet-metal bodies produce a flat pattern |
| One sheet-metal rule: 1.52 mm steel | Thickness is validated against the brief |
| Bend radius and K-factor set in the rule | Drives the flat pattern length; wrong K-factor = wrong blank |
| Six components, named exactly as the brief | The exporter finds parts by name: `Chassie`, `Left Cover`, `Right Cover`, `Nut`, `Leg`, `Stabalizer` |
| Each part its own component | Bodies inside one component cannot be flattened separately |
| No sketch geometry left on the flat face | Stray closed loops classify as holes and get cut |

Issue a starter file with the rule preset and the six components pre-named and
empty. It costs you an hour and removes the most common failure mode from all
a whole cohort's submissions at once.

## The pipeline transcribes, it does not correct

**Every blank is cut exactly as submitted.** A group that forgot a hole gets a
part with no hole. A group that measured 70 mm where the reference is 61.7 gets
a 70 mm part. A group that modelled a bend that does not exist gets that bend
scribed, and can try to fold it. Getting it wrong *is* the assessment, and a
pipeline that quietly corrected those mistakes would erase the thing being
marked.

The only fatal conditions are physical impossibilities: a profile that is not
closed, or a blank larger than the stock sheet. Those cannot be cut at all, so
there is nothing to transcribe.

**This tool does not mark.** Marking is done by hand, with vernier calipers on
the assembled unit. The pipeline's whole job is turning a cohort of submissions into
correct blanks. `--marking-annex` will write a `marking.csv` of per-group
deviations if you ever want the numbers, but it is off by default and nothing
downstream depends on it.

## What it catches, and what it cannot

The DXF handling is robust. The risk is in what students submit, so the
pipeline is built to make those deviations visible.

| Student mistake | Detected? | How |
|---|---|---|
| Modelled as a folded solid, not sheet metal | yes | no flat pattern exists -> exports nothing, logged `MISSING` |
| Component named wrong | yes | expected six not found, logged `MISSING` |
| **File named wrong** (`L2-G7` not `L2-G07`) | **only with `--roster`** | otherwise silent: valid parts, wrong ID marked |
| Stray sketch left on the flat face | yes | one more interior cut than the rest of the class |
| A hole never modelled | yes | one fewer interior cut than the rest of the class |
| Wrong scale / mis-read caliper | yes | bounding box outside the cohort spread |
| Extra or missing bend | yes | bend count differs from the class |
| Profile not watertight | yes | fatal, part rejected |
| Whole part missing | yes | `MISSING PARTS` for that group |
| Different K-factor from a classmate | no, by design | that is the assignment |
| Correct model of a mis-measured part | no | nothing can know; the cohort check flags only gross errors |

**Always pass `--roster`.** A wrong file name is the one failure that produces
perfectly valid parts carrying somebody else's ID. Geometry cannot detect it,
and the error is only discovered when a group tries to assemble a chassis that
isn't theirs. The roster names it explicitly and flags every affected part in
`marking.csv` — the run still completes, so one badly named submission never
holds up the other 68.

```
python -m lgpipe.cli --config shop.json --roster roster.txt --raw raw/ --out out/
```

The cohort check exists because a full cohort model the *same* landing gear. No
single-file validator could catch a stray sketch or a mis-scaled part -- but
checked against 68 independent opinions of what that part should be, it stands
out immediately.

## Design decisions worth knowing

**Geometry decides, layer names only hint.** Fusion separates outer profile /
interior profiles / bend lines onto distinct layers, and that is what makes
this cheap. But those names have drifted between releases and differ from the
Inventor exporter, and a file round-tripped through another tool can lose them
entirely. So the classifier stitches entities into chains, finds closed loops,
takes the largest-area loop as the outer profile, loops inside it as holes,
and open chains as bend lines. Layer names break only the one genuinely
ambiguous case: bend *centre* line vs bend *extent* line. Tested against three
layer-naming conventions including everything dumped on layer `0`.

**The ID mark is injected, not modelled.** Asking a full cohort to sketch their own
ID gives you as many fonts and as many chances to put text across a bend. The pipeline
computes the safe region — the profile eroded off the cut edge, minus holes
grown by a clearance, minus bend lines grown by a clearance — finds the
largest empty rectangle in it, and fits the text there, rotating 90° or
shortening to the group number if that fits better. Subtracting the bend lines
is what automatically confines the mark to a single flange, so it stays flat
and readable after forming.

**The mark scales with the part, and never loses the section.** Height is a
fraction of the largest clear area on that specific blank, clamped between
1.6 mm (readable on steel) and 8 mm (past which marking time grows for
nothing). So a chassis carries ~8 mm and a stabiliser link ~4.5 mm, both
proportionate rather than both rubber-stamped at one size.

Where a part is too cramped for `L2-G07` on one line — the captive nut clip
has a safe area of roughly 8 × 11 mm — the tool stacks it as `L2` over `G07`
instead of shrinking. What it will *not* do is abbreviate to `G07`: if group
numbers restart per lab section then `L1-G01` and `L2-G01` would carry
identical marks, and telling those apart is the mark's entire job. A smaller
mark is a nuisance; an ambiguous one is a wrong part in someone's box. Parts
with room for neither layout are reported rather than quietly abbreviated.

**A single-stroke font, built in.** TrueType glyphs are closed outlines; a
laser traces every letter's perimeter, roughly tripling mark time and looking
wrong. SHX/LFF stroke fonts fix that but must be installed wherever the file
is opened. `strokefont.py` embeds the strokes, so output is plain polylines
that render identically everywhere with no dependency.

**Letterforms the machine can actually draw.** A gantry draws a long stroke as
one smooth coordinated move and a short one by stepping — a little across, a
little up — so the chamfers that round off an `O` or an `S` turn into a
staircase once they are a third of a millimetre long, which is what a 2 mm
mark makes of them. The `angular` font is the same letterforms with every
curve replaced by long straight runs: the shortest stroke in it is 30% of cap
height, against 14% before, and a typical mark drops from 62 segments to 53.
It is the default; `"font": "standard"` restores the rounded set. **Settings →
Make a marking card** puts both on one piece of scrap at six sizes so the
choice is made by looking at steel rather than by arguing.

**Line mark or engrave.** A thin line, a laser *follows*: the head traces it
with the beam on, starting, stopping and turning on every stroke of every
letter. On bare steel those starts are where the blobs come from. A filled
shape, a laser *engraves*: it sweeps back and forth, already at speed, and
fills the shape in, with no start or stop inside a letter. Each colour row on
the Settings page has a **Lines** choice: hairline or filled. Setting the Group
ID row to filled writes the group ID the second way: each stroke thickened to
the row's thickness (a percent of the letter height, 13 by default), merged
into solid letters, and written as a filled HATCH with no outline, one object
per mark. The bend-line row can be filled too, as a band so many mm wide. Cut
and reference rows are always hairlines: a filled outline would engrave the
whole part. In `shop.json` this is `"style"` and `"stroke"` on each layer.
The zero loses its slash in this mode (thickened, the slash closes the
counter up) and is drawn narrower than the letter O instead. Letters are
spaced out by half a stroke so neighbours never touch, and placement allows
for the extra width, so a filled mark still lands in the clear area. Bend lines
and cuts are lines in both modes. The marking card has both, side by side, plus
a row of four thicknesses.

**Marking pays for the nesting.** Because every blank carries its own ID,
parts do not need to be physically adjacent to stay sortable. That allows
height-sorted first-fit packing across the whole cohort instead of
group-by-group: at every blank that is **1 sheet instead of 2**, measured.
`--nest-mode group` reverts to sorted-but-wasteful if marking is ever off.

## Verification

`tests/make_fixtures.py` generates deliberately adversarial flat patterns —
profiles as separate LINE/ARC entities (as Fusion writes them), three
different layer-naming conventions, per-group dimensional jitter.

Checked at full cohort scale: every blank placed, none dropped, zero
overlapping pairs, minimum gap exactly at the 4 mm spec, no geometry outside
the sheet, whole run in ~14 s.

```
python3 tests/make_fixtures.py                       # writes raw_test/, never raw/
python3 -m lgpipe.cli --raw raw_test --out out_test --config shop.json
```

## Open items

- Confirm the shop's machine and software so the colour template can be built
  and tested on one real sheet before the cohort run.
- Supply the course logo as line art (DXF) for `--logo`; it is optional and
  omitted automatically on parts too small to carry it.
- Bounding-box nesting leaves more scrap than true contour nesting. At these
  part shapes the loss is small and the result is deterministic, which matters
  more here — but a real nester could be dropped in behind the same interface.
