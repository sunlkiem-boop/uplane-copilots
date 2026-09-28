#!/bin/bash
cd "$(dirname "$0")"
for i in $(seq 1 200); do
  H=$(curl -s -m5 http://127.0.0.1:8790/health 2>/dev/null)
  Q=$(echo "$H" | .venv/bin/python -c 'import json,sys;d=json.load(sys.stdin);print("q=%s run=%s idle=%ss"%(d["queued"],d["running"],int(d["driver_idle_s"])))' 2>/dev/null)
  OK=$(curl -s -m5 "http://127.0.0.1:8790/debug/jobs?n=400" 2>/dev/null | .venv/bin/python -c '
import json,sys
e=json.load(sys.stdin)["events"]
print("done=%d err=%d"%(sum(1 for x in e if x.get("event")=="result" and x.get("status")=="ok"),
                        sum(1 for x in e if x.get("event")=="result" and x.get("status")=="error")))' 2>/dev/null)
  echo "[$(date +%H:%M:%S)] $Q $OK built=$(grep -c 'prompt OK' batch_rocka.log 2>/dev/null)/42" >> batch_watch.log
  sleep 120
done
