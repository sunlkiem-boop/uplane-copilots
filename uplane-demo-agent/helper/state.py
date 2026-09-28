"""Per-client stage state: where each client is, and every typed verdict on record.

data/state/<client>.json:
  { "client": "...", "stage": 2,                 # highest stage APPROVED
    "pending": {"stage": 3, "plan": "...", "at": ...} | null,
    "signoffs": [ {"stage": 3, "verdict": "approve", "text": "...", "at": "..."} ] }
"""
from __future__ import annotations

import json
import re
import time

import config


def _norm(client: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", client.lower()).strip("-")


def path(client: str):
    return config.STATE_DIR / f"{_norm(client)}.json"


def load(client: str) -> dict:
    try:
        return json.loads(path(client).read_text())
    except (OSError, ValueError):
        return {"client": _norm(client), "stage": -1, "pending": None, "signoffs": []}


def save(st: dict) -> None:
    path(st["client"]).write_text(json.dumps(st, indent=1))


def known_clients() -> list[str]:
    return sorted(p.stem for p in config.STATE_DIR.glob("*.json"))


def record_verdict(client: str, stage: int, verdict: str, text: str) -> dict:
    st = load(client)
    st["signoffs"].append({"stage": stage, "verdict": verdict, "text": text[:600],
                           "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    if verdict == "approve" and stage > st["stage"]:
        st["stage"] = stage
    st["pending"] = None
    save(st)
    return st
