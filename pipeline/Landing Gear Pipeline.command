#!/bin/bash
# =====================================================================
#  MAAE/AERO 2001 - Landing Gear Pipeline   (macOS)
#
#  Double-click this file. If macOS says it cannot be opened, right-click
#  it once and choose Open, then Open again - that only happens the first
#  time.
# =====================================================================
cd "$(dirname "$0")" || exit 1

PY=""
for c in python3 python3.12 python3.11 python; do
  if command -v "$c" >/dev/null 2>&1; then
    if "$c" -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" 2>/dev/null; then
      PY="$c"; break
    fi
  fi
done

if [ -z "$PY" ]; then
  echo "Python 3 is not installed."
  echo "Install it from https://www.python.org/downloads/ and run this again."
  read -r -p "Press return to close." _
  exit 1
fi

echo "Using $($PY --version)"

if ! "$PY" -c "import tkinter" >/dev/null 2>&1; then
  echo
  echo "This Python has no tkinter, so the window cannot open."
  echo "Install Python from https://www.python.org/downloads/ - that build"
  echo "includes it - then run this again."
  read -r -p "Press return to close." _
  exit 1
fi

if ! "$PY" -c "import ezdxf, shapely, numpy" >/dev/null 2>&1; then
  echo "Installing the three libraries the pipeline needs, one moment..."
  "$PY" -m pip install --quiet --upgrade pip >/dev/null 2>&1
  "$PY" -m pip install --quiet ezdxf shapely numpy
  if ! "$PY" -c "import ezdxf, shapely, numpy" >/dev/null 2>&1; then
    # Homebrew and some system Pythons refuse to install into their own
    # folder. Put the packages in this user's folder instead.
    echo "Trying again into your own folder..."
    "$PY" -m pip install --quiet --user --break-system-packages ezdxf shapely numpy
  fi
fi

# If that still did not work, open the window anyway. It shows a red line
# at the top with an "Install them now" button, which is friendlier than a
# terminal that closes itself.

exec "$PY" app.py
