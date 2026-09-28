"""
LLM backend for the daily digest — a trimmed descendant of the Slack Ad-Bot's
llm.py (same hard-won CLI-path and auth handling, no vision path), plus one thing
the Ad-Bot never needed: the ability to hand the CLI a set of **MCP tools**, which
is how the Notion scrape reads the meeting notes.

  text_json(system, instructions, schema)              -> dict
  agent_json(system, instructions, schema, mcp_tools=…) -> dict   (tool-using)
  backend_info()                                       -> dict   (no network)

Backends: CLI (default, billed to a `claude` login) or API (ANTHROPIC_API_KEY).
Note the API backend cannot serve agent_json — it has no MCP connection — so the
Notion scrape is CLI-only by construction.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional


DIGEST_CONFIG_DIR = os.path.expanduser(os.environ.get("DIGEST_CLAUDE_CONFIG_DIR", "").strip())
CLI_TIMEOUT = int(os.environ.get("DIGEST_CLI_TIMEOUT", "900"))

_CLI_CANDIDATES = ("~/.local/bin/claude", "/opt/homebrew/bin/claude",
                   "/usr/local/bin/claude", "~/.cargo/bin/claude",
                   "~/.bun/bin/claude", "/opt/local/bin/claude")


def _resolve_cli() -> str:
    """Resolve `claude` WITHOUT trusting PATH: scheduled/SwiftBar launches run
    with a minimal /usr/bin:/bin PATH that has no ~/.local/bin, and the bare
    string then dies as a raw ENOENT with no hint of the real cause."""
    explicit = os.environ.get("CLAUDE_CLI_PATH", "").strip()
    if explicit:
        return os.path.expanduser(explicit)
    found = shutil.which("claude")
    if found:
        return found
    for cand in _CLI_CANDIDATES:
        p = os.path.expanduser(cand)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return "claude"


CLI_PATH = _resolve_cli()


def _backend() -> str:
    forced = os.environ.get("LLM_BACKEND", "").strip().lower()
    if forced in {"cli", "api"}:
        return forced
    if DIGEST_CONFIG_DIR:
        return "cli"          # a dedicated login is an explicit choice of CLI
    return "api" if os.environ.get("ANTHROPIC_API_KEY") else "cli"


class SetupError(RuntimeError):
    """A machine/account problem no retry fixes. `.fix` is the remedy, verbatim."""

    def __init__(self, detail: str, fix: str):
        super().__init__(f"{detail}\n{fix}")
        self.detail = detail
        self.fix = fix


class AuthError(SetupError):
    """Credentials missing, lapsed, or barred from inference."""


class McpError(SetupError):
    """The MCP server the scrape needs is absent or unauthenticated."""


class CliNotFoundError(SetupError):
    """The `claude` binary isn't where we can execute it (usually PATH)."""


# The CLI exits 0 on a lapsed login and reports it only inside the JSON
# envelope, so this has to be a text match rather than a status code.
_AUTH_RE = re.compile(
    r"not logged in|please run /login|invalid api[- ]?key|authentication[ _]error"
    r"|oauth token (?:has )?expired|token (?:has )?expired|revoked|unauthorized"
    r"|does not have access to claude|please log ?in again|login again",
    re.I,
)
_MCP_RE = re.compile(
    r"mcp server .*not (?:found|connected)|no such tool|tool .*not available"
    r"|needs authentication|not authenticated|/mcp",
    re.I,
)


def _cli_missing_fix() -> str:
    looked = ", ".join(["$PATH"] + [os.path.expanduser(c) for c in _CLI_CANDIDATES[:3]])
    return (f"Fix: couldn't find the `claude` binary (looked in {looked}). Scheduled "
            f"and SwiftBar launches get a minimal PATH — set CLAUDE_CLI_PATH=/full/path/to/claude "
            f"in helper/.env (`command -v claude` prints it).")


def _auth_fix() -> str:
    if _backend() == "api":
        return "Fix: ANTHROPIC_API_KEY in helper/.env is missing, wrong, or revoked."
    if DIGEST_CONFIG_DIR:
        state = "has lapsed" if cli_account().get("email") else "is not signed in yet"
        return (f"Fix: the digest bot's own Claude login ({DIGEST_CONFIG_DIR}) {state}. "
                f"Run `./auth.sh` in {ROOT_HINT} and sign in.")
    return ("Fix: the `claude` CLI is not logged in — run `claude auth login`. (Set "
            "DIGEST_CLAUDE_CONFIG_DIR in helper/.env to give the bot its own account.)")


def _mcp_fix() -> str:
    return (f"Fix: the Notion MCP server isn't connected for this login. Run "
            f"`./auth.sh --notion` in {ROOT_HINT}, then `claude /mcp` and authenticate "
            f"Notion in the browser. Until then the digest can only use notes already "
            f"cached under data/notes/.")


