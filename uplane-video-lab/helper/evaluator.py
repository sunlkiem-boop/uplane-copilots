"""Eval gate — QA a generated clip BEFORE it can be finalized.

After a clip's mp4 lands, frames are extracted and scored by the LLM against
the clip's own assembled prompt — which contains, verbatim, the same LOCKED
blocks that govern generation (product physical spec + exact label text,
product staging rules, anti-AI rules, person realism). The checklist is
therefore derived from the locked blocks by construction: one edit to a saved
segment changes both the prompt and what the eval enforces.

Hard gate: a clip with failed items cannot be finalized unless the reviewer
explicitly overrides ("accept anyway"). Every failure comes with a drafted
remedy — re-roll (with the exact prompt fix), hide (reframe/remove), or fix in
post — but nothing regenerates until the reviewer clicks it.
"""
from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

import brandkit
import generation
import llm
import prompt_engine
import videotools

FRAME_COUNT = 10

EVAL_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string",
                    "description": "2-3 sentences: what the clip shows and the overall QA impression."},
        "items": {"type": "array", "minItems": 4, "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "short-kebab-slug, unique per item"},
                "category": {"type": "string",
                             "enum": ["product-spec", "label", "staging", "anti-ai", "person", "beats"]},
                "rule": {"type": "string",
                         "description": "The specific locked rule / prompt requirement being checked (short quote or tight paraphrase)"},
                "verdict": {"type": "string", "enum": ["pass", "fail", "unsure"]},
                "frames": {"type": "array", "items": {"type": "integer"},
                           "description": "1-based frame numbers that are the evidence (offending frames for fail, representative for pass)"},
                "note": {"type": "string",
                         "description": "What was actually seen, concretely; for fail, exactly what is wrong"},
            },
            "required": ["id", "category", "rule", "verdict", "frames", "note"]}},
        "fixes": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": "the failed item this remedy addresses"},
                "route": {"type": "string", "enum": ["reroll", "hide", "post"],
                          "description": "reroll = regenerate with a prompt fix; hide = reframe/remove the uncontrollable element (also a reroll, but by removing); post = fixable in editing (depolish, composite, caption) without burning credits"},
                "detail": {"type": "string", "description": "The remedy in plain words for the reviewer"},
                "prompt_fix": {"type": "string",
                               "description": "For reroll/hide: the EXACT text to append to the clip's prompt so this failure cannot repeat. Empty for post."},
            },
            "required": ["item_id", "route", "detail", "prompt_fix"]}},
    },
    "required": ["summary", "items", "fixes"],
}

_SYSTEM = """You are the Uplane Video Lab QA gate — an adversarial inspector of
AI-generated ad clips. You are shown evenly-spaced frames (in order) from ONE
generated clip, plus the exact prompt that produced it. The prompt ends with
LOCKED blocks (product physical spec, exact on-pack label text, product staging
rules, strict anti-AI rules) — those locked blocks ARE your checklist. Derive
one checklist item per locked rule that could plausibly be violated by what
this clip depicts, plus items for: label text fidelity, the clip's stated BEATS
actually happening, and (if a person is present) skin/person realism.

Be adversarial: assume a rule is violated until the frames convince you
otherwise. Shallow passes have shipped real failures before (pills appearing to
spill from a sealed bottle passed a lazy check). Look closely at: caps and
closures, container shape/proportions/colour, label spelling, duplicated or
warped products, branding bleeding onto other objects, hands and fingers,
on-screen text, lighting (over-saturated / blown-out / excessive contrast),
AI-smooth skin, floating or morphing objects, and frame-to-frame consistency of
the product. Skip rules that cannot apply (e.g. pill staging when no pills
appear; handheld shake when the style is locked-off studio) — do not pad the
list. 'unsure' means the frames genuinely cannot settle it (e.g. motion blur);
use it honestly, not as a soft pass.

COUNTING PROTOCOL (mandatory): whenever the spec fixes an EXACT count of anything
visible (pills, softgels, product units, beams, spokes, tiles, fingers), you must
literally COUNT the instances in each frame where they appear and STATE the
counted number in the item's note (e.g. "frame 7: 4 green softgels + 1 capsule =
5 pieces; spec is 3+1=4"). An off-by-one IS a fail. Never mark a count item
'pass' without a stated per-frame number — an uncounted count is 'unsure', not
'pass'. A reviewer caught a 4-for-3 softgel miss that a previous eval passed;
that must not happen again.

For every FAIL, draft ONE remedy in fixes[]. Prefer the cheapest route that
truly fixes it: 'post' when editing can solve it (grade/depolish, caption,
composite a real photo); 'hide' when the element is something the generator
cannot control (exact counts, fine print, unreferenced surfaces) and should be
reframed away or removed; 'reroll' when the clip must be regenerated with a
sharpened prompt — then write the exact prompt_fix text. Re-rolls cost real
credits; never recommend one for something post can fix."""


