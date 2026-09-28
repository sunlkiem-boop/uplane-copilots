#!/usr/bin/env bash
# Start the Uplane Creative Studio helper in the BACKGROUND and open it in the
# browser. No terminal window stays open. Safe to run repeatedly: if the helper
# is already up, this just opens the browser.
#
# Called by the "Start Creative Studio" app (and usable on its own via
# double-click in Finder). Prefers the existing .venv so it needs no PATH/uv.
set -u
cd "$(dirname "$0")"
URL="http://127.0.0.1:8788"
LOG="/tmp/uplane-creative-studio.log"

# Already running? Just open it.
if curl -s -o /dev/null --max-time 1 "$URL/health"; then
  open "$URL"
  exit 0
fi

# Start detached. Use the venv python directly when it exists (no uv/PATH needed),
# otherwise fall back to run.sh (first-time setup). Redirect all three streams so
# the process fully detaches and survives the launcher exiting.
if [ -x ".venv/bin/python" ]; then
  nohup ".venv/bin/python" app.py >"$LOG" 2>&1 </dev/null &
else
  nohup ./run.sh >"$LOG" 2>&1 </dev/null &
fi

# Wait (up to ~30s) for it to answer, then open the browser.
for _ in $(seq 1 60); do
  curl -s -o /dev/null --max-time 1 "$URL/health" && break
  sleep 0.5
done
open "$URL"
