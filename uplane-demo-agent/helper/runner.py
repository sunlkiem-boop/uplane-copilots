"""Run a pipeline step in the bot's OWN Claude login, and write the TRACE.

Same hard-won CLI handling as the digest's llm.py: never trust PATH (SwiftBar
launches get a minimal one), name auth failures as auth failures, and keep the
API key out of the child env so nothing silently bills the wrong account.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import config

_CLI_CANDIDATES = ("~/.local/bin/claude", "/opt/homebrew/bin/claude",
                   "/usr/local/bin/claude", "~/.bun/bin/claude")


def cli() -> str:
    forced = os.environ.get("CLAUDE_CLI_PATH", "").strip()
    if forced and os.access(os.path.expanduser(forced), os.X_OK):
        return os.path.expanduser(forced)
    found = shutil.which("claude")
    if found:
        return found
    for c in _CLI_CANDIDATES:
        p = os.path.expanduser(c)
        if os.access(p, os.X_OK):
            return p
    raise SetupError("can't find the `claude` binary",
                     "set CLAUDE_CLI_PATH=/full/path/to/claude in helper/.env")


class SetupError(RuntimeError):
    def __init__(self, detail: str, fix: str):
        super().__init__(f"{detail} — {fix}")
        self.detail, self.fix = detail, fix


_AUTH_MARKERS = ("not logged in", "please log in", "does not have access",
                 "credit balance", "login again")


def run_claude(prompt: str, model: str, cwd: str | None = None,
               timeout: int = 3600) -> str:
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env["CLAUDE_CONFIG_DIR"] = config.CLAUDE_CONFIG_DIR
    r = subprocess.run(
        [cli(), "-p", prompt, "--model", model, "--dangerously-skip-permissions"],
        cwd=cwd or str(config.UPLANE), env=env,
        capture_output=True, text=True, timeout=timeout)
    out = (r.stdout or "").strip()
    low = (out + (r.stderr or "")).lower()
    if r.returncode != 0 or any(m in low for m in _AUTH_MARKERS):
        raise SetupError(
            f"claude run failed (exit {r.returncode}): {(r.stderr or out)[:300]}",
            "check ./auth.sh --status — the bot has its own login and it must be able to infer")
    return out


def write_trace(client: str, stage: int | str, prompt: str, result_head: str) -> Path:
    """Every run leaves a TRACE file — what was asked, when, what came back."""
    # unique even within one second — a revise rerun in the same second used to
    # OVERWRITE the original run's trace, silently losing the record it exists to keep
    ts = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time()*1000)%1000:03d}"
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in client)
    p = config.TRACE_DIR / f"{ts}-{safe}-stage{stage}.md"
    p.write_text(
        f"# TRACE — {client} · stage {stage} · {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"## prompt\n\n```\n{prompt[:4000]}\n```\n\n"
        f"## result (head)\n\n{result_head[:4000]}\n")
    return p
