"""Watch a downloaded video ad and produce its synopsis — the structured
breakdown the reviewer approves before any prompt is written.

Pipeline per ad folder:
  video.mp4 -> keyframes (ffmpeg) + transcript (faster-whisper, optional)
            -> multimodal synopsis via llm.frames_json
Everything is cached on disk next to the video so re-runs are instant.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import llm
import videotools

FRAME_COUNT = 8

SYNOPSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "One line: what this ad is and how it sells"},
        "style": {"type": "string", "description": "e.g. 'casual handheld UGC phone mashup', 'polished studio commercial'"},
        "aspect": {"type": "string"},
        "hook": {"type": "string", "description": "What the first ~3 seconds do to stop the scroll"},
        "tags": {"type": "object",
                 "description": "MECE creative classification along four independent axes. "
                                "Pick the SINGLE best value per axis; add a second only when the ad "
                                "genuinely blends two (e.g. ugc + studio). Axes are independent — a "
                                "ugc-format ad can still use a comparison angle.",
                 "properties": {
                     "format": {"type": "array",
                                "items": {"type": "string", "enum": ["ugc", "studio", "talking-head", "demo", "animation", "vo-montage"]},
                                "description": "How it's produced. ugc=creator/handheld/personal; studio=polished/controlled lighting; talking-head=a person addressing camera; demo=product-in-use/how-to/screen-recording; animation=motion graphics; vo-montage=narrated b-roll with no on-camera presenter."},
                     "angle": {"type": "array",
                               "items": {"type": "string", "enum": ["problem-solution", "transformation", "comparison", "testimonial", "educational", "offer", "founder-story", "listicle"]},
                               "description": "The core persuasion strategy. transformation=before/after or time-based journey; comparison=us-vs-them/vs the category; testimonial=personal endorsement/social proof; educational=teaches a mechanism/ingredient; offer=discount/urgency-led; listicle=multiple reasons or use-cases."},
                     "hook": {"type": "array",
                              "items": {"type": "string", "enum": ["outcome-led", "problem-callout", "bold-claim", "question", "pattern-interrupt", "curiosity"]},
                              "description": "What the first ~3 seconds do. Usually exactly one."},
                     "focus": {"type": "array",
                               "items": {"type": "string", "enum": ["product-hero", "lifestyle", "ingredient-tech", "results-proof", "persona", "price-value"]},
                               "description": "What the creative visually centers on."}},
                 "required": ["format", "angle", "hook", "focus"]},
        "beats": {"type": "array", "items": {"type": "object", "properties": {
            "start": {"type": "number"}, "end": {"type": "number"},
            "location": {"type": "string"},
            "visual": {"type": "string", "description": "Concrete visual language: framing, the one action, product orientation"},
            "vo_line": {"type": "string", "description": "Voiceover line over this beat, if any"}},
            "required": ["start", "end", "visual"]}},
        "voiceover": {"type": "object", "properties": {
            "present": {"type": "boolean"},
            "style": {"type": "string"},
            "script": {"type": "array", "items": {"type": "string"}}},
            "required": ["present"]},
        "on_screen_text": {"type": "array", "items": {"type": "string"},
                           "description": "Every piece of burned-in text visible in the frames, verbatim"},
        "faces_present": {"type": "boolean"},
        "products_shown": {"type": "string"},
        "claims": {"type": "array", "items": {"type": "string"},
                   "description": "Every product/benefit claim made, verbatim where possible"},
        "sounds": {"type": "string", "description": "Music / natural sound / silence, best guess from the transcript"},
        "why_it_works": {"type": "string"},
        "recreation_notes": {"type": "array", "items": {"type": "string"},
                             "description": "Face moments needing face-free replacements, claims that look non-compliant, format quirks"},
        "gate": {"type": "object", "properties": {
            "category": {"type": "string",
                         "enum": ["standard", "promotion", "event", "package_deal"],
                         "description": "standard = a regular product ad worth recreating. "
                                        "promotion = the ad's core message is a discount/sale/promo code/limited offer. "
                                        "event = the ad is tied to a specific event, season or holiday (Black Friday, New Year, Mother's Day, an expo/launch event). "
                                        "package_deal = the core offer is a bundle/multipack/package deal rather than the product itself. "
                                        "Showing packaging or mentioning a promo in passing does NOT gate an otherwise standard product ad — gate only when the promo/event/bundle IS the concept."},
            "reason": {"type": "string", "description": "One plain sentence explaining the call, quoting the ad where useful"}},
            "required": ["category", "reason"]},
    },
    "required": ["headline", "style", "hook", "tags", "beats", "voiceover", "on_screen_text",
                 "faces_present", "claims", "recreation_notes", "gate"],
}

_SYSTEM = """You are a senior creative strategist reverse-engineering a competitor's
Meta video ad so it can be recreated (rebranded) as an AI-generated video. You are
given evenly-spaced keyframes in order, the audio transcript (when available), and
the raw text of the Ad Library card. Reconstruct the ad faithfully: its beat
structure with timestamps, camera/style language, voiceover script, every on-screen
text, and every claim. Be concrete and visual; a director should be able to reshoot
the ad from your beats alone. In recreation_notes, flag every face moment (these
must be replaced with face-free beats), every claim that smells non-compliant for a
supplements/food brand, and anything else the recreation must adapt.

