"""
"Upload your own" → fit-check against a client's winning formula.

Given an uploaded creative's cached labels and a target client's codified winning
formula (the traits that separate that client's winners from losers), score how
well the upload fits, then decide ADAPT (close — tweak the few diverging traits)
vs RECREATE (far — rebuild around the winning formula). Pure local stats, no LLM.
"""
from __future__ import annotations

import engine
import formula as formula_mod

# A formula field counts as a "winning trait" worth matching against when winners
# favour it noticeably more than losers do. Edge = winners_pct − losers_pct.
_MIN_EDGE = 12
_MIN_TRAITS = 4          # if fewer clear the bar, top up by edge so there's enough to compare
_MAX_TRAITS = 10         # cap so the brief stays digestible (strongest-contrast first)
_ADAPT_THRESHOLD = 0.55  # fit at/above this → adapt; below → recreate

# field -> human label, from the prompt-builder parts.
_FIELD_LABEL = {primary: label for _k, _g, label, primary in formula_mod.PARTS if primary}


def _words(v) -> str:
    return str(v or "").replace("_", " ").replace("/", " / ").strip()


def _strong_traits(formula: dict) -> list[dict]:
    """The winning traits to grade an upload against, strongest contrast first."""
    fields = []
    for field, f in (formula or {}).items():
        if not isinstance(f, dict) or not f.get("value"):
            continue
        fields.append({"field": field, "label": _FIELD_LABEL.get(field, _words(field)),
                       "value": f["value"], "edge": f.get("edge", 0),
                       "winners_pct": f.get("winners_pct", 0)})
    fields.sort(key=lambda x: x["edge"], reverse=True)
    strong = [f for f in fields if f["edge"] >= _MIN_EDGE]
    if len(strong) < _MIN_TRAITS:
        # not enough clear signal — top up with the next-best positive-edge traits
        extra = [f for f in fields if f not in strong and f["edge"] > 0]
        strong = (strong + extra)[: max(_MIN_TRAITS, len(strong))]
    return strong[:_MAX_TRAITS]


def check_one(name: str, labels: dict, strong: list[dict]) -> dict:
    matches, diverges = [], []
    for t in strong:
        have = (labels.get(t["field"]) or "").strip().lower()
        want = (t["value"] or "").strip().lower()
        if have and have == want:
            matches.append({"label": t["label"], "value": _words(want), "winners_pct": t["winners_pct"]})
        else:
            diverges.append({"label": t["label"], "your_value": _words(have) or "—",
                             "winning_value": _words(want), "winners_pct": t["winners_pct"]})
    n = len(strong)
    score = (len(matches) / n) if n else 0.0
    verdict = "adapt" if (n and score >= _ADAPT_THRESHOLD) else "recreate"

    # Brief: ADAPT keeps the matches, changes the divergers; RECREATE specs the full
    # winning formula. Both carry the upload's own copy so the writer keeps its voice.
    if verdict == "adapt":
        brief = {
            "keep": [f"{m['label']}: {m['value']}" for m in matches],
            "change": [f"{d['label']}: {d['your_value']} → {d['winning_value']} "
                       f"({d['winners_pct']}% of winners use this)" for d in diverges],
        }
    else:
        brief = {"build": [f"{t['label']}: {_words(t['value'])} ({t['winners_pct']}% of winners)" for t in strong]}

    return {
        "name": name,
        "fit_score": round(score * 100),
        "verdict": verdict,
        "matches": matches,
        "diverges": diverges,
        "brief": brief,
        "description": {
            "headline": labels.get("headline", ""),
            "sub_headline": labels.get("sub_headline", ""),
            "subtext": labels.get("subtext", ""),
            "cta": labels.get("cta_text_exact", ""),
            "visual": labels.get("visual_description", ""),
        },
    }


def fit_check(target_client: str, upload_client: str, names: list[str]) -> dict:
    """Compare each named upload (in upload_client's creative store) against
    target_client's saved winning formula."""
    rec = formula_mod.load(target_client)
    formula = (rec or {}).get("formula") or {}
    strong = _strong_traits(formula)
    if not strong:
        return {"ok": False, "reason": "no_formula",
                "error": f"No winning formula for “{target_client}” yet. Run a Performance Review for "
                         f"{target_client} and click “Generate concepts” once so the studio learns what "
                         f"wins, then fit-check uploads against it."}

    labels = engine.cached_labels(upload_client, names)
    cards, needs = [], []
    for nm in names:
        lab = labels.get(nm)
        if not lab:
            needs.append(nm)
            continue
        cards.append(check_one(nm, lab, strong))
    cards.sort(key=lambda c: c["fit_score"], reverse=True)
    return {
        "ok": True,
        "target": target_client,
        "trait_count": len(strong),
        "traits": [{"label": t["label"], "value": _words(t["value"]), "winners_pct": t["winners_pct"]} for t in strong],
        "cards": cards,
        "needs_labeling": needs,
    }
