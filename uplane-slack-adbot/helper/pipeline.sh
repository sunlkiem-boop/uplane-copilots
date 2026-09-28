#!/usr/bin/env bash
# Keep the board fed while the prompts are still being written.
#
# Building 42 prompts takes ~an hour; the board takes ~6 min per job. Waiting for
# the build to finish before firing anything wastes that whole hour, so this just
# re-runs fire_batch.py every few minutes — it enqueues only prompts it hasn't
# sent yet (batch_sent.json), so re-running is safe and idempotent.
#
# Exits once the builder is gone AND every built prompt has been queued.
set -uo pipefail
cd "$(dirname "$0")"

INTERVAL=${INTERVAL:-180}
LOG=logs/pipeline.log
mkdir -p logs

while true; do
  built=$(ls batch_prompts/*.txt 2>/dev/null | wc -l | tr -d ' ')
  out=$(.venv/bin/python fire_batch.py 2>&1)
  echo "[$(date +%H:%M:%S)] built=$built $(echo "$out" | tail -1)" >> "$LOG"
  sent=$(python3 -c "import json;print(len(json.load(open('batch_sent.json'))))" 2>/dev/null || echo 0)
  # builder finished and nothing is left unsent -> done
  if ! pgrep -f batch_rocka.py > /dev/null && [ "$sent" -ge "$built" ]; then
    echo "[$(date +%H:%M:%S)] pipeline done — $sent prompts queued" >> "$LOG"
    exit 0
  fi
  sleep "$INTERVAL"
done
