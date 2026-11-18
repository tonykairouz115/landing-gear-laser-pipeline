@echo off
REM ===================================================================
REM  MAAE/AERO 2001 - submission check
REM  Who submitted, who didn't, and which submissions look copied.
REM  Separate from the laser pipeline. Changes no files.
REM
REM  EITHER drag the Brightspace download (.zip or folder) onto this file,
REM  OR put it next to this file as "download.zip" or a "download" folder
REM  and double-click.
REM
REM  Also next to this file, if you have them:
REM      classlist.csv   Brightspace class list export (needed for
REM                      "who didn't submit")
REM      fingerprints\   output of FingerprintDesigns.py in Fusion
REM                      (optional: build history, dims, geometry)
REM
REM  Result: submission_check\report.html opens when it's done.
REM ===================================================================

setlocal
set PYTHONUTF8=1
cd /d "%~dp0"
echo.
echo  MAAE/AERO 2001 - submission check
echo  =================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo  [X] Python is not on your PATH.
    echo      Open "Anaconda Prompt" from the Start menu and run this file
    echo      from there.
    echo.
    pause
    exit /b 1
)

set "SRC=%~1"
if "%SRC%"=="" if exist "download.zip" set "SRC=download.zip"
if "%SRC%"=="" if exist "download" set "SRC=download"
if "%SRC%"=="" (
    echo  [X] No submissions found.
    echo      Drag the Brightspace download onto this file, or put it here
    echo      as download.zip or a folder called download.
    echo.
    pause
    exit /b 1
)

set "EXTRA="
if exist "classlist.csv" set "EXTRA=--classlist classlist.csv"
if not exist "classlist.csv" echo  [!] No classlist.csv: students who submitted nothing can't be listed.
if exist "fingerprints" set "EXTRA=%EXTRA% --fusion-data fingerprints"

python check_submissions.py --submissions "%SRC%" --out submission_check %EXTRA%
if errorlevel 1 (
    echo.
    echo  [X] The check did not finish. See the message above.
    pause
    exit /b 1
)

start "" "submission_check\report.html"
echo.
pause
endlocal