# --------------------------------------------------------------------------- #
# Core evaluation
# --------------------------------------------------------------------------- #
def _clip_video(pkg_dir: Path, meta: dict, index: int) -> Path | None:
    c = next((c for c in meta.get("clips", []) if c.get("index") == index), None)
    for name in ([(c or {}).get("output")] if c else []) + [f"clip-{index + 1}.mp4", "output.mp4"]:
        if name and (pkg_dir / Path(name).name).is_file():
            return pkg_dir / Path(name).name
    return None


def eval_path(pkg_dir: Path, index: int) -> Path:
    return pkg_dir / f"eval-clip-{index + 1}.json"


def frames_dir(pkg_dir: Path, index: int) -> Path:
    return pkg_dir / f"eval-frames-clip-{index + 1}"


def evaluate_clip(package: str, index: int) -> dict:
    """Extract frames from a generated clip and score them against the clip's
    assembled prompt (which carries the locked blocks verbatim). Writes
    eval-clip-N.json into the package and rolls the verdict into meta.json."""
    pkg_dir = generation.HANDOFF_DIR / Path(package).name
    meta = json.loads((pkg_dir / "meta.json").read_text())
    video = _clip_video(pkg_dir, meta, index)
    if not video:
        raise RuntimeError(f"clip {index + 1} has no output video to evaluate")

    fdir = frames_dir(pkg_dir, index)
    if fdir.is_dir():
        shutil.rmtree(fdir, ignore_errors=True)
    frames = videotools.extract_frames(video, fdir, FRAME_COUNT)
    if not frames:
        raise RuntimeError("could not extract frames (is ffmpeg available?)")

    prompt_json = json.loads((pkg_dir / "prompt.json").read_text())
    clips = prompt_json.get("clips") or []
    clip = clips[index] if index < len(clips) else {}
    seg = clip.get("segment_prompt", "")

    instructions = (
        f"======== THE CLIP UNDER REVIEW ========\n"
        f"Package: {meta.get('package')} — clip {index + 1} of {len(clips) or 1} "
        f"({clip.get('label', '')}), target {clip.get('duration_seconds')}s, "
        f"faces: {clip.get('faces', 'none')}.\n"
        f"You are shown {len(frames)} evenly-spaced frames, in order.\n\n"
        "======== THE EXACT PROMPT THAT PRODUCED IT (the locked blocks at the "
        "end are your checklist) ========\n" + seg
        + "\n\nNow inspect the frames and emit the eval object."
    )
    result = llm.frames_json([str(f) for f in frames], _SYSTEM, instructions,
                             EVAL_SCHEMA, timeout=600)

    fails = [i for i in result.get("items", []) if i.get("verdict") == "fail"]
    unsure = [i for i in result.get("items", []) if i.get("verdict") == "unsure"]
    verdict = "failed" if fails else "passed"
    result["_verdict"] = verdict
    result["_clip_index"] = index
    result["_frame_count"] = len(frames)
    result["_evaluated_at"] = datetime.now(timezone.utc).isoformat()
    eval_path(pkg_dir, index).write_text(json.dumps(result, indent=2))

    set_clip_eval(package, index, {"status": verdict, "fails": len(fails),
                                   "unsure": len(unsure),
                                   "at": result["_evaluated_at"]})
    return result


