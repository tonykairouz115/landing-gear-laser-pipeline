GROUP RE-CHECK  (separate from the laser pipeline -- changes nothing in it)

Finds, for each group whose selected design failed, the first member in draw
order whose design has all six parts as sheet metal with brief names.
Nobody's parts are combined and nothing is renamed. If no member qualifies,
that group has no parts made.

1. COPY THE MEMBERS  (Command Prompt, in this folder)

   python prepare_candidates.py --export-log "C:\MAAE2001 with f3z\lgpipe\raw\_export_log.csv" --submissions "C:\MAAE2001 with f3z\lgpipe\submissions" --roster "C:\MAAE2001 with f3z\lgpipe\roster.txt"

   -> "candidates" folder: every member of every failed group, named
      AERO-L1-G03__p2__Smith-Kodi.f3d  (group, draw order, student)
   -> candidates\candidates.csv: who is who, and the original file name

2. CHECK THEM IN FUSION  (read-only, nothing exported)

   - Install fusion\CheckDesigns.py as a NEW script called CheckDesigns
     (do not replace BatchExportProject).
   - Make a NEW Fusion project, upload everything in "candidates", make it
     the active project, run CheckDesigns, pick a folder for the reports.

   -> check_results.csv  : every member, each part OK or what is wrong
   -> best_per_group.csv : the member to use for each group, or NONE COMPLETE

3. PUT THE WINNERS BACK INTO THE PIPELINE  (by hand)

   For each group in best_per_group.csv:
   - take that student's ORIGINAL file (candidates.csv has its name),
   - copy it into the pipeline's "selected" folder named with the group ID
     (e.g. AERO-L1-G03.f3d), replacing the old one,
   - export it the usual way (upload + BatchExportProject, or open it and
     run ExportFlatPatterns), into a fresh raw\<GROUP> folder.
   For a group marked NONE COMPLETE: delete its folder from raw.

   Also delete any stray DXF in raw that is not CHS/LCV/RCV/NUT/LEG/STB
   (for example raw\AERO-L1-G10\Stabalizer v3 (1).dxf).

Keep check_results.csv: it says why each group got what it got, for the
final report.
