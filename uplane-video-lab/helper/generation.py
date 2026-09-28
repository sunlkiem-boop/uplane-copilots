"""Generation handoff — the hybrid adapter.

Approved prompts become self-contained packages in data/handoff/. Today,
generation runs through the Higgsfield connector in a Claude session (Claude
Code or Cowork): open the handoff folder and follow GENERATE.md. Later, a
direct Higgsfield API adapter can implement `GenerationAdapter` and the rest
of the product doesn't change.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import brandkit

HANDOFF_DIR = brandkit.APP_ROOT / "data" / "handoff"
QUEUE_FILE = HANDOFF_DIR / "queue.json"

GENERATE_MD = """# Generating the queued videos (Higgsfield connector)

Each subfolder here is one approved package:

    prompt.md / prompt.json   the clip prompts (one "## Clip" section each)
    reference-N.*             product reference image(s) — attach to EVERY generation
    clip-1-still.jpg          appears after clip 1 — attach to clips 2+ (setting reference)
    source.jpg               a still of the competitor ad (context only)
    meta.json                settings + per-clip status in `clips[]`

Settings (from meta.json): Seedance 2 on Higgsfield, vertical 9:16, each clip < 15s.

## Per-clip protocol (a Claude session with the Higgsfield connector)

`meta.json.clips[]` tracks each clip: status pending → requested → generating → generated.
Generate clips whose status is `"requested"` (the reviewer clicked Generate on that clip),
or process the whole package in index order.

1. **Clip 1 (index 0) FIRST.** Attach only `reference-N.*`. Paste its "## Clip 1" prompt.
   Save the result into the package as `clip-1.mp4`, then mark it:
   `POST /api/handoff/<pkg>/clips/0/status {"status":"generated","output":"clip-1.mp4"}`
   The app then extracts `clip-1-still.jpg` from it.
