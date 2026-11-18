@echo off
REM ===================================================================
REM  MAAE/AERO 2001 - Landing gear project
REM  STEP 1 of 3: pick one design per group, at random, on the record.
REM
REM  Double-click this file.
REM
REM  BEFORE YOU RUN IT
REM      Put every student .f3d submission under:
REM          lgpipe\submissions\
REM      Subfolders are fine - Brightspace bulk downloads often put each
REM      student in their own folder, and that works as-is.
REM
REM  WHAT YOU GET
REM      selected\               the winners. Upload THIS to Fusion.
REM      drawings\              the winners' PDFs, one folder per group
REM      selection_log.csv      every candidate and who was picked
REM      selection_report.txt   summary, and anything that needs fixing
REM
REM  THEN
REM      Step 2: see 2_UPLOAD_TO_FUSION.txt
REM      Step 3: 3_run_pipeline.bat
REM ===================================================================

setlocal
set PYTHONUTF8=1
cd /d "%~dp0"
echo.
echo  MAAE/AERO 2001 - random design selection
echo  =======================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo  [X] Python is not on your PATH.
    echo.
    echo      If you have Anaconda, open "Anaconda Prompt" from the Start
    echo      menu, then run this file from there:
    echo          cd /d "%~dp0"
    echo          1_select_designs.bat
    echo.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('python --version') do echo  [OK] %%v

if not exist "submissions" (
    echo.
    echo  [X] No "submissions" folder found.
    echo      Create it and put the student .f3d files inside:
    echo        %cd%\submissions
    echo.
    pause
    exit /b 1
)

set EXTRA=
if exist "roster.txt" set EXTRA=--roster roster.txt

echo.
python select_designs.py --submissions submissions --out selected ^
       --drawings drawings %EXTRA%
set RESULT=%errorlevel%

if %RESULT%==0 (
    echo.
    echo  DONE. Next: upload the "selected" folder to Fusion and run
    echo  BatchExportProject.
) else (
    echo.
    echo  FINISHED WITH PROBLEMS.
    echo  Open selection_report.txt and fix those before exporting.
    echo  The designs that WERE selected are still in "selected".
)

echo.
pause
endlocal
