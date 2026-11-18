@echo off
REM =====================================================================
REM  MAAE/AERO 2001 - Landing Gear Pipeline   (Windows)
REM
REM  Double-click this file. It checks Python, installs anything missing
REM  the first time, then opens the pipeline window.
REM =====================================================================

setlocal
set PYTHONUTF8=1
cd /d "%~dp0"

set PY=
for %%c in (py python) do (
    if not defined PY (
        %%c -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
        if not errorlevel 1 set PY=%%c
    )
)

if not defined PY (
    echo.
    echo  Python 3 is not installed, or was installed without
    echo  "Add Python to PATH" ticked.
    echo.
    echo  Install from https://www.python.org/downloads/
    echo  and TICK "Add python.exe to PATH" on the first screen.
    echo.
    echo  If you have Anaconda, open "Anaconda Prompt" from the Start
    echo  menu and run this file from there instead.
    echo.
    pause
    exit /b 1
)

%PY% -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo.
    echo  This Python has no tkinter, so the window cannot open.
    echo  Reinstall Python from python.org and leave "tcl/tk and IDLE"
    echo  ticked, then run this again.
    echo.
    pause
    exit /b 1
)

%PY% -c "import ezdxf, shapely, numpy" >nul 2>&1
if errorlevel 1 (
    echo  Installing the three libraries the pipeline needs, one moment...
    %PY% -m pip install --quiet --upgrade pip
    %PY% -m pip install --quiet ezdxf shapely numpy
    %PY% -c "import ezdxf, shapely, numpy" >nul 2>&1
    if errorlevel 1 (
        echo  Trying again into your own folder...
        %PY% -m pip install --quiet --user --break-system-packages ezdxf shapely numpy
    )
)

REM  If that still did not work, open the window anyway. It shows a red
REM  line at the top with an "Install them now" button, which is friendlier
REM  than a console that closes itself.

start "" %PY% app.py
endlocal
