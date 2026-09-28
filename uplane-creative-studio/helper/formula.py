"""
Winning-formula codification + the per-ad prompt-builder model.

- codify(): contrast Winners vs Losers across the categorical creative-schema
  fields -> the winning formula (value + how strongly winners favor it over losers).
- preset(): the editable preset rows (one per "part") shown above the builder.
- build_card(): for one ad in a category, the part controls (Locked / Recommended /
  Freeform) per the performance diagnosis. The prompt itself is assembled client-side
  from these parts so it updates live as chips change.
"""
from __future__ import annotations

import json
from collections import Counter

import engine
from views import DATA_DIR, _client_safe

FORMULA_DIR = DATA_DIR / "formula"

# part: (key, group, label, primary categorical field or None)
# (no "format" part — unreliable for images, and some were uploaded as screenshots)
PARTS = [
    ("hook", "attention", "Hook / concept", "creative_archetype"),
    ("visual", "attention", "Visual & people", "human_presence"),
    ("headline", "attention", "Headline / messaging", "message_type"),
    ("aesthetic", "attention", "Aesthetic style", "aesthetic_style"),
    ("claim", "conversion", "Core claim / benefit", "benefit_type"),
    ("proof", "conversion", "Proof / trust", "proof_type"),
    ("offer", "conversion", "Offer mechanic", "offer_mechanic"),
    ("cta", "conversion", "Call to action", None),
    ("color", "look", "Color palette", "color_temperature"),
    ("text_placement", "look", "Text & placement", "text_density"),
    ("tagline", "look", "Tagline", None),
    ("graphics", "look", "Graphics / props", None),
]

# Freeform option sets for the Look knobs (the shallow variations).
LOOK_OPTIONS = {
    "color": ["brand palette", "high-contrast dark", "bright & warm", "cool & clean", "bold duotone", "muted pastel"],
    "text_placement": ["headline top", "headline bottom", "centered overlay", "minimal text", "left-aligned block"],
    "tagline": ["benefit tagline", "question hook", "price / offer line", "social-proof line", "no tagline"],
    "graphics": ["product hero", "before / after", "badge / seal", "icon callouts", "lifestyle scene", "data / stat callout"],
}

# Each non-loser category reduces to (CTR ok?, CPA ok?). Impressions = reach/budget.
CATEGORY_DIAGNOSIS = {
    "winners": {"ctr_ok": True, "cpa_ok": True, "mode": "Keep & vary"},
    # Strong potential is ALSO good on CTR & cost, but under-exposed — the play is to
    # SCALE it: keep the winning offer locked, but unlock the concept/attention so you
    # can spin bolder variants. `scale` is what separates it from Keep & vary.
    "strong_potential": {"ctr_ok": True, "cpa_ok": True, "mode": "Scale & vary", "scale": True},
    "lightweight": {"ctr_ok": False, "cpa_ok": True, "mode": "Fix attention"},
    "medium_potential": {"ctr_ok": False, "cpa_ok": True, "mode": "Fix attention"},
    "heavyweight": {"ctr_ok": True, "cpa_ok": False, "mode": "Fix conversion"},
    "weak_potential": {"ctr_ok": True, "cpa_ok": False, "mode": "Fix conversion"},
    # losers: no prompts
}


def control_for(group: str, diag: dict) -> str:
    """Locked / Recommended / Freeform for a part group, given the diagnosis."""
    if group == "look":
        return "freeform"
    if group == "format":
        return "recommended"
    if group == "attention":
        if diag["ctr_ok"] and diag["cpa_ok"]:
            # winners lock the concept (reproduce the winner); scale unlocks it to vary & expand.
            return "recommended" if diag.get("scale") else "locked"
        return "locked" if not diag["ctr_ok"] else "recommended"  # fix attention vs. it's fine
    if group == "conversion":
        if diag["ctr_ok"] and diag["cpa_ok"]:
            return "locked"
        return "locked" if not diag["cpa_ok"] else "recommended"  # fix conversion vs. it's fine
    return "recommended"


# --------------------------------------------------------------------------- #
# Contrast codification
# --------------------------------------------------------------------------- #
def _counts(labels_list: list[dict], field: str) -> Counter:
    c = Counter()
    for lab in labels_list:
        v = (lab.get(field) or "").strip().lower()
        if v and v != "unclear":
            c[v] += 1
    return c


def codify(winners: list[dict], losers: list[dict]) -> dict:
    """field -> {value, winners_pct, losers_pct, edge}. The 'edge' is how much more
    the winners favor that value than the losers do (the contrast signal)."""
    formula = {}
    nw, nl = max(len(winners), 1), max(len(losers), 1)
    for field, opts in engine.FIELD_OPTIONS.items():
        wc = _counts(winners, field)
        if not wc:
            continue
        value, wn = wc.most_common(1)[0]
        w_pct = round(wn / nw * 100)
        l_pct = round(_counts(losers, field).get(value, 0) / nl * 100) if losers else 0
        formula[field] = {"value": value, "winners_pct": w_pct, "losers_pct": l_pct, "edge": w_pct - l_pct}
    return formula


def _words(v) -> str:
    return str(v or "").replace("_", " ").replace("/", " / ").strip()


def _codify_cluster(labels_list: list[dict]) -> dict:
    """Dominant value per field WITHIN one cluster (+ % of the cluster), formula-shaped."""
    out = {}
    n = max(len(labels_list), 1)
    for field in engine.FIELD_OPTIONS:
        c = _counts(labels_list, field)
        if not c:
            continue
        value, cnt = c.most_common(1)[0]
        pct = round(cnt / n * 100)
        out[field] = {"value": value, "winners_pct": pct, "losers_pct": 0, "edge": pct}
    return out


