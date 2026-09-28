#!/usr/bin/env bash
# Demo Agent runner. Pinned to its own Python 3.12 venv (never Anaconda base).
#   ./run.sh --bot      run the Slack listener (long-running; SwiftBar supervises)
#   ./run.sh --probes   everything that could silently be broken, incl. a real inference
#   ./run.sh --health   same, without spending tokens
set -euo pipefail
cd "$(dirname "$0")/helper"
VENV=".venv"
UV="$(command -v uv 2>/dev/null || true)"
if [ -z "$UV" ]; then
  for c in "$HOME/.local/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv "$HOME/.cargo/bin/uv"; do
    [ -x "$c" ] && { UV="$c"; break; }
  done
fi
if [ ! -x "$VENV/bin/python" ]; then
  [ -z "$UV" ] && { echo "❌ need uv to create the venv (PATH, ~/.local/bin, /opt/homebrew/bin…)" >&2; exit 1; }
  "$UV" venv --python 3.12 "$VENV"
  "$UV" pip install --python "$VENV/bin/python" -r requirements.txt
fi
[ -f .env ] && { set -a; . ./.env; set +a; }
case "${1:-}" in
  --bot)    exec "$VENV/bin/python" bot.py ;;
  --probes) exec "$VENV/bin/python" -c "import probes;print(probes.render(probes.run_probes(deep=True)))" ;;
  --health) exec "$VENV/bin/python" -c "import probes;print(probes.render(probes.run_probes(deep=False)))" ;;
  *) echo "usage: ./run.sh --bot | --probes | --health"; exit 1 ;;
esac