2. **Clips 2, 3 …** Attach `reference-N.*` AND `clip-1-still.jpg` — match its setting,
   lighting and product look, but render the NEW shot the prompt asks for (the "SHOT
   LEDGER" line says which shot is which; never repeat another clip's shot). Save
   `clip-2.mp4`, `clip-3.mp4` …, and POST the same per-clip status update (index 1, 2 …).
3. The same product reference goes on every clip; a person/face appears in clip 1 only;
   clips 2+ are B-roll and must be DIFFERENT shots from one another.
4. **QA gate (automatic).** When you POST `"generated"` the app extracts frames and
   runs a QA eval against the prompt's locked blocks. The package stays in
   `"reviewing"` until every clip's eval passes or the reviewer overrides — you do
   NOT need to do anything for this. If the reviewer applies a QA fix, the clip
   flips back to `"requested"` and its prompt gains a `REVISION — QA FIX` block:
   re-generate it exactly like a normal requested clip (the revision text is part
   of the prompt now).
5. **Stitch** the clips in index order with hard cuts in editing — that's the final video.
6. If a render looks too clean/orange, run the de-polish pass in prompt.md's POST notes.

## For a future direct-API integration

Implement `GenerationAdapter.generate(package_dir)` — the queue format is the contract.
"""


class GenerationAdapter:
    """Contract for a future direct Higgsfield API integration."""

    def generate(self, package_dir: Path) -> dict:  # pragma: no cover
        raise NotImplementedError(
            "Direct API generation is not wired yet — use the Higgsfield "
            "connector flow in GENERATE.md.")


def _load_queue() -> list:
    if QUEUE_FILE.is_file():
        try:
            return json.loads(QUEUE_FILE.read_text())
        except Exception:
            return []
    return []


def queue_package(ad_dir: Path, kit_slug: str,
                  references: list | None = None) -> dict:
    """Freeze one approved ad into a self-contained handoff package.
    `references` picks specific image filenames from the kit's references/
    folder (attached to every generation, in order); default is the kit's
    main reference. One image gives the sharpest product fidelity."""
    ad_dir = Path(ad_dir)
    kit = brandkit.load(kit_slug)
    prompt_json = json.loads((ad_dir / "prompt.json").read_text())
    ad_meta = json.loads((ad_dir / "ad.json").read_text())

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    pkg_name = f"{stamp}-{kit_slug}-{ad_dir.name}"
    pkg = HANDOFF_DIR / pkg_name
    pkg.mkdir(parents=True, exist_ok=True)

    shutil.copy(ad_dir / "prompt.md", pkg / "prompt.md")
    shutil.copy(ad_dir / "prompt.json", pkg / "prompt.json")

    refs_dir = Path(kit["dir"]) / "references"
    chosen = []
    for name in (references or []):
        cand = refs_dir / Path(str(name)).name
        if cand.is_file() and str(cand) not in chosen:
            chosen.append(str(cand))
    if not chosen:
        hero = kit.get("reference_image_path")
        if hero and Path(hero).is_file():
            chosen = [hero]
    used = []
    for i, ref in enumerate(chosen, 1):
        dst = pkg / f"reference-{i}{Path(ref).suffix}"
        shutil.copy(ref, dst)
        used.append(Path(ref).name)
    if (ad_dir / "poster.jpg").is_file():
        shutil.copy(ad_dir / "poster.jpg", pkg / "source.jpg")

    clips = prompt_json.get("clips") or []
    if len(clips) > 1:
        total = round(sum(c.get("duration_seconds", 0) for c in clips))
        duration = f"{len(clips)} separate generations, ~{total}s total, stitch in editing"
    else:
        duration = "up to 15s, one generation"
    clips_meta = [{
        "index": i,
        "label": c.get("label") or f"Clip {i + 1}",
        "role": c.get("role") or ("hook" if i == 0 else "broll"),
        "duration_seconds": c.get("duration_seconds"),
        "status": "pending",     # pending -> requested -> generating -> generated
        "output": None,
    } for i, c in enumerate(clips)] or [{
        "index": 0, "label": "The video", "role": "hook",
        "duration_seconds": None, "status": "pending", "output": None}]
    meta = {
        "package": pkg_name,
        "ad_library_id": ad_dir.name,
        "source_ad_url": ad_meta.get("video_url", ""),
        "kit": kit_slug,
        "product": kit.get("product_name", ""),
        "model": "seedance-2 (Higgsfield)",
        "aspect": "9:16",
        "duration": duration,
        "clip_count": max(1, len(clips)),
        "clips": clips_meta,
        "references_used": used,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "queued",
    }
    (pkg / "meta.json").write_text(json.dumps(meta, indent=2))

    if not (HANDOFF_DIR / "GENERATE.md").is_file():
        (HANDOFF_DIR / "GENERATE.md").write_text(GENERATE_MD)

    queue = _load_queue()
    queue.append(meta)
    QUEUE_FILE.write_text(json.dumps(queue, indent=2))
    return meta


def queue_status() -> list:
    return _load_queue()


def _gate_rollup(clips: list, default: str = "queued") -> str:
    """Package status under the QA gate: 'generated' (final) only when every
    clip is generated AND its eval passed or was explicitly overridden;
    'reviewing' while generated clips await QA or carry failures."""
    states = [c.get("status") for c in clips]
    if any(s in ("requested", "generating") for s in states):
        return "generating"
    if states and all(s == "generated" for s in states):
        evs = [(c.get("eval") or {}).get("status") for c in clips]
        if all(e in ("passed", "overridden") for e in evs):
            return "generated"
        return "reviewing"
    return default


def _capture_clip1_still(package: str, output: str | None) -> None:
    """After clip 1 renders, grab a still — the setting/look reference attached
    to clips 2+ (same environment, new shot)."""
    import videotools
    pkg = HANDOFF_DIR / Path(package).name
    vid = None
    for name in ([output] if output else []) + ["clip-1.mp4", "clip-01.mp4", "output.mp4"]:
        if name and (pkg / Path(name).name).is_file():
            vid = pkg / Path(name).name
            break
    if vid:
        try:
            videotools.make_poster(vid, pkg / "clip-1-still.jpg")
        except Exception:
            pass


def update_clip_status(package: str, index: int, status: str,
                       output: str | None = None, error: str | None = None) -> dict:
    """Set one clip's status/output in a multi-clip package, roll up the overall
    package status, and (when clip 1 finishes) capture its still for clips 2+."""
    meta_path = HANDOFF_DIR / Path(package).name / "meta.json"
    if not meta_path.is_file():
        return {}
    meta = json.loads(meta_path.read_text())
    clips = meta.get("clips") or []
    for c in clips:
        if c.get("index") == index:
            c["status"] = status
            if output is not None:
                c["output"] = output
            if error is not None:
                c["error"] = error
    meta["status"] = _gate_rollup(clips, meta.get("status", "queued"))
    meta_path.write_text(json.dumps(meta, indent=2))
    if index == 0 and status == "generated":
        _capture_clip1_still(package, output)
    queue = _load_queue()
    for entry in queue:
        if entry.get("package") == Path(package).name:
            entry["status"] = meta["status"]
            entry["clips"] = clips
    QUEUE_FILE.write_text(json.dumps(queue, indent=2))
    return meta


def requeue(package: str) -> dict:
    """Put a skipped/failed package back to 'queued' (reset any un-generated
    clips to pending) so a session generates it again."""
    meta_path = HANDOFF_DIR / Path(package).name / "meta.json"
    if not meta_path.is_file():
        return {}
    meta = json.loads(meta_path.read_text())
    meta["status"] = "queued"
    meta.pop("error", None)
    for c in meta.get("clips", []):
        if c.get("status") != "generated":
            c["status"] = "pending"
    meta_path.write_text(json.dumps(meta, indent=2))
    queue = _load_queue()
    for entry in queue:
        if entry.get("package") == Path(package).name:
            entry["status"] = "queued"
            entry.pop("error", None)
            if meta.get("clips"):
                entry["clips"] = meta["clips"]
    QUEUE_FILE.write_text(json.dumps(queue, indent=2))
    return {"ok": True, "status": "queued"}


def remove_package(package: str) -> dict:
    """Cancel a package: drop it from queue.json and delete its handoff folder
    (including any generated clip files)."""
    name = Path(package).name
    queue = _load_queue()
    before = len(queue)
    queue = [e for e in queue if e.get("package") != name]
    QUEUE_FILE.write_text(json.dumps(queue, indent=2))
    pkg_dir = HANDOFF_DIR / name
    if pkg_dir.is_dir():
        shutil.rmtree(pkg_dir, ignore_errors=True)
    return {"ok": True, "removed": name, "was_queued": before != len(queue)}


def update_package_status(package: str, status: str, output: str | None = None,
                          job_id: str | None = None, error: str | None = None) -> dict:
    """Update one package's status in queue.json + its meta.json. Called by
    whatever fires the generation (a Claude session today, an API adapter later)."""
    patch = {"status": status}
    if output:
        patch["output"] = output
    if job_id:
        patch["generation_job_id"] = job_id
    if error:
        patch["error"] = error
    queue = _load_queue()
    for entry in queue:
        if entry.get("package") == package:
            entry.update(patch)
    QUEUE_FILE.write_text(json.dumps(queue, indent=2))
    meta_path = HANDOFF_DIR / Path(package).name / "meta.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text())
        meta.update(patch)
        meta_path.write_text(json.dumps(meta, indent=2))
    return patch
