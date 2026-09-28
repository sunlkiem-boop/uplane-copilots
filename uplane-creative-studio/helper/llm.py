"""
LLM backend — two interchangeable paths behind one interface, copied from the
Uplane Compliance Copilot:

  * CLI  (default): shells out to your locally-logged-in `claude` command.
                    No API key, no extra cost; uses the same account Claude Code uses.
  * API  (opt-in) : Anthropic SDK if ANTHROPIC_API_KEY is set (forced-JSON, faster).

Selection: LLM_BACKEND=cli|api forces it; otherwise api if ANTHROPIC_API_KEY else cli.

Exposes generic helpers used by the creative engine + prompt builder:
  vision_json(img_bytes, system, instructions, schema) -> dict
  text_json(system, instructions, schema)              -> dict
"""
from __future__ import annotations

import os
import re
import json
import base64
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


def _backend() -> str:
    forced = os.environ.get("LLM_BACKEND", "").strip().lower()
    if forced in {"cli", "api"}:
        return forced
    return "api" if os.environ.get("ANTHROPIC_API_KEY") else "cli"


VISION_MODEL = os.environ.get("VISION_MODEL", "claude-sonnet-4-6")
TEXT_MODEL = os.environ.get("TEXT_MODEL", "claude-sonnet-4-6")
CLI_PATH = os.environ.get("CLAUDE_CLI_PATH") or shutil.which("claude") or "claude"
CLI_MODEL = os.environ.get("CLAUDE_CLI_MODEL", "").strip()
CLI_TIMEOUT = int(os.environ.get("CLI_TIMEOUT", "180"))


def backend_info() -> dict:
    b = _backend()
    info = {"backend": b}
    if b == "cli":
        info["cli_path"] = CLI_PATH
        info["cli_found"] = bool(shutil.which(CLI_PATH) or Path(CLI_PATH).exists())
        info["cli_model"] = CLI_MODEL or "(account default)"
    else:
        info["vision_model"] = VISION_MODEL
        info["api_key_set"] = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return info


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _clean(text: str) -> str:
    return _CTRL_RE.sub("", text)


def _loads_loose(text: str) -> dict:
    """Parse JSON that may be wrapped in prose or ``` fences."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON object found in model output: {text[:300]!r}")
    return json.loads(text[start : end + 1])


# --------------------------------------------------------------------------- #
# API backend
# --------------------------------------------------------------------------- #
def _api_tool_call(model: str, system: str, content, schema: dict, tool_name: str) -> dict:
    from anthropic import Anthropic

    msg = Anthropic().messages.create(
        model=model,
        max_tokens=4096,
        system=system,
        tools=[{"name": tool_name, "description": "Return the result.", "input_schema": schema}],
        tool_choice={"type": "tool", "name": tool_name},
        messages=[{"role": "user", "content": content}],
    )
    for block in msg.content:
        if getattr(block, "type", None) == "tool_use":
            return dict(block.input)
    raise RuntimeError("API returned no tool_use block")


# --------------------------------------------------------------------------- #
# CLI backend
# --------------------------------------------------------------------------- #
def _cli_run(prompt: str, allow_read: bool, timeout: int = CLI_TIMEOUT) -> str:
    cmd = [CLI_PATH, "-p", "--output-format", "json"]
    if CLI_MODEL:
        cmd += ["--model", CLI_MODEL]
    if allow_read:
        cmd += ["--allowedTools", "Read"]
    proc = subprocess.run(cmd, input=_clean(prompt), capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        detail = proc.stderr.strip()
        if not detail and proc.stdout.strip():
            try:
                env = json.loads(proc.stdout)
                detail = str(env.get("result") or env.get("error") or proc.stdout)
            except Exception:
                detail = proc.stdout.strip()
        raise RuntimeError(f"claude CLI failed ({proc.returncode}): {detail[:600]}")
    try:
        env = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return proc.stdout
    if env.get("is_error"):
        raise RuntimeError(f"claude CLI error: {str(env.get('result'))[:500]}")
    return env.get("result", "")


def _cli_json(prompt: str, image_path: Optional[str], schema: dict, timeout: int = CLI_TIMEOUT) -> dict:
    full = (
        (f"First, use the Read tool to open and view the image at: {image_path}\n\n" if image_path else "")
        + prompt
        + "\n\nRespond with ONLY a single JSON object — no prose, no markdown fences. "
        + "It MUST conform to this JSON schema:\n"
        + json.dumps(schema)
    )
    out = _cli_run(full, allow_read=bool(image_path), timeout=timeout)
    return _loads_loose(out)


_EXT_BY_TYPE = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}


# --------------------------------------------------------------------------- #
# Public
# --------------------------------------------------------------------------- #
def vision_json(img_bytes: bytes, system: str, instructions: str, schema: dict,
                media_type: str = "image/jpeg", tool_name: str = "emit", timeout: int = CLI_TIMEOUT) -> dict:
    if media_type not in _EXT_BY_TYPE:
        media_type = "image/jpeg"
    if _backend() == "api":
        b64 = base64.b64encode(img_bytes).decode("ascii")
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
            {"type": "text", "text": instructions},
        ]
        return _api_tool_call(VISION_MODEL, system, content, schema, tool_name)
    with tempfile.NamedTemporaryFile(suffix=_EXT_BY_TYPE[media_type], delete=False) as f:
        f.write(img_bytes)
        tmp = f.name
    try:
        return _cli_json(system + "\n\n" + instructions, tmp, schema, timeout=timeout)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def text_json(system: str, instructions: str, schema: dict, tool_name: str = "emit",
              timeout: int = CLI_TIMEOUT) -> dict:
    if _backend() == "api":
        return _api_tool_call(TEXT_MODEL, system, instructions, schema, tool_name)
    return _cli_json(system + "\n\n" + instructions, None, schema, timeout=timeout)
