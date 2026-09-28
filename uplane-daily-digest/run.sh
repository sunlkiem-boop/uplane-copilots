#!/usr/bin/env bash
# Build the daily digest. No arguments = dry run (prints, posts nothing).
#   ./run.sh --post        post it to DIGEST_CHANNEL
#   ./run.sh --health      what's wired up, what isn't
#   ./run.sh --bot         run the conversational Slack listener (long-running)
# Pinned to its own Python 3.12 venv so it never touches the Anaconda base env.
set -euo pipefail
cd "$(dirname "$0")/helper"

VENV=".venv"

# Resolve uv by absolute path: scheduled and SwiftBar launches run under a
# minimal PATH with no ~/.local/bin, where a bare `uv` is "command not found".
UV="$(command -v uv 2>/dev/null || true)"
if [ -z "$UV" ]; then
  for c in "$HOME/.local/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv "$HOME/.cargo/bin/uv"; do
    [ -x "$c" ] && { UV="$c"; break; }
  done
fi

if [ ! -x "$VENV/bin/python" ]; then
  if [ -z "$UV" ]; then
    echo "❌ Need uv to create the venv and it isn't installed (looked on PATH," >&2
    echo "   ~/.local/bin, /opt/homebrew/bin, /usr/local/bin, ~/.cargo/bin)." >&2
    exit 1
  fi
  echo "First run: creating an isolated Python 3.12 environment…"
  "$UV" venv --python 3.12 "$VENV"
fi

# Dependency sync must never be the thing that stops a digest going out.
if [ -n "$UV" ]; then
  "$UV" pip install --python "$VENV/bin/python" --quiet -r requirements.txt
elif "$VENV/bin/python" -m pip --version >/dev/null 2>&1; then
  "$VENV/bin/python" -m pip install --quiet -r requirements.txt
else
  echo "⚠️  Skipping dependency sync (no uv, no pip)." >&2
fi

if [ -f .env ]; then set -a; . ./.env; set +a; fi

# Unbuffered, always. The listener's log is redirected to a file, so Python
# block-buffers stdout: startup lines ("Slack OK", "listening") sat unflushed for
# 8KB while only stderr warnings appeared, making a healthy process look broken and
# a stale warning look current.
export PYTHONUNBUFFERED=1

# --bot runs the conversational Socket Mode listener instead of the one-shot digest.
if [ "${1:-}" = "--bot" ]; then
  shift
  exec "$VENV/bin/python" bot.py "$@"
fi

exec "$VENV/bin/python" digest.py "$@"
