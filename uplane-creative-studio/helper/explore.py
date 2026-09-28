"""Data-grounded 'what-if' explorer — NO LLM, NO API key.

Joins each creative's metadata attributes (from the cache) to its performance
bucket (winner / loser, from a saved view) and answers questions by counting:
"creatives WITH attribute X: N winners / M losers" vs without. Plain arithmetic.
"""
from __future__ import annotations

from collections import defaultdict

import engine

# free-text words -> (field, value-or-None) so plain questions map to attributes
SYNONYMS = {
    "face": ("human_presence", "face"), "faces": ("human_presence", "face"),
    "human": ("human_presence", None), "humans": ("human_presence", None),
    "people": ("human_presence", None), "person": ("human_presence", None),
    "hands": ("human_presence", "hands_only"),
    "price": ("offer_mechanic", "price_anchor"), "pricing": ("offer_mechanic", "price_anchor"),
    "discount": ("offer_mechanic", "discount"), "offer": ("offer_mechanic", None),
    "proof": ("proof_type", None), "review": ("proof_type", "review/rating"),
    "rating": ("proof_type", "review/rating"), "testimonial": ("proof_type", "testimonial"),
    "color": ("color_temperature", None), "warm": ("color_temperature", "warm"),
    "cool": ("color_temperature", "cool"), "text": ("text_density", None),
    "headline": ("message_type", None), "benefit": ("benefit_type", None),
    "emotion": ("primary_emotion", None), "trust": ("trust_cue", None),
    "aesthetic": ("aesthetic_style", None), "style": ("aesthetic_style", None),
    "subject": ("primary_subject", None), "archetype": ("creative_archetype", None),
    "hook": ("creative_archetype", None), "polish": ("visual_polish", None),
    "setting": ("setting", None), "comparison": ("creative_archetype", "comparison"),
}


def _rows(client: str, categories: dict) -> list[dict]:
    all_ads = [a for ads in (categories or {}).values() for a in ads]
    labels, _ = engine.analyze_many(client, all_ads)
    winners, losers = set(categories.get("winners", [])), set(categories.get("losers", []))
    return [{"ad": a, "labels": labels[a], "winner": a in winners, "loser": a in losers}
            for a in all_ads if a in labels]


def _match(query: str):
    """Map a free-text question to (field, value-or-None)."""
    q = (query or "").lower()
    # 1) an exact attribute value mentioned (e.g. "price_anchor", "talking head")
    for field, opts in engine.FIELD_OPTIONS.items():
        for o in opts:
            for tok in o.replace("/", " ").replace("_", " ").split():
                if len(tok) >= 4 and tok in q:
                    return field, o
    # 2) a synonym
    for k, (f, v) in SYNONYMS.items():
        if k in q:
            return f, v
    # 3) a field name
    for field in engine.FIELD_OPTIONS:
        if field.replace("_", " ") in q:
            return field, None
    return None, None


def _tally(rows: list[dict]) -> dict:
    d = {"n": len(rows), "winners": sum(r["winner"] for r in rows), "losers": sum(r["loser"] for r in rows)}
    d["win_rate"] = round(d["winners"] / d["n"] * 100) if d["n"] else 0
    d["loss_rate"] = round(d["losers"] / d["n"] * 100) if d["n"] else 0
    return d


def _breakdown(rows: list[dict], field: str) -> list[dict]:
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        groups[(r["labels"].get(field) or "").strip().lower() or "—"].append(r)
    out = [{"value": v, **_tally(g)} for v, g in groups.items()]
    out.sort(key=lambda d: (-d["win_rate"], -d["n"]))
    return out


def _with_without(rows: list[dict], field: str, value: str) -> dict:
    has = [r for r in rows if (r["labels"].get(field) or "").strip().lower() == value]
    hasnt = [r for r in rows if (r["labels"].get(field) or "").strip().lower() != value]
    return {"value": value, "with": _tally(has), "without": _tally(hasnt)}


def _signals(rows: list[dict]) -> dict:
    nW = sum(r["winner"] for r in rows) or 1
    nL = sum(r["loser"] for r in rows) or 1
    sig = []
    for field, opts in engine.FIELD_OPTIONS.items():
        for o in opts:
            w = sum(1 for r in rows if r["winner"] and (r["labels"].get(field) or "").lower() == o)
            l = sum(1 for r in rows if r["loser"] and (r["labels"].get(field) or "").lower() == o)
            if w + l < 2:
                continue
            edge = round(w / nW * 100 - l / nL * 100)
            if abs(edge) >= 20:
                sig.append({"field": field, "value": o, "winners": w, "losers": l, "edge": edge})
    return {
        "wins": sorted([s for s in sig if s["edge"] > 0], key=lambda s: -s["edge"])[:6],
        "fails": sorted([s for s in sig if s["edge"] < 0], key=lambda s: s["edge"])[:6],
    }


# human-readable "general traits" to profile a group of creatives by
TRAIT_FIELDS = ["creative_archetype", "aesthetic_style", "primary_emotion",
                "color_temperature", "message_type", "human_presence", "offer_mechanic"]


def _profile(rows: list[dict]) -> list[str]:
    """The dominant value of each trait field across a group (deduped, capped)."""
    out = []
    for field in TRAIT_FIELDS:
        c = defaultdict(int)
        for r in rows:
            v = (r["labels"].get(field) or "").strip().lower()
            if v and v not in ("unclear", "none"):
                c[v] += 1
        if c:
            top = max(c.items(), key=lambda kv: kv[1])[0]
            if top not in out:
                out.append(top)
    return out[:4]


def trait_oneliners(client: str, categories: dict, media_types: dict) -> list[dict]:
    """Per format (image / video): the dominant traits across NON-losers vs losers,
    so the UI can render a one-liner contrast. Raw lowercase values (UI humanizes)."""
    rows = _rows(client, categories or {})
    media_types = media_types or {}
    lines = []
    for t in ("image", "video"):
        grp = [r for r in rows if (media_types.get(r["ad"]) or "image") == t]
        if not grp:
            continue
        nl = [r for r in grp if not r["loser"]]
        ls = [r for r in grp if r["loser"]]
        lines.append({"type": t, "non_losers_n": len(nl), "losers_n": len(ls),
                      "non_losers": _profile(nl), "losers": _profile(ls)})
    return lines


def explore(client: str, categories: dict, query: str = "") -> dict:
    rows = _rows(client, categories or {})
    res = {"n": len(rows), "winners": sum(r["winner"] for r in rows),
           "losers": sum(r["loser"] for r in rows), "signals": _signals(rows)}
    if query:
        field, value = _match(query)
        res["query"] = query
        res["matched"] = {"field": field, "value": value, "field_label": field.replace("_", " ") if field else None}
        if field:
            res["breakdown"] = _breakdown(rows, field)
            if value:
                res["with_without"] = _with_without(rows, field, value)
    return res
