#!/usr/bin/env bash
# Save your Claude credential so the background helper can authenticate.
# Paste EITHER a token from `claude setup-token` (sk-ant-oat...) OR an
# Anthropic API key (sk-ant-api...). It picks the right setting automatically.
set -euo pipefail
cd "$(dirname "$0")"

echo "Paste your credential and press Enter:"
echo "  • from  claude setup-token   → starts with  sk-ant-oat..."
echo "  • or an Anthropic API key    → starts with  sk-ant-api..."
echo
printf "Credential: "
read -r CRED
# trim leading/trailing whitespace only (tokens have no internal spaces)
CRED="$(printf '%s' "$CRED" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"

if [ -z "$CRED" ]; then
  echo "Nothing entered — aborted."; exit 1
fi

case "$CRED" in
  sk-ant-oat*) KEY=CLAUDE_CODE_OAUTH_TOKEN ;;
  sk-ant-api*) KEY=ANTHROPIC_API_KEY ;;
  sk-ant-*)    KEY=ANTHROPIC_API_KEY ;;
  *) echo "That doesn't look like a Claude credential (should start with sk-ant-...). Nothing saved."; exit 1 ;;
esac

printf '%s=%s\n' "$KEY" "$CRED" > .env
echo
echo "✓ Saved $KEY to $(pwd)/.env"
echo "Next:  ./run.sh"
