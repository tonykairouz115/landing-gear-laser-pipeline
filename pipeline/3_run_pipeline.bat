@echo off
REM ===================================================================
REM  MAAE/AERO 2001 - Landing gear project
REM  STEP 3 of 3: build the production sheet for the laser.
REM
REM  Double-click this file. It checks the setup, installs anything
REM  missing the first time, then builds the production sheets.
REM
REM  Expects this folder layout:
REM      lgpipe\                  <- you are here
REM         1_select_designs.bat
REM         2_UPLOAD_TO_FUSION.txt
REM         3_run_pipeline.bat
REM         shop.json
REM         roster.txt
REM         raw\                  <- Fusion batch export writes here
REM            MAAE-L1-G01\   CHS.dxf LCV.dxf RCV.dxf NUT.dxf LEG.dxf STB.dxf
REM            MAAE-L1-G02\  ...
REM         out\                  <- results appear here
REM ===================================================================

setlocal
cd /d "%~dp0"
echo.
echo  MAAE/AERO 2001 landing gear pipeline
echo  ====================================
echo.

REM --- 1. Is Python installed? ---------------------------------------
python --version >nul 2>&1
if errorlevel 1 (
    echo  [X] Python is not installed, or was installed without
    echo      "Add Python to PATH" ticked.
    echo.
    echo      Install from https://www.python.org/downloads/
    echo      and TICK "Add python.exe to PATH" on the first screen.
    echo.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('python --version') do echo  [OK] %%v

REM --- 2. Are the three libraries present? ----------------------------
python -c "import ezdxf, shapely, numpy" >nul 2>&1
if errorlevel 1 (
    echo  [..] Installing required libraries, one moment...
    python -m pip install --quiet --upgrade pip
    python -m pip install --quiet ezdxf shapely numpy
    if errorlevel 1 (
        echo  [X] Library install failed. Check your internet connection.
        pause
        exit /b 1
    )
    echo  [OK] Libraries installed
) else (
    echo  [OK] Libraries present
)

REM --- 3. Is there anything to process? -------------------------------
if not exist "raw" (
    echo.
    echo  [X] No "raw" folder found.
    echo      Run the Fusion batch export first and point it at:
    echo        %cd%\raw
    echo.
    pause
    exit /b 1
)

REM --- 4. Optional files -----------------------------------------------
REM Always pass shop.json: if it does not exist yet, the pipeline falls back to
REM shop.default.json (the 48 x 48 in sheet) rather than a built-in default.
set EXTRA= --config shop.json
if exist "roster.txt" set EXTRA=%EXTRA% --roster roster.txt
set PYTHONUTF8=1

echo.
echo  Building production sheets...
echo  -----------------------------------
python -m lgpipe.cli --raw raw --out out %EXTRA%
set RESULT=%errorlevel%
echo  -----------------------------------

if %RESULT%==3 (
    echo.
    echo  NOT READY TO CUT - some groups were left off the sheet or need renaming.
    echo  See the lines marked !!! above and out\report.md, fix them, run again.
    goto :end
)
if %RESULT%==0 (
    echo.
    echo  DONE. Take these to the laser:
    echo.
    for %%f in (out\sheets\*.dxf) do echo        %cd%\%%f
    echo.
    echo  Read out\report.md before cutting.
) else (
    echo.
    echo  FINISHED WITH PROBLEMS - some parts could not be cut.
    echo  Open out\report.md and fix those before going to the shop.
)


:end
echo.
pause
endlocal
