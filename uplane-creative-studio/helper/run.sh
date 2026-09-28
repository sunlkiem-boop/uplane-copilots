#!/usr/bin/env bash
# Start the Uplane Creative Studio local helper (serves the web UI + API on
# http://127.0.0.1:8788).
#
# Pins everything to its OWN Python 3.12 virtualenv so it never touches the
# Anaconda (base) environment. Re-runnable; the venv is created once and reused.
set -euo pipefail
cd "$(dirname "$0")"

VENV=".venv"

if [ ! -x "$VENV/bin/python" ]; then
  echo "First run: creating an isolated Python 3.12 environment…"
  uv venv --python 3.12 "$VENV"
fi

uv pip install --python "$VENV/bin/python" --quiet -r requirements.txt

echo "Starting Uplane Creative Studio on http://127.0.0.1:8788  (leave this window open)"
exec "$VENV/bin/python" app.py
