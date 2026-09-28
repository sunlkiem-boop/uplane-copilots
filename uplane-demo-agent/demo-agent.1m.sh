#!/usr/bin/env bash
# <bitbar.title>Demo Agent</bitbar.title>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
#
# Keeps the Demo Agent's Socket Mode listener alive. SwiftBar, not launchd, for the
# same measured reason as the Digest: launchd agents cannot read ~/Desktop (TCC
# denies at spawn); SwiftBar runs in the GUI session with Desktop access.
ROOT="$HOME/Desktop/copilots/uplane-demo-agent"
STATE="$HOME/Library/Logs/uplane-demo-agent"
BOTLOG="$STATE/bot.log"
PIDFILE="$STATE/bot.pid"
mkdir -p "$STATE"

have_tokens() {
  [ -f "$ROOT/helper/.env" ] || return 1
  grep -q "^SLACK_APP_TOKEN=xapp-" "$ROOT/helper/.env" 2>/dev/null || return 1
  grep -q "^SLACK_BOT_TOKEN=xoxb-" "$ROOT/helper/.env" 2>/dev/null
}

alive() {
  local pid; pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

if have_tokens && ! alive; then
  nohup "$ROOT/run.sh" --bot >> "$BOTLOG" 2>&1 &
  echo $! > "$PIDFILE"
  sleep 1
fi

if ! have_tokens; then
  echo "🤖 ⚠︎"
  echo "---"
  echo "Demo Agent — tokens missing | color=orange"
  echo "Finish SETUP.md (Slack app + .env) | href=file://$ROOT/SETUP.md"
elif alive; then
  echo "🤖"
  echo "---"
  echo "Demo Agent — listener up (pid $(cat "$PIDFILE"))"
  echo "Open log | bash=/usr/bin/open param1=$BOTLOG terminal=false"
  echo "Restart | bash=/bin/bash param1=-c param2=\"kill $(cat "$PIDFILE") 2>/dev/null\" terminal=false refresh=true"
else
  echo "🤖 ✗"
  echo "---"
  echo "Demo Agent — starting… | color=orange"
  echo "Open log | bash=/usr/bin/open param1=$BOTLOG terminal=false"
fi
