"""
Creative metadata engine — read each creative image into a curated subset of the
creative-schema (engine/creative-schema_20260625.csv), via Claude vision.

We use the robust visual/strategic fields (not the full ~40), because Tab 1 gives
us small ~128px Meta table thumbnails; text fields are best-effort. Results are
cached per creative (by normalized ad name) under data/metadata/<client>/.
"""
from __future__ import annotations

import hashlib
import json

import creatives
import llm
from views import DATA_DIR

METADATA_DIR = DATA_DIR / "metadata"

# (field, definition, options) — categorical fields drive the formula + parts.
FIELDS = [
    ("format", "Creative format", ["static_image", "carousel_frame", "screenshot", "graphic_card", "video_frame", "display_banner", "other"]),
    ("aspect_ratio", "Observed aspect ratio", ["9:16", "4:5", "1:1", "16:9", "other"]),
    ("primary_subject", "Most visually dominant subject", ["product", "person", "text", "environment", "object/prop", "before_after", "mixed"]),
    ("human_presence", "Human visibility", ["none", "face", "hands_only", "body_no_face", "full_body", "multiple_people"]),
    ("setting", "Setting / background", ["studio", "home", "kitchen", "bathroom", "car", "office", "outdoor", "retail", "gym", "abstract", "unclear"]),
    ("visual_polish", "Production feel", ["raw", "realistic_polished", "polished", "studio", "artificial/AI-looking"]),
    ("color_temperature", "Overall color temperature", ["warm", "cool", "neutral", "mixed"]),
    ("saturation", "Overall saturation", ["muted", "natural", "vibrant", "neon"]),
    ("text_density", "Amount of on-image text", ["none", "low", "medium", "high"]),
    ("message_type", "Primary message strategy", ["benefit_led", "problem_solution", "offer_led", "social_proof", "educational", "comparison", "testimonial", "routine_integration", "authority_led", "urgency_led"]),
    ("benefit_type", "Main benefit category", ["save_money", "save_time", "convenience", "quality", "performance", "beauty", "health/wellness", "safety/security", "status", "entertainment", "other"]),
    ("proof_type", "Main proof mechanism", ["none", "number", "review/rating", "testimonial", "customer_count", "expert", "certification", "demo", "before_after", "comparison"]),
    ("offer_mechanic", "Commercial offer mechanic", ["none", "discount", "free_trial", "free_quote", "bundle", "subscription", "limited_time", "guarantee", "price_anchor", "other"]),
    ("creative_archetype", "Broad creative concept", ["product_hero", "UGC_testimonial", "talking_head", "comedy_skit", "day_in_life", "routine/GRWM", "demo/how_to", "myth_busting", "founder_story", "expert_explainer", "meme/trend", "comparison", "before_after", "graphic_card"]),
    ("aesthetic_style", "Visual style", ["raw_UGC", "polished_UGC", "clean_wellness", "clinical", "cozy_home", "mainstream_retail", "luxury", "minimalist", "colorful_playful", "editorial", "corporate", "meme_native"]),
    ("primary_emotion", "Likely main emotion elicited", ["trust", "calm", "curiosity", "aspiration", "relief", "urgency", "confidence", "desire", "comfort", "fear/anxiety", "belonging", "humor"]),
    ("trust_cue", "Main trust signal", ["none", "human_face", "expert", "certification", "review", "customer_count", "demo", "brand_familiarity", "realism", "data/number"]),
]
TEXT_FIELDS = [
    ("headline", "Largest / primary headline text, transcribed VERBATIM word-for-word (empty if none)"),
    ("sub_headline", "Secondary / supporting headline text, verbatim (empty if none)"),
    ("subtext", "Smaller body, caption, price, label or fine-print text — transcribe verbatim and combine into one string (empty if none)"),
    ("cta_text_exact", "Button / call-to-action text, verbatim (empty if none)"),
    ("visual_description", "2-3 sentences describing ONLY what is visibly shown: the main subject(s), any people (and what they are doing), setting/background, layout, and key colors. Do NOT name the brand or company. Do NOT mention pixel dimensions or aspect ratio."),
    ("core_claim", "One plain-language sentence summarizing what the ad claims"),
    ("dominant_color_1_hex", "Most dominant color as #RRGGBB"),
    ("dominant_color_2_hex", "Second dominant color as #RRGGBB"),
]

FIELD_OPTIONS = {name: opts for name, _d, opts in FIELDS}