ROOT_HINT = str(Path(__file__).resolve().parent.parent)


def _cli_env() -> dict:
    """Env for the subprocess. ANTHROPIC_API_KEY is stripped deliberately: an
    exported key would override the subscription login we just pointed at and
    silently move billing to a different account."""
    env = dict(os.environ)
    if DIGEST_CONFIG_DIR:
        env["CLAUDE_CONFIG_DIR"] = DIGEST_CONFIG_DIR
    env.pop("ANTHROPIC_API_KEY", None)
    return env


_ACCT_CACHE: tuple = (0.0, None)
_ACCT_TTL = 60.0


def cli_account(force: bool = False) -> dict:
    """Which account the CLI will bill, and whether it is logged in. Never raises."""
    global _ACCT_CACHE
    now = time.time()
    if not force and _ACCT_CACHE[1] is not None and now - _ACCT_CACHE[0] < _ACCT_TTL:
        return _ACCT_CACHE[1]
    acct: dict
    try:
        proc = subprocess.run([CLI_PATH, "auth", "status"], capture_output=True,
                              text=True, timeout=25, env=_cli_env())
        st = json.loads(proc.stdout or "{}")
        acct = {"email": st.get("email") or "",
                "plan": st.get("subscriptionType") or st.get("authMethod") or "",
                "logged_in": bool(st.get("loggedIn"))}
    except Exception:
        root = DIGEST_CONFIG_DIR or os.path.expanduser("~/.claude")
        try:
            with open(Path(root) / ".claude.json", encoding="utf-8") as f:
                raw = (json.load(f) or {}).get("oauthAccount") or {}
            acct = {"email": raw.get("emailAddress") or "",
                    "plan": raw.get("organizationType") or "", "logged_in": None}
        except (OSError, ValueError):
            acct = {"email": "", "plan": "", "logged_in": None}
    _ACCT_CACHE = (now, acct)
    return acct


# `claude mcp list` prints one line per server as
#     [claude.ai ]<name>: <url-or-command> - <status>
# where <name> MAY CONTAIN SPACES ("claude.ai Google Drive: …"). An earlier version
# anchored on `([A-Za-z0-9_.-]+):` and so matched nothing on a real machine, reporting
# "no MCP servers" while eight were configured — a health check that lied.
_MCP_LINE = re.compile(r"^\s*(?:claude\.ai\s+)?(.+?):\s+\S+\s+-\s+(.+?)\s*$")


def mcp_servers() -> list[dict]:
    """MCP servers for the login we'll use, with connection status. Never raises.

    Status matters as much as presence: a server that is registered but reports
    "Needs authentication" cannot answer a tool call, so treating it as available
    would turn a fixable setup gap into a confusing mid-run failure."""
    try:
        proc = subprocess.run([CLI_PATH, "mcp", "list"], capture_output=True,
                              text=True, timeout=120, env=_cli_env())
        out = proc.stdout or ""
        if "No MCP servers configured" in out:
            return []
        servers = []
        for line in out.splitlines():
            m = _MCP_LINE.match(line)
            if not m:
                continue
            name, status = m.group(1).strip(), m.group(2).strip()
            servers.append({"name": name, "status": status,
                            "connected": "connect" in status.lower()
                                         and "needs" not in status.lower()})
        return servers
    except Exception:
        return []


def backend_info() -> dict:
    """Who answers the next call, and can they. No inference, no tokens."""
    b = _backend()
    info: dict = {"backend": b}
    if b == "cli":
        found = bool(shutil.which(CLI_PATH) or Path(CLI_PATH).exists())
        acct = cli_account()
        servers = mcp_servers() if found else []
        notion = next((s for s in servers if "notion" in s["name"].lower()), None)
        info.update(cli_path=CLI_PATH, cli_found=found,
                    config_dir=DIGEST_CONFIG_DIR or "(shared ~/.claude)",
                    dedicated_account=bool(DIGEST_CONFIG_DIR),
                    account=acct.get("email") or "(not logged in)",
                    plan=acct.get("plan") or "",
                    mcp_servers=[s["name"] for s in servers],
                    mcp_connected=[s["name"] for s in servers if s["connected"]],
                    notion_mcp=bool(notion and notion["connected"]),
                    scrape_model=os.environ.get("DIGEST_SCRAPE_MODEL", "claude-sonnet-5"),
                    synth_model=os.environ.get("DIGEST_SYNTH_MODEL", "claude-opus-5"))
        info["ready"] = found and bool(acct.get("email")) and acct.get("logged_in") is not False
        if not info["ready"]:
            info["fix"] = _cli_missing_fix() if not found else _auth_fix()
        elif not info["notion_mcp"]:
            # Not fatal: cached notes still produce a digest. Distinguish the two
            # states, because the fix is different — register it, vs authenticate it.
            if notion:
                info["notion_status"] = notion["status"]
                info["warning"] = (f"Notion MCP is registered but not usable "
                                   f"({notion['status']}).")
                info["fix"] = (f"Fix: run `CLAUDE_CONFIG_DIR={DIGEST_CONFIG_DIR} claude`, "
                               f"type `/mcp`, select notion, choose Authenticate.")
            else:
                info["warning"] = "Notion MCP not registered — live scrape unavailable."
                info["fix"] = _mcp_fix()
    else:
        key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        info.update(api_key_set=key, ready=key, notion_mcp=False)
        if not key:
            info["fix"] = _auth_fix()
    return info


