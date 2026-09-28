"""
The living per-client performance profile — replaces the pile of dated "saved
views". One profile per client = the latest analysis, kept fresh automatically,
distilled to: metric averages (winners vs all ads), the winning formula (only the
traits that genuinely separate winners from losers), and bucket counts.

Source of truth, in order:
  1. data/profiles/<client>.json   (written automatically on every /analyze)
  2. fallback: the newest existing data/views/<client>/*.json  (so prior saved
     views keep working with zero migration)

The winning-formula stats come straight from formula.codify (winners_pct /
losers_pct / edge) — no invented numbers. A trait only shows if its contrast is
strong; weak ones are hidden and counted.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import formula
import views
from views import DATA_DIR, _client_safe

PROFILES_DIR = DATA_DIR / "profiles"

# A trait only earns a spot in the "winning formula" if winners favor it this
# much more than losers (percentage points) — below this it's not a real signal.
STRONG_EDGE = 18
MIN_WINNERS_PCT = 40
MAX_TRAITS = 6

# field -> short, self-explanatory label for the profile card
FIELD_LABEL = {
    "creative_archetype": "Hook",
    "message_type": "Message",
    "benefit_type": "Benefit",
    "proof_type": "Proof",
    "offer_mechanic": "Offer",
    "aesthetic_style": "Look",
    "human_presence": "People",
    "color_temperature": "Color",
    "text_density": "Text",
    "primary_emotion": "Emotion",
}


def _humanize(v) -> str:
    return str(v or "").replace("_", " ").replace("/", " / ").strip()


def save_profile(client: str, ad_type: str, settings: dict, result: dict) -> None:
    """Persist (overwrite) the client's living profile; archive the prior run's
    one-line summary into history (most recent first, capped)."""
    safe = _client_safe(client)
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    path = PROFILES_DIR / f"{safe}.json"

    history = []
    if path.is_file():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
            history = prev.get("history", [])
            history.insert(0, {
                "updated_at": prev.get("updated_at"),
                "ads": prev.get("_ads"),
                "winners": prev.get("_winners"),
            })
            history = history[:8]
        except Exception:
            history = []

    cats = result.get("categories", [])
    by_key = {c.get("key"): c for c in cats}
    total = (result.get("counts", {}) or {}).get("kept") or sum(c.get("count", 0) for c in cats)
    record = {
        "client": safe,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "ad_type": ad_type or "image",
        "settings": settings,
        "result": result,
        "history": history,
        "_ads": total,
        "_winners": (by_key.get("winners", {}) or {}).get("count", 0),
    }
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")


def _latest(client: str) -> Optional[dict]:
    """{result, ad_type, updated_at, settings, history} from the profile file, or
    falling back to the newest saved view (backward-compat)."""
    path = PROFILES_DIR / f"{_client_safe(client)}.json"
    if path.is_file():
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            return {"result": d.get("result", {}), "ad_type": d.get("ad_type", "image"),
                    "updated_at": d.get("updated_at"), "settings": d.get("settings"),
                    "history": d.get("history", []), "from": "profile"}
        except Exception:
            pass
    vs = views.list_views(client)
    if vs:
        rec = views.get_view(vs[0]["id"], client)
        if rec:
            return {"result": rec.get("result", {}), "ad_type": rec.get("ad_type", "image"),
                    "updated_at": rec.get("saved_at"), "settings": rec.get("settings"),
                    "history": [{"updated_at": v["saved_at"]} for v in vs[1:9]], "from": "view"}
    return None


def _winning_formula(client: str) -> tuple[list, int, bool]:
    """Strong, justified traits from the saved formula. -> (traits, hidden_count, has_formula)."""
    f = formula.load(client)
    if not f or not f.get("formula"):
        return [], 0, False
    strong, hidden = [], 0
    for field, st in f["formula"].items():
        if field not in FIELD_LABEL:
            continue
        edge = st.get("edge", 0) or 0
        wp = st.get("winners_pct", 0) or 0
        if edge >= STRONG_EDGE and wp >= MIN_WINNERS_PCT:
            strong.append({
                "field": field, "label": FIELD_LABEL[field],
                "value": _humanize(st.get("value")),
                "winners_pct": wp, "losers_pct": st.get("losers_pct", 0) or 0, "edge": edge,
            })
        else:
            hidden += 1
    strong.sort(key=lambda x: x["edge"], reverse=True)
    return strong[:MAX_TRAITS], hidden, True


def full_result(client: str) -> Optional[dict]:
    """The client's latest full analysis result (for the Performance Review screen)."""
    d = _latest(client)
    return d["result"] if d else None


def list_profiles() -> list:
    """One entry per client that has a profile (or a legacy view), newest first."""
    out: dict[str, dict] = {}
    if PROFILES_DIR.is_dir():
        for p in sorted(PROFILES_DIR.glob("*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                out[d.get("client")] = {"client": d.get("client"), "updated_at": d.get("updated_at"),
                                        "ads": d.get("_ads"), "winners": d.get("_winners")}
            except Exception:
                pass
    for v in views.list_views():            # legacy saved views → still surfaced per client
        c = v.get("client")
        if c and c not in out:
            out[c] = {"client": c, "updated_at": v.get("saved_at"), "ads": None, "winners": None}
    return sorted(out.values(), key=lambda x: x.get("updated_at") or "", reverse=True)


def build_profile(client: str) -> dict:
    data = _latest(client)
    if not data:
        return {"client": client, "has_data": False}
    result = data["result"]
    cats = result.get("categories", [])
    by_key = {c.get("key"): c for c in cats}
    winners = by_key.get("winners", {}) or {}
    losers = by_key.get("losers", {}) or {}
    total = (result.get("counts", {}) or {}).get("kept") or sum(c.get("count", 0) for c in cats)
    bench = result.get("benchmark", {}) or {}
    w_avg = winners.get("avg", {}) or {}

    traits, hidden, has_formula = _winning_formula(client)

    # categories -> ad-name map so the Studio can build straight from the profile
    cat_ads = {c.get("key"): [a.get("ad_name") for a in (c.get("ads") or [])] for c in cats}

    return {
        "client": client,
        "has_data": True,
        "source": data.get("from"),
        "updated_at": data.get("updated_at"),
        "ad_type": data.get("ad_type", "image"),
        "totals": {
            "ads": total,
            "winners": winners.get("count", 0),
            "losers": losers.get("count", 0),
            "between": max(total - winners.get("count", 0) - losers.get("count", 0), 0),
        },
        "averages": {
            "winners": {"ctr": w_avg.get("ctr"), "cpa": w_avg.get("cpa"), "n": w_avg.get("n")},
            "all": {"ctr": bench.get("ctr"), "cpa": bench.get("cpa"), "n": bench.get("n")},
        },
        "formula": traits,
        "formula_hidden": hidden,
        "has_formula": has_formula,
        "history_count": len(data.get("history", [])),
        "categories": cat_ads,
    }
