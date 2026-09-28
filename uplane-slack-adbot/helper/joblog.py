"""
Append-only job event log — so "what happened?" is always answerable locally.

The Slack thread is the human-facing record, but it isn't queryable, it scrolls
away, and it vanishes if someone deletes the message. When a job aborted we had
to read the reason off a screenshot; this file is the fix. One JSON object per
line, newest last.

    from joblog import log
    log("result", job, status="error", error="…")

Read it back with `python3 joblog.py` (or `… 20 <job_id>`), or GET /debug/jobs.
"""
from __future__ import annotations

import datetime
import json
import pathlib
import threading

LOG_DIR = pathlib.Path(__file__).resolve().parent / "logs"
PATH = LOG_DIR / "jobs.jsonl"
ROTATED = LOG_DIR / "jobs.1.jsonl"
MAX_BYTES = 2_000_000          # ~one rotation keeps recent history bounded

_LOCK = threading.Lock()       # Flask is threaded; keep lines from interleaving


def _job_fields(job) -> dict:
    """The context worth having on every line — never blow up on a partial job."""
    if job is None:
        return {}
    g = lambda k: getattr(job, k, None)
    return {k: v for k, v in {
        "job_id": g("id"),
        "brand": g("client_label"),
        "product": g("product_label"),
        "file": g("file_name"),
        "count": g("count"),
        "phase": g("phase"),
        "folder_url": g("folder_url"),
        # Slack linkage — lets offline tools (context-graph reaction sync) find
        # the job's thread later and read reactions as ratings.
        "channel": g("channel"),
        "thread_ts": g("thread_ts"),
    }.items() if v not in (None, "")}


def log(event: str, job=None, **fields) -> None:
    """Append one event. Logging must never break a job, so this swallows IO
    errors — a missing log line is survivable, a crashed generation is not."""
    rec = {"ts": datetime.datetime.now().isoformat(timespec="seconds"),
           "event": event, **_job_fields(job), **fields}
    try:
        with _LOCK:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            if PATH.exists() and PATH.stat().st_size > MAX_BYTES:
                PATH.replace(ROTATED)
            with PATH.open("a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
    except Exception:
        pass


def tail(n: int = 50, job_id: str | None = None) -> list[dict]:
    """Most recent events last. Reads the rotated file too when needed."""
    lines: list[str] = []
    for p in (ROTATED, PATH):
        try:
            lines += p.read_text().splitlines()
        except OSError:
            pass
    out = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if job_id and r.get("job_id") != job_id:
            continue
        out.append(r)
    return out[-n:]


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    jid = sys.argv[2] if len(sys.argv) > 2 else None
    for r in tail(n, jid):
        head = f"{r.pop('ts', '')}  {r.pop('event', ''):<16}"
        jid_s = r.pop("job_id", "")
        rest = " ".join(f"{k}={v}" for k, v in r.items())
        print(f"{head} {jid_s:<14} {rest}")
