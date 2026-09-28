#!/usr/bin/env bash
# launchd entrypoint for the Ad-Bot helper.
#
# WHY THIS EXISTS: the helper used to be started with `nohup ./run.sh &` from
# ephemeral tool-call shells. When such a shell hit its timeout it was SIGTERM'd
# as a process GROUP — taking the helper down with it (nohup only blocks SIGHUP).
# That was the recurring "helper mysteriously died" failure all day on 2026-07-24.
# Under launchd the helper's lifecycle belongs to macOS: KeepAlive restarts it on
# any crash, and no debugging session can reap it by accident.
#
# Differences from run.sh (interactive): no `uv pip install` step — launchd's PATH
# may not have uv, and set -e would turn that into a crash loop. The venv already
# exists; deps are managed by running ./run.sh manually when requirements change.
set -u
cd "$(dirname "$0")"

if [ -f .env ]; then
  set -a; . ./.env; set +a
fi

exec ".venv/bin/python" app.py
