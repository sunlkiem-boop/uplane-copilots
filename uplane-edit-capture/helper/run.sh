#!/usr/bin/env bash
# Start the Edit Capture helper (:8791). Stdlib-only — no venv needed.
set -euo pipefail
cd "$(dirname "$0")"
echo "Starting Edit Capture helper on http://127.0.0.1:8791  (leave this window open)"
exec python3 app.py
