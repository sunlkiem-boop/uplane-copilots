"""Startup probes (spec §10.3): every reason the bot could silently not work,
checked at boot and printable on demand. Each line is PASS/FAIL + the fix."""
from __future__ import annotations

import json
import os
import subprocess

import config
import runner


def run_probes(deep: bool = False) -> list[tuple[bool, str]]:
    out: list[tuple[bool, str]] = []
    out.append((bool(config.SLACK_BOT_TOKEN), "SLACK_BOT_TOKEN in helper/.env"))
    out.append((bool(config.SLACK_APP_TOKEN), "SLACK_APP_TOKEN (xapp-, Socket Mode) in helper/.env"))
    out.append((bool(config.SLACK_USER_TOKEN),
                "SLACK_USER_TOKEN (optional — stage-0 Slack sweeps degrade without it)"))
    out.append((os.path.isdir(config.CLAUDE_CONFIG_DIR),
                f"login dir {config.CLAUDE_CONFIG_DIR} exists (run ./auth.sh)"))
    skills_dir = os.path.join(config.CLAUDE_CONFIG_DIR, "skills")
    missing = [s for s in config.SKILLS
               if not os.path.exists(os.path.join(skills_dir, s))]
    out.append((not missing,
                "pipeline skills linked into the login"
                + (f" — missing: {', '.join(missing)} (run ./auth.sh --skills)" if missing else "")))
    out.append((config.UPLANE.is_dir(), f"uplane workspace at {config.UPLANE}"))
    out.append((config.CANON_CHECK.exists(), f"canon_check.py at {config.CANON_CHECK}"))
    # Notion MCP registered on THAT login (auth needs a browser; presence is checkable)
    try:
        env = dict(os.environ, CLAUDE_CONFIG_DIR=config.CLAUDE_CONFIG_DIR)
        r = subprocess.run([runner.cli(), "mcp", "list"], env=env,
                           capture_output=True, text=True, timeout=30)
        out.append(("notion" in (r.stdout or "").lower(),
                    "Notion MCP registered on the bot's login (./auth.sh --notion; "
                    "stage 0 fails without it)"))
    except Exception as e:
        out.append((False, f"Notion MCP check errored: {e}"))
    if deep:
        try:
            got = runner.run_claude("reply with exactly: demo-agent-ok",
                                    config.CHAT_MODEL, timeout=120)
            out.append(("demo-agent-ok" in got, "login can INFER (real call)"))
        except Exception as e:
            out.append((False, f"login inference: {e}"))
    return out


def render(probes: list[tuple[bool, str]]) -> str:
    return "\n".join(("✅ " if ok else "❌ ") + msg for ok, msg in probes)
