# Submission check

Separate from the laser pipeline. It reads the Brightspace bulk download, changes nothing, and gives you:

- **Who submitted:** every student on the class list, marked *Submitted*, *No .f3d (other files only)* or *NOT SUBMITTED*.
- **Which pairs look copied:** every pair of students in the class, compared signal by signal. Each signal is its own column, so you can see why a pair was flagged.

A flag is not proof. It means "open these two side by side".

## What you need

- Python, the same one the pipeline uses. Nothing to install.
- The Brightspace bulk download of the assignment, as the .zip or unzipped.
- `classlist.csv`: the Brightspace class list export with first name, last name and student number, plus a group column if you can include one. Without it the check still runs, but it can't list students who submitted nothing.

## Run it

1. Put `classlist.csv` next to `run_check.bat`.
2. Drag the Brightspace download onto `run_check.bat`, or save it next to the .bat as `download.zip` and double-click the .bat.
3. `submission_check\report.html` opens when it's done. There are also `students.csv` and `pairs.csv` for Excel.

By hand:

```
python check_submissions.py --submissions download.zip --classlist classlist.csv
```

## The columns

| Strength | Column | What it means |
|---|---|---|
| Strong | Same file | Byte-for-byte identical .f3d |
| Strong | Shared internal data | Identical pieces inside the file (such as body geometry) that almost nobody else has |
| Strong | Shared internal IDs | Hidden IDs Fusion gives every body, sketch and feature. They carry over when a file is copied; two people modelling separately never share them |
| Medium | Same save stamps | Parts of the file saved at the exact same second |
| Medium | Shared exact numbers | Values with 4+ decimals found in both files and almost nowhere else |
| Medium | Shared rare names | The same unusual names or typos inside the file |
| Medium* | Same build history | Same features in the same order, and few others in the class built it that way |
| Medium* | All dims identical | Every sketch dimension matches |
| Medium* | Same rare dims | Dimension values few others in the class used |
| Medium* | Same geometry | Every body's volume and area match |
| Medium* | Same custom names (Fusion) | Same non-default component, body and sketch names |
| Weak | Similar file size, Same saved view*, Submitted within 10 min | Shown, but never flag a pair on their own |
| - | Submitted first | Who uploaded earlier: a hint about who had the original, not proof |

Anything most of the class shares, such as Fusion's own defaults or a starter file, is ignored automatically. Only things a handful of students have in common count, by default 5% of the class or fewer (`--rare`).

\* Columns marked * need the Fusion step below. Without it they show `-`.

## Optional: the Fusion step

Build history, dimensions, geometry and saved view can only be read by Fusion.

1. Make a new Fusion project, separate from the laser one, and upload **all** the .f3d files into it without renaming them.
2. Add `fusion\FingerprintDesigns.py` as a script (Utilities > Scripts and Add-Ins > + ) and run it with that project active. Pick an empty folder.
3. Name that folder `fingerprints` and put it next to `run_check.bat`, or pass it with `--fusion-data`.

Every design is opened and closed without saving, so for a full class this takes a while.

## Before trusting it

The scoring has been tested on made-up files only. Before the real run:

1. Send 3 real .f3d files: two from different students, and one that is a copy of another, renamed and re-saved in Fusion. That confirms which signals survive a real copy.
2. On the first real batch, check a few flags by hand. If unrelated pairs are flagged, the bar can be raised.
