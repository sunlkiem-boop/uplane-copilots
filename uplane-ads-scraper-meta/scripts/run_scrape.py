#!/usr/bin/env python3
"""
Wrapper around the scraper engine (scrape.py, one level up from scripts/).

- Takes a Meta Ad Library URL as first positional arg.
- Creates a fresh run directory under runs/<page_id>-<timestamp>/ in this workspace.
- Runs the existing scraper into <run_dir>/screenshots/ (streaming its stdout/stderr).
- Parses the scraper's stdout to build a manifest.json with one entry per ad.
- Prints `RUN_DIR=<absolute path>` on the last line so the calling skill can capture it.

Why a wrapper exists: the scraper itself stays untouched (per project constraints).
This wrapper adds the structured manifest the report builder needs, plus the
per-run directory layout (screenshots + manifest + analyses + report inputs all
co-located).
"""
from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
from urllib.parse import parse_qs, urlparse

HOME = os.path.expanduser("~")
# uplane-ads-scraper-meta/ — derived from this script's location so the folder
# can be moved without breaking. Engine + runs live here; brand profiles live
# under ~/Desktop/uplane/clients/<client>/<slug>/.
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRAPER = os.path.join(BASE, "scrape.py")
VENV_PY = os.path.join(HOME, ".meta-scraper-venv", "bin", "python")
RUNS_ROOT = os.path.join(BASE, "runs")

# Matches the scraper's per-ad output. Two lines per ad:
#   "  [03] saved /abs/path/ad-...-03.png  ->  https://destination.example.com/page"
#   "  [03] creative /abs/path/creative-...-03.png is_video=false"
# The dest URL is optional. The creative path can be NONE if extraction failed.
SAVE_LINE_RE = re.compile(
    r"^\s*\[(?P<idx>\d+)\]\s+saved\s+(?P<png>\S+\.png)(?:\s+->\s+(?P<dest>\S+))?\s*$"
)
CREATIVE_LINE_RE = re.compile(
    r"^\s*\[(?P<idx>\d+)\]\s+creative\s+(?P<png>\S+)\s+is_video=(?P<is_video>true|false)"
    r"(?:\s+hash=(?P<hash>\S+))?\s*$"
)


def die(msg: str, code: int = 2) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def run_id_from(url: str) -> str:
    """Identifier used in the run-directory name.

    Two URL shapes are accepted:
      - Single-brand Pages-result URL → use the raw ``view_all_page_id``
      - Keyword / category search URL → synthesize ``search-<slug>-<country>``
        so multiple keyword runs sit cleanly side-by-side under ``runs/``.
    """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    pid = qs.get("view_all_page_id", [None])[0]
    if pid:
        return pid
    q = qs.get("q", [None])[0]
    if q:
        country = qs.get("country", ["ALL"])[0]
        slug = re.sub(r"[^a-z0-9]+", "-", q.lower()).strip("-")[:40] or "search"
        return f"search-{slug}-{country.lower()}"
    die(
        "URL is missing both view_all_page_id and q. Provide either a "
        "Pages-result URL (single brand) or a keyword-search URL (category)."
    )


def main() -> int:
    if len(sys.argv) < 2:
        die('usage: run_scrape.py "<meta_ad_library_url>"')

    url = sys.argv[1].strip()
    pid = run_id_from(url)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = os.path.join(RUNS_ROOT, f"{pid}-{stamp}")
    shots_dir = os.path.join(run_dir, "screenshots")
    os.makedirs(shots_dir, exist_ok=True)

    if not os.path.isfile(SCRAPER):
        die(f"scraper not found at {SCRAPER}")
    if not os.path.isfile(VENV_PY):
        die(f"venv python not found at {VENV_PY}")

    print(f"Run dir: {run_dir}", flush=True)
    print(f"Calling scraper...\n", flush=True)

    # Stream the scraper's output as it runs so the user sees progress.
    # We also collect it into a buffer for post-hoc parsing.
    proc = subprocess.Popen(
        [VENV_PY, SCRAPER, url, shots_dir],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # merge so order is preserved
        text=True,
        bufsize=1,
    )
    captured: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        captured.append(line)
    rc = proc.wait()

    if rc != 0:
        # Persist whatever we got, in case the user wants to inspect it.
        with open(os.path.join(run_dir, "scraper.log"), "w") as f:
            f.writelines(captured)
        die(f"scraper exited with code {rc}. See {run_dir}/scraper.log", code=rc)

    # Build manifest from parsed lines. Each ad emits two lines (saved + creative);
    # we merge them by index. `creative_png` may be "" if extraction failed; for
    # videos it's typically the poster frame (CSS play-button overlay is excluded).
    by_idx: dict[int, dict] = {}
    for raw in captured:
        line = raw.rstrip("\n")
        m = SAVE_LINE_RE.match(line)
        if m:
            idx = int(m.group("idx"))
            entry = by_idx.setdefault(idx, {"index": idx})
            entry["card_png"] = os.path.basename(m.group("png"))
            entry["dest_url"] = m.group("dest") or ""
            continue
        m = CREATIVE_LINE_RE.match(line)
        if m:
            idx = int(m.group("idx"))
            entry = by_idx.setdefault(idx, {"index": idx})
            png = m.group("png")
            entry["creative_png"] = "" if png == "NONE" else os.path.basename(png)
            entry["is_video"] = m.group("is_video") == "true"
            h = m.group("hash") or ""
            entry["creative_hash"] = "" if h in ("", "NONE") else h
            continue
    # Default missing fields so downstream code can rely on shape
    manifest = []
    for idx in sorted(by_idx.keys()):
        e = by_idx[idx]
        e.setdefault("card_png", "")
        e.setdefault("creative_png", "")
        e.setdefault("creative_hash", "")
        e.setdefault("dest_url", "")
        e.setdefault("is_video", False)
        manifest.append(e)

    # Persist run metadata.
    with open(os.path.join(run_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    meta = {
        "page_id": pid,
        "source_url": url,
        "scraped_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "ad_count": len(manifest),
    }
    with open(os.path.join(run_dir, "run.json"), "w") as f:
        json.dump(meta, f, indent=2)

    # Persist full scraper output for debugging.
    with open(os.path.join(run_dir, "scraper.log"), "w") as f:
        f.writelines(captured)

    if not manifest:
        die("scraper finished but no ad screenshots were captured.", code=1)

    print(f"\nManifest: {len(manifest)} ads written to {run_dir}/manifest.json")
    print(f"RUN_DIR={run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
