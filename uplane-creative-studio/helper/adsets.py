"""
Ad-set analysis for Performance Review (Tab 1).

Two things layered on top of the per-creative analysis, both keyed off the
"Ad set name" column (only present when the Meta export includes the ad-set
breakdown):

1. set_view  — roll every ad up to its AD SET (images + videos lumped together,
   since a set mixes them), aggregate the underlying counts, categorise the SETS
   with the same 7-bucket logic the per-creative view uses, and for each set name
   the creative that CARRIED it (most of the set's results).

2. annotate  — per-creative notes for the existing view that explain the
   "performance was distributed" phenomenon the user noticed:
     * delivery concentration inside an ad's own set (a sibling hogged the budget,
       so a starved sibling looks like a loser when it was never given a chance), and
     * look-alike ads (shared cached creative traits) that under/over-performed in
       OTHER sets — the inferred reason an ad's result may owe more to its set than
       to the creative itself.

Pure local stats — no LLM. Reuses analysis.py's aggregation + category model.
"""
from __future__ import annotations

import analysis
import creatives

# Strong = clearly working, weak = clearly not — used to read a look-alike's fate.
_STRONG_CATS = {"winners", "strong_potential"}
_WEAK_CATS = {"losers", "weak_potential", "medium_potential"}

# Categorical fields that define a creative's "look" for look-alike matching.
_LOOKALIKE_FIELDS = [
    "creative_archetype", "message_type", "human_presence",
    "benefit_type", "aesthetic_style", "proof_type",
]
_MIN_SHARED_TRAITS = 3  # how many of the above must match to count as a look-alike


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _words(v) -> str:
    return (str(v or "").replace("_", " ").replace("/", " / ").strip())


def _selected_ages(ds, age_selection):
    if not ds.age_available or not age_selection or "all" in age_selection:
        return None
    sel = {a for a in age_selection if a in set(ds.age_segments)}
    return sel or None


def _pct(part, whole):
    if not whole:
        return 0
    return round(part / whole * 100)


def _set_metrics(members: list[dict], period_key: str) -> dict:
    """Set-level metrics from the underlying COUNTS of its member ads."""
    imp = sum((m[period_key].get("imp") or 0) for m in members)
    clicks = sum((m[period_key].get("clicks") or 0) for m in members)
    spend = sum((m[period_key].get("spend") or 0) for m in members)
    results = sum((m[period_key].get("results") or 0) for m in members)
    ctr = (clicks / imp * 100.0) if imp > 0 else None
    cpa = (spend / results) if results > 0 else None
    return {"imp": imp or None, "ctr": ctr, "cpa": cpa,
            "clicks": clicks or None, "spend": spend or None, "results": results or None}


# --------------------------------------------------------------------------- #
# Group ads by ad set (ALL ads, not just gate-passers — a starved sibling is
# often gate-excluded, and that's exactly the ad we want to surface).
# --------------------------------------------------------------------------- #
def _group_by_set(ds, selected_ages, delivery_sel, media_types) -> dict:
    groups: dict[str, list] = {}
    for name, entry in ds.ads.items():
        aset = (entry.get("ad_set") or "").strip()
        if not aset:
            continue
        if delivery_sel and entry["delivery"] not in delivery_sel:
            continue
        rows = entry["rows"]
        if selected_ages is not None:
            rows = [r for r in rows if (r.get(analysis.COL_AGE) or "").strip() in selected_ages]
        if not rows:
            continue
        p1 = analysis._aggregate(rows, ds.periods[0])
        p2 = analysis._aggregate(rows, ds.periods[1])
        groups.setdefault(aset, []).append({
            "name": name,
            "media_type": media_types.get(name),
            "delivery": entry["delivery"],
            "p1": p1, "p2": p2,
        })
    return groups


# --------------------------------------------------------------------------- #
# Per-creative notes (the "distributed performance" explanation)
# --------------------------------------------------------------------------- #
def _concentration_note(ad_name: str, members: list[dict]) -> str:
    n = len(members)
    if n < 2:
        return ""
    total_imp = sum((m["p1"].get("imp") or 0) for m in members)
    if total_imp <= 0:
        return ""
    me = next((m for m in members if m["name"] == ad_name), None)
    if me is None:
        return ""
    my_imp = me["p1"].get("imp") or 0
    share = my_imp / total_imp
    dominant = max(members, key=lambda m: (m["p1"].get("imp") or 0))
    starved = [m for m in members if m["name"] != ad_name and (m["p1"].get("imp") or 0) / total_imp <= 0.20]
    if dominant["name"] == ad_name and share >= 0.50 and starved:
        others = n - 1
        return (f"Shared its ad set with {others} other ad{'s' if others != 1 else ''} and soaked up "
                f"{_pct(my_imp, total_imp)}% of the set's impressions — the rest were starved, so they may "
                f"look weaker than the creative really is.")
    if share <= 0.25:
        return (f"Got only {_pct(my_imp, total_imp)}% of its ad set's impressions — “{dominant['name']}” "
                f"took most of the budget, so soft numbers here may be starvation, not the creative.")
    return ""


