#!/usr/bin/env bash
# Start Uplane Atlas (:8792). Stdlib-only — no venv needed.
# --reset restores the seeded map: current doc is backed up to atlas.json.pre-reset.
set -euo pipefail
cd "$(dirname "$0")"
DOC="$HOME/Desktop/uplane/architecture/atlas.json"
if [ "${1:-}" = "--reset" ]; then
  [ -f "$DOC" ] && cp "$DOC" "$DOC.pre-reset"
  python3 - <<'PY'
import json, os
seed = json.load(open(os.path.join(os.path.dirname(os.path.abspath("run.sh")), "..", "seed", "atlas.json")))
doc = os.path.expanduser("~/Desktop/uplane/architecture/atlas.json")
try: seed["version"] = json.load(open(doc)).get("version", 0) + 1
except Exception: seed["version"] = 1
json.dump(seed, open(doc, "w"), indent=1)
print("map reset to seed as version", seed["version"])
PY
  exit 0
fi
exec python3 app.py
