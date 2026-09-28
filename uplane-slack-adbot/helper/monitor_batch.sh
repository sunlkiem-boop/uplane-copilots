#!/bin/bash
# Detached overnight monitor: records batch + board progress every 3 minutes.
cd "$(dirname "$0")"
OUT=batch_monitor.log
for i in $(seq 1 80); do
  H=$(curl -s -m5 http://127.0.0.1:8790/health 2>/dev/null)
  Q=$(echo "$H" | .venv/bin/python -c 'import json,sys;d=json.load(sys.stdin);print(f"q={d[\"queued\"]} run={d[\"running\"]} idle={int(d[\"driver_idle_s\"])}s")' 2>/dev/null)
  B=$(grep -c 'prompt OK\|from cache' batch_rocka.log 2>/dev/null)
  R=$(grep -c 'REFUSED' batch_rocka.log 2>/dev/null)
  E=$(grep -c 'queued ' batch_rocka.log 2>/dev/null)
  ALIVE=$(pgrep -f batch_rocka.py | wc -l | tr -d ' ')
  echo "[$(date +%H:%M:%S)] $Q | built=$B enqueued=$E refusals=$R batch_alive=$ALIVE" >> "$OUT"
  sleep 180
done
