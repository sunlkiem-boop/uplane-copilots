#!/usr/bin/env bash
# Start the Uplane Slack Ad-Bot helper (Slack Socket Mode + extension bridge on
# http://127.0.0.1:8790). Pins to its OWN Python 3.12 venv so it never touches
# the Anaconda base env. Re-runnable; venv created once and reused.
set -euo pipefail
cd "$(dirname "$0")"

VENV=".venv"

# Resolve uv by absolute path. Launched from the SwiftBar Control Room this runs
# under `/bin/bash -lc`, which reads BASH's profile — not ~/.zshrc — so a zsh
# user's ~/.local/bin is absent and `uv` was "command not found": Start silently
# did nothing but append one line to control-room/logs/slack-ad-bot.log.
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

# Dependency sync must never be what stops the bot coming back up: the venv
# already exists at this point, so degrade to its own pip, then to a warning.
if [ -n "$UV" ]; then
  "$UV" pip install --python "$VENV/bin/python" --quiet -r requirements.txt
elif "$VENV/bin/python" -m pip --version >/dev/null 2>&1; then
  echo "uv not found — syncing deps with the venv's pip instead."
  "$VENV/bin/python" -m pip install --quiet -r requirements.txt
else
  echo "⚠️  Skipping dependency sync (no uv, no pip). Starting with what's installed."
fi

# Load .env if present (SLACK_BOT_TOKEN, SLACK_APP_TOKEN, …)
if [ -f .env ]; then
  set -a; . ./.env; set +a
fi

echo "Starting Uplane Slack Ad-Bot on http://127.0.0.1:${ADBOT_PORT:-8790}  (leave this window open)"
exec "$VENV/bin/python" app.py
