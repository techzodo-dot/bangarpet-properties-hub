#!/usr/bin/env bash
# One-command local demo for macOS / Linux:  ./run_demo.sh
# Creates a virtual environment, installs dependencies, sets up the database,
# loads [DEMO] data and starts the site at http://127.0.0.1:8000/
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
  done
fi
[ -n "$PY" ] || { echo "Python 3.10+ is required. Install it from https://www.python.org/downloads/"; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo "Python 3.10 or newer is required (found $("$PY" --version 2>&1))."; exit 1; }

if [ ! -d .venv ]; then
  echo "==> Creating virtual environment (.venv)"
  "$PY" -m venv .venv
fi
VPY=".venv/bin/python"

echo "==> Installing dependencies"
"$VPY" -m pip install --quiet --upgrade pip
"$VPY" -m pip install --quiet -r requirements.txt

[ -f .env ] || { echo "==> Creating .env from .env.example"; cp .env.example .env; }

echo "==> Setting up the database"
"$VPY" manage.py migrate --verbosity 0
echo "==> Loading demo data"
"$VPY" manage.py seed_demo

PORT="${PORT:-8000}"
echo
echo "============================================================"
echo "  Bangarpet Property Hub demo:  http://127.0.0.1:${PORT}/"
echo "  Admin panel:                  http://127.0.0.1:${PORT}/management/"
echo "  Password for all demo accounts: DemoPass#2024"
echo "  Press Ctrl+C to stop."
echo "============================================================"
exec "$VPY" manage.py runserver "127.0.0.1:${PORT}"
