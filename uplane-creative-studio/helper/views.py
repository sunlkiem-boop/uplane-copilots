"""
Saved views + per-client session memory.

A "view" is a Tab-1 analysis frozen WITHOUT the CSV: the periods, the filter and
display settings, the age selection, the ad-type label, the client, and the
computed categories + rows + numbers + one-liners. Saved under
  data/views/<client>/<id>.json
so the sidebar can reload it (and Tab 2 can import it) without re-uploading.

A "session" stores the last-used filter thresholds per client, powering the
"value from previous session" radio on the upload screen. Stored under
  data/sessions/<client>.json
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

DATA_DIR = Path(
    os.environ.get(
        "CREATIVE_STUDIO_DATA",
        str(Path(__file__).resolve().parent.parent / "data"),
    )
).expanduser()
VIEWS_DIR = DATA_DIR / "views"
SESSIONS_DIR = DATA_DIR / "sessions"


def _safe(name: str) -> str:
    """Filesystem-safe slug for a client or label."""
    s = re.sub(r"[^a-zA-Z0-9 _-]+", "", (name or "").strip()).strip()
    s = re.sub(r"\s+", "-", s)
    return s or "untitled"


def _client_safe(client: str) -> str:
    # Preserve the client's display name as the folder name (spaces ok), but
    # strip slashes / leading dots to keep it inside DATA_DIR.
    s = Path(client or "unknown").name.strip().lstrip(".")
    return s or "unknown"


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #
def save_view(client: str, label: str, ad_type: str, settings: dict, result: dict) -> dict:
    client = _client_safe(client)
    ts = datetime.now()
    vid = f"{ts.strftime('%Y%m%d-%H%M%S')}-{_safe(label)}"
    folder = VIEWS_DIR / client
    folder.mkdir(parents=True, exist_ok=True)
    record = {
        "id": vid,
        "client": client,
        "label": label or "Untitled view",
        "ad_type": ad_type or "image",
        "saved_at": ts.isoformat(timespec="seconds"),
        "periods": result.get("periods"),
        "settings": settings,
        "result": result,
    }
    (folder / f"{vid}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return _summary(record)


def _summary(record: dict) -> dict:
    """Lightweight view descriptor for the sidebar list."""
    return {
        "id": record["id"],
        "client": record["client"],
        "label": record["label"],
        "ad_type": record.get("ad_type", "image"),
        "saved_at": record["saved_at"],
        "periods": record.get("periods"),
    }


def list_views(client: Optional[str] = None) -> list[dict]:
    if not VIEWS_DIR.is_dir():
        return []
    out = []
    folders = [VIEWS_DIR / _client_safe(client)] if client else sorted(VIEWS_DIR.iterdir())
    for folder in folders:
        if not folder.is_dir():
            continue
        for f in folder.glob("*.json"):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
                out.append(_summary(rec))
            except Exception:
                continue
    out.sort(key=lambda v: v["saved_at"], reverse=True)
    return out


def all_ad_names(client: Optional[str] = None) -> list[str]:
    """Every distinct ad name across a client's saved views (first-seen order).
    This is the persistent record of which ads exist (we don't keep the CSV)."""
    names: list[str] = []
    seen: set[str] = set()
    if not VIEWS_DIR.is_dir():
        return names
    folders = [VIEWS_DIR / _client_safe(client)] if client else sorted(VIEWS_DIR.iterdir())
    for folder in folders:
        if not folder.is_dir():
            continue
        for f in folder.glob("*.json"):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            for cat in (rec.get("result") or {}).get("categories", []):
                for ad in cat.get("ads", []):
                    nm = (ad.get("ad_name") or "").strip()
                    if nm and nm not in seen:
                        seen.add(nm)
                        names.append(nm)
    return names


def get_view(vid: str, client: Optional[str] = None) -> Optional[dict]:
    vid = Path(vid).name  # no traversal
    candidates = []
    if client:
        candidates.append(VIEWS_DIR / _client_safe(client) / f"{vid}.json")
    else:
        candidates = list(VIEWS_DIR.glob(f"*/{vid}.json")) if VIEWS_DIR.is_dir() else []
    for p in candidates:
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    return None


def delete_view(vid: str, client: Optional[str] = None) -> bool:
    vid = Path(vid).name
    paths = (
        [VIEWS_DIR / _client_safe(client) / f"{vid}.json"]
        if client
        else (list(VIEWS_DIR.glob(f"*/{vid}.json")) if VIEWS_DIR.is_dir() else [])
    )
    deleted = False
    for p in paths:
        if p.is_file():
            p.unlink()
            deleted = True
    return deleted


# --------------------------------------------------------------------------- #
# Sessions (previous-thresholds memory)
# --------------------------------------------------------------------------- #
def get_session(client: str) -> Optional[dict]:
    p = SESSIONS_DIR / f"{_client_safe(client)}.json"
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def set_session(client: str, thresholds: dict) -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    p = SESSIONS_DIR / f"{_client_safe(client)}.json"
    payload = {
        "client": _client_safe(client),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        **(thresholds or {}),
    }
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
