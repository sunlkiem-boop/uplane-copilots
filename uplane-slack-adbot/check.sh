#!/bin/bash
# One command to answer "is the bot sane?" — no Slack, no Chrome, no credits.
#   ./check.sh          run the eval suite
#   ./check.sh -v       show every passing check
#   ./check.sh --mutate also prove the suite catches seeded regressions
cd "$(dirname "$0")" || exit 1
if [ "${1:-}" = "--mutate" ]; then exec ./evals/mutate.sh; fi
exec helper/.venv/bin/python helper/selftest.py "$@"
