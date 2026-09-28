"""
Manual edits, and how they survive the next rebuild.

The digest is regenerated from scratch every morning, so an edit made in Slack has
to be stored as an *intention* rather than a patch to one day's output — otherwise
every correction is forgotten overnight, which makes the bot untrustworthy the
second time you use it.

Each edit is applied twice, on purpose, because the two paths fail differently:

  * **Into the synthesis prompt** (`prompt_block`) — semantic, so it still works
    tomorrow when the model words the same item differently. This is what makes a
    dropped item stay dropped.
  * **Onto the finished digest** (`apply`) — literal fuzzy match, so the edit takes
    effect *now* even if the model ignored the instruction. This is what makes the
    Slack message change the moment you ask.

Drops expire (default 21 days). An `add` does not: it is real work that hasn't been
done yet. Nothing is ever silently in force — the footer always carries the count,
and `list edits` in Slack shows each one with its age.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher

import config

PATH = config.DATA_DIR / "overrides.json"
DROP_TTL_DAYS = int(__import__("os").environ.get("DIGEST_DROP_TTL_DAYS", "21"))
_MATCH_THRESHOLD = 0.62


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def load() -> dict:
    if not PATH.exists():
        return {"version": 1, "items": []}
    try:
        with open(PATH, encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("items", [])
        return d
    except (OSError, ValueError):
        return {"version": 1, "items": []}


def save(d: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)


def add(action: str, client: str = "", match: str = "", fields: dict | None = None,
        note: str = "", today: date | None = None) -> dict:
    """Record an edit. `action` is drop | add | modify | status."""
    today = today or date.today()
    d = load()
    nid = f"ov{len(d['items']) + 1}_{today.strftime('%m%d')}"
    rec = {"id": nid, "action": action, "client": config.canonical(client) or client,
           "match": match, "fields": fields or {}, "note": note,
           "created": today.isoformat()}
    if action == "drop" and DROP_TTL_DAYS > 0:
        rec["expires"] = (today + timedelta(days=DROP_TTL_DAYS)).isoformat()
    d["items"].append(rec)
    save(d)
    return rec


def remove(oid: str) -> dict | None:
    d = load()
    for i, r in enumerate(d["items"]):
        if r["id"] == oid:
            d["items"].pop(i)
            save(d)
            return r
    return None


def undo(today: date | None = None) -> dict | None:
    """Drop the most recently added edit."""
    d = load()
    if not d["items"]:
        return None
    r = d["items"].pop()
    save(d)
    return r


def clear() -> int:
    d = load()
    n = len(d["items"])
    save({"version": 1, "items": []})
    return n


def active(today: date | None = None) -> list[dict]:
    today = today or date.today()
    out = []
    for r in load()["items"]:
        exp = r.get("expires")
        if exp and exp < today.isoformat():
            continue
        out.append(r)
    return out


def prune(today: date | None = None) -> int:
    """Delete expired edits. Returns how many went."""
    today = today or date.today()
    d = load()
    before = len(d["items"])
    d["items"] = [r for r in d["items"]
                  if not (r.get("expires") and r["expires"] < today.isoformat())]
    if len(d["items"]) != before:
        save(d)
    return before - len(d["items"])


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
_NORM = re.compile(r"[^a-z0-9 ]+")


def _norm(s: str) -> str:
    return " ".join(_NORM.sub(" ", (s or "").lower()).split())


def _similar(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na in nb or nb in na:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def _todo_text(t: dict) -> str:
    return f"{t.get('deliverable', '')} {t.get('detail', '')}".strip()


def _find(group_todos: list[dict], match: str) -> int:
    """Index of the best match above threshold, else -1."""
    best, best_i = 0.0, -1
    for i, t in enumerate(group_todos):
        s = _similar(match, _todo_text(t))
        if s > best:
            best, best_i = s, i
    return best_i if best >= _MATCH_THRESHOLD else -1


# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #
def _group_for(digest: dict, client: str) -> dict:
    for g in digest.get("clients") or []:
        if g.get("client") == client:
            return g
    g = {"client": client,
         "display": config.clients().get(client, {}).get("display") or client or "Other",
         "deadline": "", "todos": [], "waiting_on": []}
    digest.setdefault("clients", []).append(g)
    return g


def apply(digest: dict, today: date | None = None) -> dict:
    """Apply active edits to a finished digest. Records what it did in `_edits`."""
    today = today or date.today()
    applied, missed = [], []
    for r in active(today):
        act, client, match = r["action"], r.get("client", ""), r.get("match", "")
        fields = r.get("fields") or {}

        if act == "add":
            g = _group_for(digest, client)
            g["todos"].append({
                "deliverable": fields.get("deliverable", "") or match or "(unnamed)",
                "quantity": fields.get("quantity", ""),
                "detail": fields.get("detail", ""),
                "owner": "you", "owner_name": "",
                "status": fields.get("status", "today"),
                "due": fields.get("due", ""), "due_label": fields.get("due_label", "you added"),
                "source_url": "", "source_label": f"added by you {r['created'][5:]}",
                "supersedes": "",
            })
            applied.append(r["id"])
            continue

        # drop / modify / status all need to locate an existing item.
        groups = ([g for g in digest.get("clients") or [] if g.get("client") == client]
                  if client else list(digest.get("clients") or []))
        hit = False
        for g in groups:
            i = _find(g.get("todos") or [], match)
            if i < 0:
                continue
            if act == "drop":
                g["todos"].pop(i)
            elif act == "status":
                g["todos"][i]["status"] = fields.get("status", "open")
            elif act == "modify":
                for k, v in fields.items():
                    if v:
                        g["todos"][i][k] = v
                g["todos"][i]["source_label"] = (
                    (g["todos"][i].get("source_label") or "") + " · you edited").strip(" ·")
            hit = True
            break
        (applied if hit else missed).append(r["id"])

    digest["_edits"] = {"active": len(active(today)), "applied": applied, "unmatched": missed}
    return digest


def prompt_block(today: date | None = None) -> str:
    """Active edits, rendered as instructions for the synthesis pass."""
    today = today or date.today()
    items = active(today)
    if not items:
        return ""
    drops = [r for r in items if r["action"] == "drop"]
    mods = [r for r in items if r["action"] in ("modify", "status")]
    adds = [r for r in items if r["action"] == "add"]
    L = ["\n# HIS OWN CORRECTIONS — these outrank the notes",
         "He edited a previous digest by hand. The notes do not know about any of this, "
         "so honour it or you will hand him back work he already told you to remove."]
    if drops:
        L.append("\nDROPPED — do not emit these, or anything that is the same work under "
                 "different wording:")
        L += [f'  - [{r.get("client") or "any client"}] "{r["match"]}"'
              f'{" — " + r["note"] if r.get("note") else ""}' for r in drops]
    if mods:
        L.append("\nCORRECTED — his version is right, the note's version is not:")
        L += [f'  - [{r.get("client") or "any"}] "{r["match"]}" → '
              f'{json.dumps(r["fields"], ensure_ascii=False)}' for r in mods]
    if adds:
        L.append("\nADDED BY HIM — include these; do not duplicate them if the notes also "
                 "mention the same work:")
        L += [f'  - [{r.get("client") or "unassigned"}] '
              f'{r["fields"].get("deliverable") or r["match"]}'
              f'{" (due " + r["fields"]["due"] + ")" if r["fields"].get("due") else ""}'
              for r in adds]
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- #
# Which Slack message is today's digest
# --------------------------------------------------------------------------- #
STATE = config.DATA_DIR / "state.json"


def _state() -> dict:
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def set_message(day: str, channel: str, ts: str) -> None:
    """Remember the posted digest so a later edit can update it in place instead of
    posting a second copy — two digests in one morning is worse than none."""
    s = _state()
    s[day] = {"channel": channel, "ts": ts}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)


def get_message(day: str) -> dict:
    return _state().get(day) or {}


def summary(today: date | None = None) -> str:
    """Human-readable list for the `list edits` reply."""
    items = active(today)
    if not items:
        return "No manual edits are active."
    lines = []
    for r in items:
        age = (date.today() - date.fromisoformat(r["created"])).days
        what = r.get("match") or (r["fields"].get("deliverable") if r.get("fields") else "")
        cl = config.clients().get(r.get("client", ""), {}).get("display") or r.get("client") or "—"
        exp = f", expires {r['expires']}" if r.get("expires") else ""
        lines.append(f"`{r['id']}`  *{r['action']}*  [{cl}]  {what}  _({age}d ago{exp})_")
    return "\n".join(lines)