def _lookalike_note(ad_name: str, my_cat: str, my_set: str, labels: dict, ad_index: dict) -> str:
    me = labels.get(ad_name)
    if not me:
        return ""
    my_traits = {f: (me.get(f) or "").strip().lower() for f in _LOOKALIKE_FIELDS}
    my_traits = {f: v for f, v in my_traits.items() if v and v not in ("unclear", "none")}
    if len(my_traits) < _MIN_SHARED_TRAITS:
        return ""
    strong = weak = 0
    shared_example = []
    for other, olab in labels.items():
        if other == ad_name:
            continue
        oinfo = ad_index.get(other)
        if not oinfo or (oinfo.get("ad_set") or "") == my_set:
            continue  # same set or not classified — skip
        shared = [f for f, v in my_traits.items() if (olab.get(f) or "").strip().lower() == v]
        if len(shared) < _MIN_SHARED_TRAITS:
            continue
        ocat = oinfo.get("cat")
        if ocat in _STRONG_CATS:
            strong += 1
        elif ocat in _WEAK_CATS:
            weak += 1
            if not shared_example:
                shared_example = shared[:2]
        else:
            continue
    total = strong + weak
    if total < 2:
        return ""
    trait_words = " and ".join(_words(my_traits[f]) for f in (shared_example or list(my_traits)[:2]))
    if weak >= max(2, strong * 2) and my_cat in _STRONG_CATS:
        return (f"Look-alike ads in other sets (same {trait_words}) mostly underperformed — its edge here "
                f"may owe more to the set than to the creative.")
    if strong >= max(2, weak * 2) and my_cat in _WEAK_CATS:
        return (f"This same look ({trait_words}) is winning in other sets — worth giving this one its own "
                f"set or more budget rather than writing it off.")
    if strong >= 2 and my_cat in _STRONG_CATS:
        return f"This look ({trait_words}) wins across sets too, so the formula travels."
    return ""


def _set_note(ad_name: str, my_cat: str, my_set: str, set_members: dict, labels: dict, ad_index: dict) -> str:
    members = set_members.get(my_set, [])
    parts = []
    c = _concentration_note(ad_name, members)
    if c:
        parts.append(c)
    la = _lookalike_note(ad_name, my_cat, my_set, labels, ad_index)
    if la:
        parts.append(la)
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# Per-ad-set view
# --------------------------------------------------------------------------- #
def _carrier_trait(name: str, labels: dict) -> str:
    lab = labels.get(name)
    if not lab:
        return ""
    arch = _words(lab.get("creative_archetype"))
    msg = _words(lab.get("message_type"))
    bits = [b for b in (arch, msg) if b and b not in ("unclear", "none")]
    return (", a " + " ".join(bits) + " creative") if bits else ""


def _set_read(label: str, carrier: dict, members: list[dict], labels: dict) -> str:
    n = len(members)
    cname = carrier["name"]
    media = carrier.get("media_type") or "creative"
    trait = _carrier_trait(cname, labels)
    total_res = sum((m["p1"].get("results") or 0) for m in members)
    total_imp = sum((m["p1"].get("imp") or 0) for m in members)
    cres = carrier["p1"].get("results") or 0
    cimp = carrier["p1"].get("imp") or 0
    if total_res > 0:
        share = _pct(cres, total_res)
        drove = f"delivered {share}% of the set's results"
    else:
        share = _pct(cimp, total_imp)
        drove = f"took {share}% of the set's impressions"
    if n == 1:
        return f"A single-ad set — all of it is “{cname}” ({media}{trait})."
    return (f"Carried by “{cname}” ({media}{trait}), which {drove} across {n} ads in the set.")


def _categorise_sets(set_rows: list[dict], pct: float) -> None:
    imps = [s["p1"]["imp"] for s in set_rows]
    ctrs = [s["p1"]["ctr"] for s in set_rows]
    cpas = [s["p1"]["cpa"] for s in set_rows]
    cut_imp = analysis._percentile([v for v in imps if v is not None], pct)
    cut_ctr = analysis._percentile([v for v in ctrs if v is not None], pct)
    cut_cpa = analysis._percentile([v for v in cpas if v is not None], pct)
    for s in set_rows:
        m = s["p1"]
        imp_high = m["imp"] is not None and cut_imp is not None and m["imp"] > cut_imp
        ctr_high = m["ctr"] is not None and cut_ctr is not None and m["ctr"] > cut_ctr
        # same neutral-when-results-pending rule as the per-creative view.
        cat, pending = analysis.classify(imp_high, ctr_high, m["cpa"], cut_cpa, m.get("spend"))
        s["category"] = cat
        s["pending_results"] = pending