def evaluate_clip_async(package: str, index: int) -> None:
    """Fire-and-forget eval in a background thread (the house pattern)."""
    set_clip_eval(package, index, {"status": "running"})

    def _run():
        try:
            evaluate_clip(package, index)
        except Exception as e:
            set_clip_eval(package, index, {"status": "error", "error": str(e)})

    threading.Thread(target=_run, daemon=True).start()


# --------------------------------------------------------------------------- #
# Meta/queue bookkeeping + the gate
# --------------------------------------------------------------------------- #
def set_clip_eval(package: str, index: int, ev: dict) -> dict:
    """Write one clip's eval summary into meta.json + queue.json and re-roll
    up the package status through the gate."""
    pkg_dir = generation.HANDOFF_DIR / Path(package).name
    meta_path = pkg_dir / "meta.json"
    if not meta_path.is_file():
        return {}
    meta = json.loads(meta_path.read_text())
    for c in meta.get("clips", []):
        if c.get("index") == index:
            c["eval"] = ev
    meta["status"] = generation._gate_rollup(meta.get("clips") or [],
                                             meta.get("status", "queued"))
    meta_path.write_text(json.dumps(meta, indent=2))
    queue = generation._load_queue()
    for entry in queue:
        if entry.get("package") == Path(package).name:
            entry["status"] = meta["status"]
            entry["clips"] = meta.get("clips", [])
    generation.QUEUE_FILE.write_text(json.dumps(queue, indent=2))
    return meta


def override_clip(package: str, index: int, note: str = "") -> dict:
    """Reviewer's explicit 'accept anyway' — the only way past a failed eval."""
    ev = {"status": "overridden", "at": datetime.now(timezone.utc).isoformat()}
    if note.strip():
        ev["note"] = note.strip()
    return set_clip_eval(package, index, ev)


def apply_fix(package: str, index: int, item_id: str) -> dict:
    """Apply a drafted reroll/hide fix: append it to the clip's prompt in the
    PACKAGE copy (prompt.json + prompt.md), reset the clip to 'requested' so a
    generation session re-rolls it. Reviewer-triggered only."""
    pkg_dir = generation.HANDOFF_DIR / Path(package).name
    ev_file = eval_path(pkg_dir, index)
    if not ev_file.is_file():
        raise RuntimeError("no eval on file for this clip")
    ev = json.loads(ev_file.read_text())
    fix = next((f for f in ev.get("fixes", []) if f.get("item_id") == item_id), None)
    if not fix:
        raise RuntimeError(f"no drafted fix for item '{item_id}'")
    if fix.get("route") == "post":
        raise RuntimeError("that remedy is a post-production fix — nothing to re-roll")
    text = (fix.get("prompt_fix") or fix.get("detail") or "").strip()
    if not text:
        raise RuntimeError("the drafted fix has no prompt text")

    prompt_file = pkg_dir / "prompt.json"
    prompt_json = json.loads(prompt_file.read_text())
    clips = prompt_json.get("clips") or []
    if index >= len(clips):
        raise RuntimeError("clip index out of range")
    item = next((i for i in ev.get("items", []) if i.get("id") == item_id), {})
    clips[index]["segment_prompt"] = (
        clips[index].get("segment_prompt", "").rstrip()
        + "\n\n──────── REVISION — QA FIX (this take MUST hold this; it failed "
          "last take) ────────\n"
        + (f"FAILED: {item.get('note')}\n" if item.get("note") else "")
        + text)
    prompt_file.write_text(json.dumps(prompt_json, indent=2))
    try:
        kit = brandkit.load(json.loads((pkg_dir / "meta.json").read_text()).get("kit", ""))
        (pkg_dir / "prompt.md").write_text(prompt_engine.render_md(prompt_json, kit))
    except Exception:
        pass

    meta = generation.update_clip_status(package, index, "requested", output="")
    set_clip_eval(package, index, {"status": "superseded",
                                   "applied_fix": item_id,
                                   "at": datetime.now(timezone.utc).isoformat()})
    return {"ok": True, "clip": index, "applied": item_id, "status": "requested"}
