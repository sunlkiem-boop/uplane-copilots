#!/usr/bin/env bash
# Log the Ad-Bot into its OWN Claude account.
#
# The bot's prompt-building shells out to `claude`. By default that borrows the
# login in ~/.claude — your personal one — so the bot competes with your own
# Claude Code usage for quota, and switching accounts in Claude Code silently
# reroutes every prompt it builds. ADBOT_CLAUDE_CONFIG_DIR (helper/.env) gives
# it a login of its own; this script signs that login in and reports it back.
#
# Measured: SETTING CLAUDE_CONFIG_DIR at all detaches the CLI from the ambient
# login — even setting it to ~/.claude verbatim reports loggedIn:false, because
# the "Claude Code-credentials" Keychain entry is only consulted while the
# variable is unset. So the split is absolute, and this script is a required
# setup step, not a nicety: until it runs, the bot cannot build prompts.
#
#   ./auth.sh            log in / re-log in the bot's account
#   ./auth.sh --status   show which account the bot is on, and whether it works
#   ./auth.sh --logout   sign the bot's account out (leaves yours alone)
set -euo pipefail
cd "$(dirname "$0")"

# An explicitly exported value wins over helper/.env — `set -a; . .env` would
# otherwise clobber it, so pointing this at another dir to inspect it silently
# did nothing.
_PRESET="${ADBOT_CLAUDE_CONFIG_DIR:-}"
[ -f helper/.env ] && { set -a; . ./helper/.env; set +a; }
[ -n "$_PRESET" ] && ADBOT_CLAUDE_CONFIG_DIR="$_PRESET"

DIR="${ADBOT_CLAUDE_CONFIG_DIR:-$HOME/.claude-adbot}"
DIR="${DIR/#\~/$HOME}"
CLI="${CLAUDE_CLI_PATH:-$(command -v claude || true)}"

if [ -z "$CLI" ]; then
  echo "❌ No \`claude\` CLI on PATH. Install it, then re-run ./auth.sh"
  exit 1
fi

# `claude auth status` emits JSON and honours CLAUDE_CONFIG_DIR, so it reports
# the BOT's login, not yours. loggedIn is real ground truth; a .claude.json on
# disk only proves a login happened at some point.
show() {
  local js
  js="$(CLAUDE_CONFIG_DIR="$DIR" "$CLI" auth status 2>/dev/null || true)"
  ADBOT_AUTH_JSON="$js" python3 - <<'PY'
import json, os, sys
try:
    s = json.loads(os.environ.get("ADBOT_AUTH_JSON") or "{}") or {}
except Exception:
    s = {}
if s.get("loggedIn"):
    email = s.get("email") or "?"
    plan = s.get("subscriptionType") or s.get("authMethod") or "?"
    print(f"   account: {email}   plan: {plan}")
    sys.exit(0)
print("   account: (not logged in)")
sys.exit(1)
PY
}

echo "Ad-Bot Claude login"
echo "   config dir: $DIR"

# A login existing is NOT proof it can run inference: observed live, a login
# reporting loggedIn:true on an active max plan answered "Your organization does
# not have access to Claude" on every request. So actually call both models the
# bot uses (a few tokens) rather than trusting the status flag.
verify() {
  local py="helper/.venv/bin/python"
  [ -x "$py" ] || py="python3"
  ( cd helper && ADBOT_CLAUDE_CONFIG_DIR="$DIR" "../$py" - <<'PY'
import json, sys
try:
    import llm
except Exception as e:
    print(f"   ⚠️  couldn't import the helper ({e}) — skipping the inference check")
    sys.exit(0)
r = llm.verify()
for m in r["models"]:
    print(f"   {'✅' if m['ok'] else '❌'} {m['model']}"
          + ("" if m["ok"] else f" — {m.get('error','')[:120]}"))
if not r["ok"]:
    print("   " + r.get("fix", "The bot cannot run inference with this login."))
    sys.exit(1)
PY
  )
}

case "${1:-}" in
  --status)
    show || { echo "   → run ./auth.sh to sign it in"; exit 1; }
    echo "   checking that it can actually run inference…"
    verify || exit 1
    echo "   ✅ the bot has its own working login"
    exit 0
    ;;
  --logout)
    CLAUDE_CONFIG_DIR="$DIR" "$CLI" auth logout || true
    echo "   signed out (your own ~/.claude login is untouched)"
    exit 0
    ;;
esac

mkdir -p "$DIR"
echo
echo "A browser sign-in will open. Sign in with the account you want the BOT to"
echo "use — this does not touch your own Claude Code login."
echo
# --claudeai = subscription billing (the default; stated so intent is on record).
CLAUDE_CONFIG_DIR="$DIR" "$CLI" auth login --claudeai

echo
if ! show; then
  echo "⚠️  Still not logged in — re-run ./auth.sh"
  exit 1
fi
echo "   checking that it can actually run inference…"
if ! verify; then
  echo
  echo "⚠️  Signed in, but this account cannot run the bot's models yet."
  echo "    Subscription login requires an active Max or Pro seat ON THIS ACCOUNT."
  echo "    'Your organization does not have access to Claude' means the seat is"
  echo "    missing or lapsed — check https://claude.ai/settings/billing. A local"
  echo "    'subscriptionType: max' proves nothing; it is a cached value."
  echo "    Alternative: put ANTHROPIC_API_KEY in helper/.env and comment out"
  echo "    ADBOT_CLAUDE_CONFIG_DIR to bill per-token instead."
  echo "    The bot fails every drop until this line is green."
  exit 1
fi
echo
echo "✅ Done. Restart the helper (helper/run.sh) so it picks this up."
echo "   Re-check any time:  ./auth.sh --status"
echo "                       curl -s 'localhost:8790/health?verify=1'"