def set_view(client: str, ds, filters: dict, display: dict, age_selection,
             media_types: dict, labels: dict) -> dict:
    if not getattr(ds, "ad_set_available", False):
        return {"available": False}
    selected_ages = _selected_ages(ds, age_selection)
    delivery_sel = {d.lower() for d in (filters.get("delivery") or [])}
    groups = _group_by_set(ds, selected_ages, delivery_sel, media_types)
    if not groups:
        return {"available": False}

    set_rows = []
    for set_name, members in groups.items():
        members = sorted(members, key=lambda m: (m["p1"].get("results") or 0, m["p1"].get("imp") or 0), reverse=True)
        p1 = _set_metrics(members, "p1")
        p2 = _set_metrics(members, "p2")
        carrier = members[0]
        total_res = sum((m["p1"].get("results") or 0) for m in members)
        total_imp = sum((m["p1"].get("imp") or 0) for m in members)
        mix = {"image": 0, "video": 0}
        member_rows = []
        for m in members:
            mt = m.get("media_type")
            if mt in mix:
                mix[mt] += 1
            denom_res = total_res if total_res > 0 else None
            denom_imp = total_imp if total_imp > 0 else None
            member_rows.append({
                "name": m["name"],
                "media_type": mt,
                "p1": analysis._round_metrics(m["p1"]),
                "share_results": _pct(m["p1"].get("results") or 0, denom_res) if denom_res else None,
                "share_imp": _pct(m["p1"].get("imp") or 0, denom_imp) if denom_imp else None,
                "creative_url": creatives.creative_url(client, m["name"]) if client else None,
                "creative_type": mt,
                "creative_poster": creatives.poster_url(client, m["name"]) if client else None,
            })
        set_rows.append({
            "set_name": set_name,
            "n_ads": len(members),
            "media_mix": mix,
            "p1": p1, "p2": p2,
            "delta": analysis._deltas(p1, p2),
            "carrier": {"name": carrier["name"], "media_type": carrier.get("media_type")},
            "read": _set_read(set_name, carrier, members, labels),
            "trend": analysis._build_trend({"p1": p1, "p2": p2}),
            "members": member_rows,
            "p1_round": analysis._round_metrics(p1),
            "p2_round": analysis._round_metrics(p2),
        })

    pct = float(display.get("percentile") or 50)
    _categorise_sets(set_rows, pct)

    # account-style benchmark across sets (>=1 impression)
    bctr = [s["p1"]["ctr"] for s in set_rows if s["p1"]["imp"] and s["p1"]["ctr"] is not None]
    bcpa = [s["p1"]["cpa"] for s in set_rows if s["p1"]["imp"] and s["p1"]["cpa"] is not None]
    benchmark = {"ctr": analysis._r(analysis._mean(bctr), 2), "cpa": analysis._r(analysis._mean(bcpa), 2),
                 "n": len(set_rows)}

    by_cat = {k: [] for k, _, _ in analysis.CATEGORY_META}
    for s in set_rows:
        by_cat[s["category"]].append(s)
    categories = []
    for key, label, desc in analysis.CATEGORY_META:
        rows = sorted(by_cat[key], key=lambda s: (s["p1"].get("results") or 0, s["p1"].get("imp") or 0), reverse=True)
        for s in rows:  # round the headline metrics for display
            s["p1"] = s.pop("p1_round")
            s["p2"] = s.pop("p2_round")
        categories.append({
            "key": key, "label": label, "description": desc,
            "count": len(rows), "sets": rows,
        })

    return {
        "available": True,
        "percentile": pct,
        "count": len(set_rows),
        "benchmark": benchmark,
        "categories": categories,
    }


# --------------------------------------------------------------------------- #
# Orchestration — mutate the analyze() result in place.
# --------------------------------------------------------------------------- #
def enrich(client: str, ds, result: dict, filters: dict, display: dict,
           age_selection, media_types: dict, labels: dict) -> dict:
    """Add `set_note` to every shown ad row and attach `adset_view`."""
    if not getattr(ds, "ad_set_available", False):
        result["adset_view"] = {"available": False}
        return result

    selected_ages = _selected_ages(ds, age_selection)
    delivery_sel = {d.lower() for d in (filters.get("delivery") or [])}
    set_members = _group_by_set(ds, selected_ages, delivery_sel, media_types)
    ad_index = result.get("ad_index", {})

    for cat in result.get("categories", []):
        cat_key = cat.get("key")
        for ad in cat.get("ads", []):
            my_set = (ad.get("ad_set") or "").strip()
            if not my_set:
                continue
            note = _set_note(ad.get("ad_name", ""), cat_key, my_set, set_members, labels, ad_index)
            if note:
                ad["set_note"] = note

    result["adset_view"] = set_view(client, ds, filters, display, age_selection, media_types, labels)
    return result