def _concept_key(lab: dict) -> str:
    """The broad creative concept used to cluster winners into patterns (archetype,
    falling back to message strategy)."""
    v = (lab.get("creative_archetype") or "").strip().lower()
    if not v or v == "unclear":
        v = (lab.get("message_type") or "").strip().lower()
    return v or "other"


def patterns(labels_list: list[dict], min_members: int = 2, max_patterns: int = 4) -> list[dict]:
    """Cluster creatives into named CONCEPT patterns (not one formula). Winners rarely
    share a single recipe — they group into a few concepts (comparison cards, UGC
    testimonials, founder story…). Each pattern codifies its own dominant traits."""
    total = len(labels_list)
    groups: dict[str, list] = {}
    for lab in labels_list:
        groups.setdefault(_concept_key(lab), []).append(lab)
    pats = []
    for key, labs in groups.items():
        if len(labs) < min_members:
            continue
        pats.append({
            "key": key, "label": _words(key), "count": len(labs),
            "share": round(len(labs) / max(total, 1) * 100),
            "traits": _codify_cluster(labs),
            "headline_examples": [l.get("headline", "") for l in labs if l.get("headline")][:3],
        })
    pats.sort(key=lambda p: p["count"], reverse=True)
    pats = pats[:max_patterns]
    if not pats and labels_list:  # all singletons / tiny pool -> a single overall pattern
        pats = [{"key": "overall", "label": "Overall winners", "count": total, "share": 100,
                 "traits": _codify_cluster(labels_list),
                 "headline_examples": [l.get("headline", "") for l in labels_list if l.get("headline")][:3]}]
    return pats


def closest_pattern(labels: dict, pats: list[dict]) -> dict | None:
    """The pattern whose traits an ad most overlaps with (best-fit concept)."""
    best, best_score = None, -1
    for p in pats:
        score = sum(1 for f, t in p.get("traits", {}).items()
                    if (labels.get(f) or "").strip().lower() == t["value"])
        if score > best_score:
            best, best_score = p, score
    return best


def build_pattern_card(labels: dict, pattern: dict) -> dict:
    """Adapt card: every strategic part is tweakable (recommended), DEFAULTING to the
    original ad's own value so its vibe is retained; the pattern's winning value is
    offered as the suggestion (`winning`, highlighted in the UI). Look knobs freeform."""
    traits = (pattern or {}).get("traits", {})
    parts = []
    for key, group, label, primary in PARTS:
        ad_val = (labels.get(primary) or "").strip().lower() if primary else None
        win_val = traits.get(primary, {}).get("value") if primary else None
        if group == "look" or not primary:
            options = LOOK_OPTIONS.get(key, [])
            parts.append({"key": key, "label": label, "group": group, "control": "freeform",
                          "field": primary, "value": options[0] if options else (ad_val or ""),
                          "options": options, "ad_value": ad_val, "winning": win_val})
        else:
            options = engine.FIELD_OPTIONS.get(primary, [])
            parts.append({"key": key, "label": label, "group": group, "control": "recommended",
                          "field": primary, "value": ad_val or win_val or "",
                          "options": options, "ad_value": ad_val, "winning": win_val})
    return {"category": "adapt", "mode": "Adapt — keep the vibe, nudge to what wins",
            "ctr_ok": True, "cpa_ok": True, "parts": parts}


def preset(formula: dict) -> list[dict]:
    """Editable preset rows — one per part (skips parts with no categorical field)."""
    rows = []
    for key, group, label, primary in PARTS:
        if not primary or primary not in formula:
            continue
        f = formula[primary]
        rows.append({
            "key": key, "group": group, "label": label, "field": primary,
            "value": f["value"], "options": engine.FIELD_OPTIONS.get(primary, []),
            "winners_pct": f["winners_pct"], "losers_pct": f["losers_pct"], "edge": f["edge"],
        })
    return rows


def build_card(category: str, labels: dict, formula: dict) -> dict | None:
    diag = CATEGORY_DIAGNOSIS.get(category)
    if diag is None:
        return None  # losers / unknown -> no prompts
    parts = []
    for key, group, label, primary in PARTS:
        control = control_for(group, diag)
        ad_val = (labels.get(primary) or "").strip().lower() if primary else None
        fval = formula.get(primary, {}).get("value") if primary else None
        if group == "look" or not primary:
            options = LOOK_OPTIONS.get(key, [])
            value = options[0] if options else (ad_val or "")
            control = "freeform"
        elif control == "locked":
            value = fval or ad_val or ""
            options = [value]
        else:  # recommended
            value = fval or ad_val or ""
            options = engine.FIELD_OPTIONS.get(primary, [])
        parts.append({
            "key": key, "label": label, "group": group, "control": control, "field": primary,
            "value": value, "options": options, "ad_value": ad_val,
        })
    return {"category": category, "mode": diag["mode"], "ctr_ok": diag["ctr_ok"], "cpa_ok": diag["cpa_ok"], "parts": parts}


# --------------------------------------------------------------------------- #
# Persistence (the per-client "what works" memory)
# --------------------------------------------------------------------------- #
def save(client: str, ad_type: str, formula: dict, counts: dict, patterns: list = None) -> None:
    d = FORMULA_DIR
    d.mkdir(parents=True, exist_ok=True)
    payload = {"client": _client_safe(client), "ad_type": ad_type, "formula": formula, "counts": counts}
    if patterns is not None:
        payload["patterns"] = patterns
    (d / f"{_client_safe(client)}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load(client: str) -> dict | None:
    p = FORMULA_DIR / f"{_client_safe(client)}.json"
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None
