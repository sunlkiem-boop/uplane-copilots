#!/usr/bin/env bash
# <bitbar.title>Daily Digest</bitbar.title>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
#
# Two jobs, one plugin:
#   1. keep the conversational listener (bot.py, Socket Mode) alive
#   2. post the digest once per weekday morning at TARGET
#
# Why SwiftBar and not launchd (2026-08-10): launchd agents cannot read ~/Desktop —
# TCC denies it at spawn. Not a guess; ./schedule.sh installed a real agent and its
# probe came back `Operation not permitted` on helper/digest.py. The only launchd fix
# is granting /bin/zsh Full Disk Access, which hands that to every shell script on
# the machine to fix one job. SwiftBar runs in the GUI session WITH Desktop access
# and already supervises the Ad-Bot helper this way.
#
# 1-minute cadence is for the listener, not the schedule: a chat bot that's down for
# 10 minutes feels broken. The morning post is guarded by a date marker, so ticking
# every minute cannot double-post.

TARGET="0800"
ROOT="$HOME/Desktop/copilots/uplane-daily-digest"
STATE="$HOME/Library/Logs/uplane-daily-digest"
MARKER="$STATE/last-posted"
RUNLOG="$STATE/swiftbar-run.log"
BOTLOG="$STATE/bot.log"
mkdir -p "$STATE"

TODAY="$(date +%F)"
NOW="$(date +%H%M)"
DOW="$(date +%u)"                       # 1=Mon .. 7=Sun
LAST="$(cat "$MARKER" 2>/dev/null || echo none)"

have_tokens() {
  [ -f "$ROOT/helper/.env" ] || return 1
  grep -q '^SLACK_BOT_TOKEN=xox' "$ROOT/helper/.env" || return 1
  grep -q '^SLACK_APP_TOKEN=xapp' "$ROOT/helper/.env" || return 1
  grep -q '^DIGEST_CHANNEL=..*'   "$ROOT/helper/.env" || return 1
}

# Read the PID the listener recorded, and verify it's alive AND actually our bot.
#
# This used to be `pgrep -f "uplane-daily-digest/helper/.venv/bin/python bot.py"`,
# which NEVER matched: run.sh `exec`s from inside helper/, so the process argv is the
# RELATIVE ".venv/bin/python bot.py". The check therefore reported "down" every tick
# and spawned a fresh listener each minute — 29 of them before it was caught, each
# answering every DM. bot.py now also holds an exclusive flock, so even a wrong check
# here cannot produce a second listener.
bot_pid() {
  local p
  p="$(cat "$ROOT/data/bot.pid" 2>/dev/null)" || return 1
  [ -n "$p" ] || return 1
  kill -0 "$p" 2>/dev/null || return 1
  ps -o command= -p "$p" 2>/dev/null | grep -q "bot\.py" || return 1
  echo "$p"
}

spawn() {   # spawn <args...> <logfile>
  local log="$1"; shift
  /usr/bin/python3 - "$ROOT" "$log" "$@" <<'PY'
import subprocess, sys
root, log = sys.argv[1], sys.argv[2]
args = " ".join(sys.argv[3:])
fh = open(log, "a")
subprocess.Popen(["/bin/bash", "-lc", f"./run.sh {args}"], cwd=root,
                 stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
PY
}

# ---------- 1. listener ----------
BOT_STATE=""
if ! have_tokens; then
  BOT_STATE="not configured — fill helper/.env (see slack-app-manifest.yaml)"
  ICON=":exclamationmark.triangle:"
elif [ -n "$(bot_pid)" ]; then
  BOT_STATE="listening (pid $(bot_pid))"
  ICON=":sun.horizon:"
else
  spawn "$BOTLOG" --bot
  BOT_STATE="was down — restarted just now"
  ICON=":bolt.trianglebadge.exclamationmark:"
fi

# ---------- 2. the morning post ----------
POST_STATE=""
if ! have_tokens; then
  POST_STATE="blocked — no tokens"
elif [ "$LAST" = "$TODAY" ]; then
  POST_STATE="sent today"
elif [ "$DOW" -ge 6 ]; then
  POST_STATE="weekend — next Monday $TARGET"
elif [ "$NOW" \< "$TARGET" ]; then
  POST_STATE="waiting for $TARGET"
else
  echo "$TODAY" > "$MARKER"          # BEFORE the run: a synthesis takes minutes and
  spawn "$RUNLOG" --post             # many ticks overlap it. Double-post > missed post.
  POST_STATE="sending now…"
  ICON=":sun.max.fill:"
fi

LATEST="$(ls -1t "$ROOT/data/digests"/*.md 2>/dev/null | head -1)"

echo "$ICON"
echo "---"
echo "Chat: $BOT_STATE"
echo "Digest: $POST_STATE  (weekdays $TARGET, last: $LAST)"
[ -n "$LATEST" ] && echo "Latest: $(basename "$LATEST") | href=file://$LATEST"
echo "---"
echo "Post digest now | shell=/bin/bash param1=-lc param2=\"cd '$ROOT' && ./run.sh --post\" terminal=true refresh=true"
echo "Dry run (posts nothing) | shell=/bin/bash param1=-lc param2=\"cd '$ROOT' && ./run.sh\" terminal=true"
echo "Check wiring (--health) | shell=/bin/bash param1=-lc param2=\"cd '$ROOT' && ./run.sh --health\" terminal=true"
echo "Restart chat listener | shell=/bin/bash param1=-lc param2=\"kill \$(cat '$ROOT/data/bot.pid' 2>/dev/null) 2>/dev/null; true\" terminal=false refresh=true"
echo "---"
echo "Open bot log | shell=/usr/bin/open param1=\"$BOTLOG\" terminal=false"
echo "Open digest log | shell=/usr/bin/open param1=\"$RUNLOG\" terminal=false"
echo "Force re-send today | shell=/bin/bash param1=-lc param2=\"rm -f '$MARKER'\" terminal=false refresh=true"
