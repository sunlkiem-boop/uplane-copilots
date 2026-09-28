#!/usr/bin/env bash
# Start the local compliance helper.
#
# Pins everything to its OWN Python 3.12 virtualenv so it never touches the
# Anaconda (base) environment — which has a broken pyodbc metadata that makes
# uv error out. Re-runnable; the venv is created once and reused.
set -euo pipefail
cd "$(dirname "$0")"

VENV=".venv"

if [ ! -x "$VENV/bin/python" ]; then
  echo "First run: creating an isolated Python 3.12 environment…"
  uv venv --python 3.12 "$VENV"
fi

# Install / update dependencies into the venv (fast after the first time).
uv pip install --python "$VENV/bin/python" --quiet -r requirements.txt

echo "Starting helper on http://127.0.0.1:8787  (leave this window open)"
exec "$VENV/bin/python" app.py
