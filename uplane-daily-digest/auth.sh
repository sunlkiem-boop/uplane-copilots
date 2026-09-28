#!/usr/bin/env bash
# The digest's own Claude login + its Notion MCP connection.
#
#   ./auth.sh            sign the bot's Claude login in
#   ./auth.sh --notion   connect the Notion MCP server to that login
#   ./auth.sh --status    which account is it on, and is Notion connected?
#   ./auth.sh --logout   sign the bot out (your own login is untouched)
#
# WHY THIS IS MANDATORY, not a nicety: setting CLAUDE_CONFIG_DIR at all detaches
# the CLI from your ambient login — measured on this machine, even setting it to
# ~/.claude verbatim reports loggedIn:false, because the credentials live in a
# Keychain entry that is only consulted while the variable is UNSET. So the
# isolation is absolute (the bot can never ride your login, and re-authing one
# cannot log the other out) and until this runs, every digest fails at build.
set -euo pipefail
cd "$(dirname "$0")"

[ -f helper/.env ] && { set -a; . ./helper/.env; set +a; }
DIR="${DIGEST_CLAUDE_CONFIG_DIR:-$HOME/.claude-digest}"
DIR="${DIR/#\~/$HOME}"
export CLAUDE_CONFIG_DIR="$DIR"
mkdir -p "$DIR"

CLAUDE="$(command -v claude 2>/dev/null || true)"
[ -z "$CLAUDE" ] && for c in "$HOME/.local/bin/claude" /opt/homebrew/bin/claude \
  /usr/local/bin/claude; do [ -x "$c" ] && { CLAUDE="$c"; break; }; done
[ -z "$CLAUDE" ] && { echo "❌ can't find the \`claude\` binary" >&2; exit 1; }

NOTION_URL="${NOTION_MCP_URL:-https://mcp.notion.com/mcp}"

case "${1:-}" in
  --status)
    echo "config dir : $DIR"
    "$CLAUDE" auth status || true
    echo
    echo "MCP servers:"
    "$CLAUDE" mcp list || true
    echo
    echo "If Notion is listed but unauthenticated, run \`CLAUDE_CONFIG_DIR=$DIR claude\`"
    echo "then \`/mcp\` and authenticate Notion in the browser."
    ;;
  --logout)
    "$CLAUDE" auth logout || true
    echo "✅ the digest bot is signed out. Your own ~/.claude login is untouched."
    ;;
  --notion)
    echo "Registering the Notion MCP server for $DIR …"
    "$CLAUDE" mcp remove notion >/dev/null 2>&1 || true
    "$CLAUDE" mcp add --transport http notion "$NOTION_URL"
    echo
    echo "Now authenticate it (opens a browser):"
    echo "  CLAUDE_CONFIG_DIR=$DIR claude"
    echo "  then type:  /mcp    → select notion → Authenticate"
    echo
    echo "Verify with: ./auth.sh --status   (notion should show as connected)"
    ;;
  *)
    echo "Signing in the digest bot's OWN Claude account (config dir: $DIR)."
    echo "Use whichever account should carry this bot's usage."
    "$CLAUDE" auth login
    echo
    echo "Next: ./auth.sh --notion   (connect Notion so it can read the meeting notes)"
    ;;
esac
