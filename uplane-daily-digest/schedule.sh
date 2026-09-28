#!/usr/bin/env bash
# Install / remove the weekday morning digest.
#
#   ./schedule.sh install [HH:MM]   default 08:00, Mon-Fri
#   ./schedule.sh status
#   ./schedule.sh uninstall
#   ./schedule.sh test              run it through launchd right now
#
# WHY THIS VERIFIES ITSELF: this repo lives under ~/Desktop, which macOS protects
# with TCC. A launchd agent reading it gets "Operation not permitted" and, because
# launchd swallows the failure, the job looks installed and simply never produces a
# digest. That exact failure has already cost time in this copilot family, so
# `install` fires the job once through launchd and checks that a digest file
# actually appeared — and if it didn't, prints the Full Disk Access fix instead of
# reporting success.
set -euo pipefail
cd "$(dirname "$0")"

ROOT="$(pwd)"
LABEL="com.uplane.daily-digest"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
# Logs deliberately live OUTSIDE ~/Desktop. If TCC blocks launchd from this folder,
# a log path inside it cannot be written either — so the one file that would tell
# you what went wrong is the one file you don't get, and every failure looks like
# silence. ~/Library/Logs is always writable by the user's launchd session.
LOGDIR="$HOME/Library/Logs/uplane-daily-digest"
mkdir -p "$LOGDIR"

case "${1:-install}" in
  install)
    TIME="${2:-08:00}"
    HH="${TIME%%:*}"; MM="${TIME##*:}"
    HH=$((10#$HH)); MM=$((10#$MM))

    mkdir -p "$HOME/Library/LaunchAgents"
    # Weekday 1..5 = Mon..Fri. A login shell (-lc) so PATH picks up ~/.local/bin
    # for uv and claude; run.sh also resolves both by absolute path as a backstop.
    {
      echo '<?xml version="1.0" encoding="UTF-8"?>'
      echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
      echo '<plist version="1.0"><dict>'
      echo "  <key>Label</key><string>$LABEL</string>"
      echo '  <key>ProgramArguments</key><array>'
      echo '    <string>/bin/zsh</string><string>-lc</string>'
      echo "    <string>cd '$ROOT' && ./run.sh --post</string>"
      echo '  </array>'
      echo '  <key>StartCalendarInterval</key><array>'
      for d in 1 2 3 4 5; do
        echo "    <dict><key>Weekday</key><integer>$d</integer>"
        echo "         <key>Hour</key><integer>$HH</integer>"
        echo "         <key>Minute</key><integer>$MM</integer></dict>"
      done
      echo '  </array>'
      echo "  <key>StandardOutPath</key><string>$LOGDIR/digest.log</string>"
      echo "  <key>StandardErrorPath</key><string>$LOGDIR/digest.err</string>"
      echo '  <key>RunAtLoad</key><false/>'
      echo '</dict></plist>'
    } > "$PLIST"

    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    printf 'Installed %s — weekdays at %02d:%02d.\n' "$LABEL" "$HH" "$MM"

    echo
    echo "Verifying launchd can actually reach this folder (TCC check)…"
    # A throwaway probe agent rather than kickstarting the real job: the question
    # is only "can a launchd-spawned process read this directory", and answering it
    # with the real job would burn a flagship synthesis call and post a duplicate
    # digest to Slack every time the schedule is installed.
    PLABEL="$LABEL.probe"
    PPLIST="$HOME/Library/LaunchAgents/$PLABEL.plist"
    POUT="$LOGDIR/tcc-probe.log"
    : > "$POUT"
    {
      echo '<?xml version="1.0" encoding="UTF-8"?>'
      echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
      echo '<plist version="1.0"><dict>'
      echo "  <key>Label</key><string>$PLABEL</string>"
      echo '  <key>ProgramArguments</key><array>'
      echo '    <string>/bin/zsh</string><string>-lc</string>'
      echo "    <string>cat '$ROOT/helper/digest.py' >/dev/null && echo TCC_OK || echo TCC_BLOCKED</string>"
      echo '  </array>'
      echo "  <key>StandardOutPath</key><string>$POUT</string>"
      echo "  <key>StandardErrorPath</key><string>$POUT</string>"
      echo '  <key>RunAtLoad</key><true/>'
      echo '</dict></plist>'
    } > "$PPLIST"
    launchctl bootout "gui/$(id -u)/$PLABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PPLIST" 2>/dev/null || true
    for _ in $(seq 1 25); do
      grep -q "TCC_OK\|TCC_BLOCKED\|not permitted\|Permission denied" "$POUT" 2>/dev/null && break
      sleep 1
    done
    launchctl print "gui/$(id -u)/$PLABEL" >/dev/null 2>&1 || true
    PROBE="$(cat "$POUT" 2>/dev/null || true)"
    launchctl bootout "gui/$(id -u)/$PLABEL" 2>/dev/null || true
    rm -f "$PPLIST"

    if printf '%s' "$PROBE" | grep -q "TCC_OK"; then
      echo "✅ Verified: a launchd-spawned process can read this folder."
      echo "   First real run: next weekday at $(printf '%02d:%02d' "$HH" "$MM")."
      echo "   To prove the whole job end to end now: ./schedule.sh test"
    elif printf '%s' "$PROBE" | grep -qi "TCC_BLOCKED\|not permitted\|Permission denied"; then
      echo
      echo "❌ TCC blocked it — the schedule is installed but will never produce anything."
      echo "   Fix: System Settings → Privacy & Security → Full Disk Access → + →"
      echo "        press Cmd-Shift-G, enter /bin/zsh, add it, and toggle it ON."
      echo "   Then re-run: ./schedule.sh install $(printf '%02d:%02d' "$HH" "$MM")"
      exit 1
    else
      echo "⚠️  Probe was inconclusive (no output). Verify by hand: ./schedule.sh test"
    fi
    ;;

  status)
    if [ -f "$PLIST" ]; then
      echo "plist: $PLIST"
      launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null \
        | grep -E "state|last exit|program" || echo "(not loaded)"
    else
      echo "not installed"
    fi
    echo
    echo "last digests:"; ls -1t data/digests 2>/dev/null | head -5 || echo "  (none)"
    echo "last error output:"; tail -5 "$LOGDIR/digest.err" 2>/dev/null || echo "  (none)"
    ;;

  test)
    echo "Running the job through launchd (same path the schedule uses)…"
    launchctl kickstart -k "gui/$(id -u)/$LABEL"
    sleep 10
    echo "--- digest.err ---"; tail -20 "$LOGDIR/digest.err" 2>/dev/null || true
    echo "--- digest.log ---"; tail -20 "$LOGDIR/digest.log" 2>/dev/null || true
    ;;

  uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "✅ removed $LABEL"
    ;;

  *)
    echo "usage: ./schedule.sh {install [HH:MM]|status|test|uninstall}" >&2
    exit 2
    ;;
esac