Classify the ad in `tags` along the four independent axes in the schema — format
(how it's shot), angle (the argument it makes), hook (what the first ~3s do), and
focus (what it centers on) — using ONLY the allowed enum values. Choose the single
best value per axis; add a second only when the ad genuinely blends two.

Finally, triage the ad in `gate`: ads whose whole concept is a promotion, a
specific event/season, or a package/bundle deal are sorted out of the recreation
pipeline (category promotion/event/package_deal); everything else is standard.
Be conservative — a passing promo mention or visible packaging does not gate a
standard product ad."""

_whisper_model = None
_whisper_lock = threading.Lock()


def _transcribe(wav_path: Path) -> str | None:
    global _whisper_model
    try:
        from faster_whisper import WhisperModel
    except Exception:
        return None
    try:
        with _whisper_lock:
            if _whisper_model is None:
                _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
            segments, _info = _whisper_model.transcribe(str(wav_path), vad_filter=True)
            lines = []
            for seg in segments:
                lines.append(f"[{seg.start:6.2f}-{seg.end:6.2f}] {seg.text.strip()}")
        return "\n".join(lines)
    except Exception:
        return None


def prepare_media(ad_dir: Path, progress=None) -> dict:
    """Frames, poster, duration, transcript — all cached on disk."""
    notify = progress or (lambda s: None)
    ad_dir = Path(ad_dir)
    video = ad_dir / "video.mp4"
    out = {"frames": [], "transcript": None, "duration": None}
    if not video.is_file():
        return out

    out["duration"] = videotools.duration_seconds(video)

    notify("Pulling keyframes from the video…")
    frames_dir = ad_dir / "frames"
    frames = sorted(frames_dir.glob("frame_*.jpg"))
    if not frames:
        frames = videotools.extract_frames(video, frames_dir, FRAME_COUNT)
    out["frames"] = [str(f) for f in frames]

    poster = ad_dir / "poster.jpg"
    if not poster.is_file():
        videotools.make_poster(video, poster)

    tpath = ad_dir / "transcript.txt"
    if tpath.is_file():
        out["transcript"] = tpath.read_text().strip() or None
    else:
        notify("Listening to the audio and transcribing it…")
        wav = ad_dir / "audio.wav"
        if videotools.extract_audio(video, wav):
            text = _transcribe(wav)
            tpath.write_text(text or "")
            out["transcript"] = text
            try:
                wav.unlink()
            except OSError:
                pass
        else:
            tpath.write_text("")
    return out


def analyze(ad_dir: Path, progress=None) -> dict:
    """Produce (and cache) synopsis.json for one downloaded ad. `progress`
    (optional) receives one-line stage descriptions as the work advances."""
    notify = progress or (lambda s: None)
    ad_dir = Path(ad_dir)
    media = prepare_media(ad_dir, progress=progress)
    if not media["frames"]:
        raise RuntimeError("could not extract keyframes (is ffmpeg available?)")

    ad_meta = {}
    try:
        ad_meta = json.loads((ad_dir / "ad.json").read_text())
    except Exception:
        pass

    dur = media["duration"]
    transcript = media["transcript"]
    instructions = (
        f"Video duration: {dur:.1f} seconds.\n\n" if dur else ""
    ) + (
        "AUDIO TRANSCRIPT (timestamped):\n" + transcript + "\n\n"
        if transcript else
        "AUDIO TRANSCRIPT: unavailable — if the frames suggest a talking/voiceover ad, "
        "say so in voiceover.style and leave the script empty; the reviewer will add it.\n\n"
    ) + (
        "AD LIBRARY CARD TEXT (raw, includes the ad's primary text):\n"
        + ad_meta.get("card_text", "(none)")
        + "\n\nNow produce the synopsis object."
    )

    notify("Watching the frames and writing the scene-by-scene breakdown…")
    synopsis = llm.frames_json(media["frames"], _SYSTEM, instructions,
                               SYNOPSIS_SCHEMA, timeout=420)
    synopsis["_analyzed_at"] = datetime.now(timezone.utc).isoformat()
    if dur:
        synopsis["_duration_seconds"] = round(dur, 1)
    (ad_dir / "synopsis.json").write_text(json.dumps(synopsis, indent=2))
    return synopsis