# --------------------------------------------------------------------------- #
# JSON helpers
# --------------------------------------------------------------------------- #
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _clean(text: str) -> str:
    return _CTRL_RE.sub("", text)


def _loads_loose(text: str) -> dict:
    """Parse JSON that may arrive wrapped in prose or ``` fences."""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON object in model output: {text[:400]!r}")
    return json.loads(text[start:end + 1])


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _cli_run(prompt: str, timeout: int, model: Optional[str],
             allowed_tools: Optional[list] = None) -> str:
    cmd = [CLI_PATH, "-p", "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    try:
        proc = subprocess.run(cmd, input=_clean(prompt), capture_output=True,
                              text=True, timeout=timeout, env=_cli_env())
    except (FileNotFoundError, NotADirectoryError, PermissionError) as e:
        raise CliNotFoundError(f"{type(e).__name__}: {e}", _cli_missing_fix()) from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"claude CLI timed out after {timeout}s") from e

    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        if _AUTH_RE.search(detail):
            raise AuthError(detail[:300], _auth_fix())
        if _MCP_RE.search(detail):
            raise McpError(detail[:300], _mcp_fix())
        raise RuntimeError(f"claude CLI failed ({proc.returncode}): {detail[:600]}")
    try:
        env = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return proc.stdout
    if env.get("is_error"):
        detail = str(env.get("result"))
        if _AUTH_RE.search(detail):
            raise AuthError(detail[:300], _auth_fix())
        if _MCP_RE.search(detail):
            raise McpError(detail[:300], _mcp_fix())
        raise RuntimeError(f"claude CLI error: {detail[:500]}")
    return env.get("result", "")


def _schema_tail(schema: dict) -> str:
    return ("\n\nRespond with ONLY a single JSON object — no prose, no markdown "
            "fences. It MUST conform to this JSON schema:\n" + json.dumps(schema))


def text_json(system: str, instructions: str, schema: dict, tool_name: str = "emit",
              timeout: int = CLI_TIMEOUT, model: Optional[str] = None) -> dict:
    """A pure reasoning call: no tools, JSON out."""
    if _backend() == "api":
        return _api_tool_call(model or "claude-opus-5", system, instructions, schema, tool_name)
    return _loads_loose(_cli_run(system + "\n\n" + instructions + _schema_tail(schema),
                                 timeout, model))


def agent_json(system: str, instructions: str, schema: dict, mcp_tools: list,
               timeout: int = CLI_TIMEOUT, model: Optional[str] = None) -> dict:
    """A tool-using call — the CLI drives MCP tools, then returns JSON.

    CLI-only: the API backend has no MCP connection, so rather than silently
    returning a toolless (and therefore empty) answer, this refuses outright."""
    if _backend() == "api":
        raise McpError("agent_json needs the CLI backend (the API backend has no MCP).",
                       "Fix: unset ANTHROPIC_API_KEY / set LLM_BACKEND=cli in helper/.env.")
    return _loads_loose(_cli_run(system + "\n\n" + instructions + _schema_tail(schema),
                                 timeout, model, allowed_tools=mcp_tools))


# --------------------------------------------------------------------------- #
# API (reasoning calls only)
# --------------------------------------------------------------------------- #
def _api_tool_call(model: str, system: str, content, schema: dict, tool_name: str) -> dict:
    import anthropic
    from anthropic import Anthropic
    try:
        msg = Anthropic().messages.create(
            model=model, max_tokens=8192, system=system,
            tools=[{"name": tool_name, "description": "Return the result.",
                    "input_schema": schema}],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": content}])
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
        raise AuthError(str(e)[:300], _auth_fix()) from e
    for block in msg.content:
        if getattr(block, "type", None) == "tool_use":
            return dict(block.input)
    raise RuntimeError("API returned no tool_use block")
