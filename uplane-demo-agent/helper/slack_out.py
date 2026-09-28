"""Outbound chokepoint — the Demo Agent may talk to EXACTLY ONE conversation.

The digest's containment, verbatim in spirit: every outbound Slack call in this
codebase goes through post()/upload() below, both of which refuse any channel that
is not the one DM with Sun. There is no Bolt say() anywhere, so a coding mistake
elsewhere cannot widen the blast radius.
"""
from __future__ import annotations

import config
from slack_sdk import WebClient

_client: WebClient | None = None
_ALLOWED: set[str] | None = None


def client() -> WebClient:
    global _client
    if _client is None:
        _client = WebClient(token=config.SLACK_BOT_TOKEN)
    return _client


def allowed() -> set[str]:
    """The one DM channel with ME_SLACK_ID, resolved once."""
    global _ALLOWED
    if _ALLOWED is None:
        r = client().conversations_open(users=config.ME_SLACK_ID)
        _ALLOWED = {r["channel"]["id"]}
    return _ALLOWED


def _gate(channel: str) -> str:
    if channel not in allowed():
        raise PermissionError(f"outbound to {channel!r} refused — allowlist is {allowed()}")
    return channel


def post(text: str, channel: str | None = None, thread_ts: str | None = None) -> dict:
    ch = _gate(channel or next(iter(allowed())))
    return client().chat_postMessage(channel=ch, text=text, thread_ts=thread_ts)


def upload(path: str, title: str = "", channel: str | None = None) -> dict:
    ch = _gate(channel or next(iter(allowed())))
    return client().files_upload_v2(channel=ch, file=path, title=title or path)