VISION_SYSTEM = (
    "You are a precise advertising-creative analyst. You look at ONE ad creative and label its "
    "visible attributes factually. Never guess performance; only describe what is visible. The "
    "image may be a small thumbnail or a video poster frame."
)


def _schema() -> dict:
    props = {}
    for name, definition, options in FIELDS:
        props[name] = {"type": "string", "description": definition + f". One of: {', '.join(options)}."}
    for name, definition in TEXT_FIELDS:
        props[name] = {"type": "string", "description": definition}
    return {"type": "object", "properties": props, "required": [f[0] for f in FIELDS]}


def _instructions(ad_type: str) -> str:
    head = (
        f"Label this single {'video (poster frame)' if ad_type == 'video' else 'image'} ad creative. "
        "Pick the single best value per enum field; use 'unclear'/'none' only if you truly cannot tell. "
        "LOOK CAREFULLY at the whole image before the visual fields: if a product, vehicle, person, or "
        "hands/body part is visible, do NOT label it 'none' — name the actual subject. Transcribe text "
        "fields VERBATIM, word-for-word, including prices, labels and fine print."
    )
    lines = [head, "", "FIELDS:"]
    for name, definition, options in FIELDS:
        lines.append(f"- {name}: {definition} — one of: {', '.join(options)}")
    for name, definition in TEXT_FIELDS:
        lines.append(f"- {name}: {definition}")
    return "\n".join(lines)


def analyze_image(img_bytes: bytes, media_type: str = "image/jpeg", ad_type: str = "image") -> dict:
    return llm.vision_json(img_bytes, VISION_SYSTEM, _instructions(ad_type), _schema(),
                           media_type=media_type, tool_name="emit_labels")


def _meta_path(client: str, ad_name: str):
    safe = creatives._client_safe(client)
    h = hashlib.sha1(creatives.normalize(ad_name).encode("utf-8")).hexdigest()[:16]
    return METADATA_DIR / safe / f"{h}.json"


def _video_meta_path(client: str, ad_name: str):
    p = _meta_path(client, ad_name)
    return p.parent / (p.stem + ".video.json")


def get_video_meta(client: str, ad_name: str):
    """The multi-frame video dimensions (format/duration/setting/characters/scenes/...),
    or None for images / un-analyzed videos."""
    p = _video_meta_path(client, ad_name)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("video")
        except Exception:
            return None
    return None


def cached_labels(client: str, ad_names) -> dict:
    """Read already-cached labels for these ads — NEVER calls the LLM. Returns
    {ad_name: labels} for the subset that has a metadata file. Used by Tab 1's
    ad-set enrichment to spot look-alike creatives without triggering a label run."""
    out = {}
    for nm in ad_names or []:
        p = _meta_path(client, nm)
        if p.is_file():
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
                if rec.get("labels"):
                    out[nm] = rec["labels"]
            except Exception:
                pass
    return out


def get_metadata(client: str, ad_name: str, ad_type: str = "image", force: bool = False):
    """Cached per creative. Returns {ad_name, labels} or None if no creative pulled."""
    p = _meta_path(client, ad_name)
    if p.is_file() and not force:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    img = creatives.creative_file(client, ad_name)
    if img is None:
        return None
    media_type = "image/png" if img.suffix.lower() == ".png" else "image/jpeg"
    labels = analyze_image(img.read_bytes(), media_type, ad_type)
    rec = {"ad_name": ad_name, "labels": labels}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return rec


# Live progress for the (slow) per-creative read, polled by the UI for a progress bar.
# Keyed by client; thread-safe enough for this single-user local helper (atomic dict ops).
_PROGRESS: dict = {}


def get_progress(client: str) -> dict:
    return _PROGRESS.get(client, {"done": 0, "total": 0, "active": False})


def analyze_many(client: str, ad_names: list[str], ad_type: str = "image", force: bool = False):
    """Analyze each named creative (cached); skip those without a pulled image.
    Returns ({ad_name: labels}, [status...]). Publishes progress to _PROGRESS[client]."""
    labels, status = {}, []
    _PROGRESS[client] = {"done": 0, "total": len(ad_names), "active": True}
    for name in ad_names:
        if creatives.creative_file(client, name) is None:
            status.append({"ad": name, "ok": False, "reason": "no creative pulled"})
        else:
            try:
                rec = get_metadata(client, name, ad_type, force)
                labels[name] = rec["labels"]
                status.append({"ad": name, "ok": True})
            except Exception as e:
                status.append({"ad": name, "ok": False, "reason": str(e)[:200]})
        _PROGRESS[client]["done"] += 1
    _PROGRESS[client]["active"] = False
    return labels, status
