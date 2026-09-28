"""
Job bridge between the Slack helper and the Chrome extension.

The Slack side has no way to *push* to a content script, so the extension
long-polls this queue:

    GET  /next-job          -> the oldest queued job (marks it running)
    GET  /creative/<id>     -> the dropped creative bytes (served locally so the
                               extension can fetch it without the Slack token)
    POST /job-result        -> { job_id, status, image_urls[] | error }

On completion we fire an on_complete callback (registered by app.py) which posts
the preview back into the Slack thread.
"""
from __future__ import annotations

import base64
import json
import os
import time
import threading
import uuid
from dataclasses import dataclass, field, fields as dc_fields
from typing import Callable, Optional

# Queued work used to live only in memory, so restarting the helper destroyed it —
# a 42-job batch lost 39 jobs that way, silently. Unfinished jobs are now mirrored
# to disk on every state change and reloaded at startup.
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "queue.json")

# Pipelining: ONE driver (tab) may hold several in-flight generations on a board,
# so it fires the next prompt while the previous renders instead of idling. A
# second tab on the same board is still refused (it would eat the refs). A board
# leased to a driver frees once that driver has been silent for LEASE_IDLE.
MAX_INFLIGHT_PER_BOARD = int(os.environ.get("ADBOT_MAX_INFLIGHT", "3"))
LEASE_IDLE = float(os.environ.get("ADBOT_LEASE_IDLE", "75"))


@dataclass
class Job:
    id: str
    # what the extension needs to drive the board
    prompt: str
    count: int
    client_label: str
    product_label: str
    folder: str
    brand_label: str
    defaults: dict
    creative_bytes: bytes
    creative_media_type: str
    # slack context for the reply
    channel: str
    thread_ts: str
    user: str
    # bookkeeping
    file_name: str = ""       # creative filename (shown in the live status)
    product_key: str = ""     # for reruns: re-resolve the full product entry
    reference_paths: list = field(default_factory=list)   # per-product reference
                              # images to auto-attach to the board before Generate
                              # (served via /reference/<id>/<idx>, injected by the driver)
    folder_url: str = ""      # exact folder to generate into (from the modal)
    attempts: int = 0         # driver retries (access-denied -> reload -> retry once)
    generated: bool = False   # Generate has been CLICKED -> credits spent. The
                              # driver reports phase "generate" only AFTER the
                              # click, so anything from there on must never be
                              # auto-retried: that would bill a second batch.
    prior_ids: list = field(default_factory=list)   # ad-ids present at Generate
                              # time — persisted so a FRESH driver (after a reload
                              # mid-generation) can collect THIS job's new concepts
                              # without re-generating. Survives driver death.
    collect_only: bool = False  # re-attach mode: the concepts already generated;
                              # a fresh driver must ONLY collect them, never re-run.
    collect_attempts: int = 0   # cap on re-attach loops so a permanently-dead
                              # board can't spin forever.
    confirm: str = ""         # "" | awaiting | confirmed | cancelled  (Slack gate)
    folder_title: str = ""    # folder name the driver actually landed on
    phase: str = ""           # current pipeline step (for the live Slack status)
    phase_at: float = 0.0     # when we entered that phase
    last_seen: float = 0.0    # driver lease: last time a driver touched this job
    driver: str = ""          # id of the tab/driver that claimed it (board leasing)
    status_ts: str = ""       # ts of the ONE status message we keep editing
    alerted: str = ""         # last stall alert emitted (so we don't repeat)
    # How the driver assembled the batch: {collected, inferred, stillCrafting, failed}.
    # A real FIELD, set inside finish() before on_complete fires — assigning it on the
    # job after finish() returned was too late, because finish() is what calls
    # on_complete, so the Slack message never saw it.
    batch_stats: dict = field(default_factory=dict)
    rationale: str = ""
    status: str = "queued"          # queued | running | done | error
    error: str = ""
    image_urls: list = field(default_factory=list)
    created: float = field(default_factory=lambda: time.time())
    started: Optional[float] = None
    finished: Optional[float] = None


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self.on_complete: Optional[Callable[[Job], None]] = None
        self.path = STATE_PATH

    # -- durability -------------------------------------------------------- #
    # Only UNFINISHED jobs are mirrored: finished ones are already recorded in
    # joblog and restoring them would re-post old results.
    def _snapshot(self) -> list:
        out = []
        for j in self._jobs.values():
            if j.status in ("done", "error"):
                continue
            d = {}
            for f in dc_fields(j):
                v = getattr(j, f.name)
                d[f.name] = base64.b64encode(v).decode() if isinstance(v, bytes) else v
            out.append(d)
        return out

    def save(self) -> None:
        """Atomic write — a half-written queue file is worse than none."""
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"jobs": self._snapshot()}, f)
            os.replace(tmp, self.path)
        except Exception:
            pass                      # persistence must never break a live job

    def load(self) -> int:
        """Restore unfinished jobs after a restart. Anything that was RUNNING is
        re-queued: its driver died with the old process."""
        try:
            with open(self.path) as f:
                data = json.load(f)
        except Exception:
            return 0
        names = {f.name for f in dc_fields(Job)}
        n = 0
        with self._lock:
            for d in data.get("jobs", []):
                try:
                    kw = {k: v for k, v in d.items() if k in names}
                    if isinstance(kw.get("creative_bytes"), str):
                        kw["creative_bytes"] = base64.b64decode(kw["creative_bytes"])
                    job = Job(**kw)
                    if job.status == "running":
                        job.status = "queued"
                        job.started = None
                        # Credits were already spent: the concepts are rendering on
                        # the board right now. Re-queueing plain would make the next
                        # driver fire Generate a SECOND time and bill the batch
                        # twice. The live watchdog already downgrades these to
                        # collect-only; a restart has to do the same.
                        if job.generated:
                            job.collect_only = True
                    self._jobs[job.id] = job
                    n += 1
                except Exception:
                    continue
        return n

    # -- producer (Slack side) --------------------------------------------- #
    def create(self, **kw) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], **kw)
        with self._lock:
            self._jobs[job.id] = job
        self.save()
        return job

    # -- consumer (extension side) ----------------------------------------- #
    def queued_folders(self) -> list:
        """Distinct folder URLs with work waiting — lets the extension open one
        board tab per client instead of funnelling every client through one tab."""
        with self._lock:
            seen, out = set(), []
            for j in sorted((j for j in self._jobs.values() if j.status == "queued"),
                            key=lambda j: j.created):
                if j.folder_url and j.folder_url not in seen:
                    seen.add(j.folder_url)
                    out.append(j.folder_url)
            return out

    def _board_lease(self, now: float) -> dict:
        """Per-board {driver-ids that are LIVE on it, in-flight count}. Call under
        the lock. Only running jobs whose driver has checked in within LEASE_IDLE
        count — a board held by a dead driver is free to re-lease."""
        out: dict[str, dict] = {}
        for j in self._jobs.values():
            if j.status != "running" or not j.folder_url:
                continue
            if now - (j.last_seen or j.started or j.created) > LEASE_IDLE:
                continue                       # stale — its driver is gone
            o = out.setdefault(j.folder_url, {"drivers": set(), "n": 0})
            o["drivers"].add(j.driver or "")
            o["n"] += 1
        return out

    def next_queued(self, prefer_folder: str | None = None,
                    driver: str | None = None) -> Optional[Job]:
        """Claim the oldest queued job, PREFERRING one for `prefer_folder`.

        Soft affinity: a tab already sitting on a client's board keeps taking that
        client's work, so two tabs can run two clients in parallel without either
        navigating away mid-stream. It is only a preference — when that client has
        nothing left, the driver takes any job rather than sitting idle.

        ONE DRIVER PER BOARD, up to MAX_INFLIGHT_PER_BOARD in flight. A single tab
        may fire several generations before collecting them (the board renders them
        in parallel, so waiting for each is wasted wall-clock) — but a SECOND tab on
        the same board is refused, because generating CONSUMES the attached
        references and two tabs firing on one board ate each other's refs. The board
        is leased to the first driver that claims on it; a different driver gets
        nothing there until that lease goes idle (its jobs age out via the watchdog).

        Backward-compatible: a caller that passes no `driver` id keeps the strict
        one-job-per-board behaviour.
        """
        with self._lock:
            now = time.time()
            lease = self._board_lease(now)

            def blocked(folder: str) -> bool:
                o = lease.get(folder or "")
                if not o:
                    return False                       # free board
                if not driver:
                    return True                        # legacy caller: one per board
                if o["drivers"] - {driver, ""}:
                    return True                        # leased to another live tab
                return o["n"] >= MAX_INFLIGHT_PER_BOARD  # my board, but at capacity

            pending = [j for j in self._jobs.values()
                       if j.status == "queued" and not blocked(j.folder_url or "")]
            pending.sort(key=lambda j: j.created)
            if not pending:
                return None
            job = None
            if prefer_folder:
                job = next((j for j in pending if prefer_folder in (j.folder_url or "")), None)
            job = job or pending[0]
            job.status = "running"
            job.driver = driver or ""
            job.started = time.time()
            job.last_seen = job.started      # start the driver lease
        self.save()
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def touch(self, job_id: str) -> Optional[Job]:
        """Renew a running job's lease — proves its driver is still alive."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.last_seen = time.time()
            return job

    def stale_running(self, max_idle: float) -> list:
        """Running jobs whose driver has gone silent (tab closed/reloaded/crashed).
        Jobs parked on the human confirm gate are excluded — they're waiting on a
        person, not a dead driver."""
        now = time.time()
        with self._lock:
            return [
                j for j in self._jobs.values()
                if j.status == "running"
                and j.confirm != "awaiting"
                and (now - (j.last_seen or j.started or j.created)) > max_idle
            ]

    def requeue(self, job_id: str) -> Optional[Job]:
        """Put a claimed job back in the queue and count the attempt. Used when
        the driver hits an access-denied page and must reload the tab — the
        reload destroys the content script, so the job has to be re-servable."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            job.attempts += 1
            job.status = "queued"
            job.started = None
        self.save()
        return job

    def requeue_for_collect(self, job_id: str, max_attempts: int = 3) -> Optional[Job]:
        """A job died AFTER Generate (reload/crash mid-generation). The concepts
        are already generating on the board, so put it back as COLLECT-ONLY: a
        fresh driver re-opens the folder and scrapes the results WITHOUT
        re-generating (never double-bills). Returns the job, or None once the
        re-attach cap is hit (board is genuinely unreachable)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            if job.collect_attempts >= max_attempts:
                return None
            job.collect_attempts += 1
            job.collect_only = True
            job.status = "queued"
            job.started = None
        self.save()
        return job

    def oldest_queued(self) -> Optional[Job]:
        """Peek at the next queued job WITHOUT claiming it (the background
        worker uses this to decide whether to open a board tab)."""
        with self._lock:
            pending = [j for j in self._jobs.values() if j.status == "queued"]
            pending.sort(key=lambda j: j.created)
            return pending[0] if pending else None

    def finish(self, job_id: str, status: str, image_urls=None, error: str = "",
               stats: Optional[dict] = None) -> Optional[Job]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            # IDEMPOTENT: a driver may POST /job-result more than once (network
            # retry, a duplicated request, or a reload racing the first post).
            # Firing on_complete twice would upload the batch to Slack twice, so
            # a job that has already finished is returned untouched.
            if job.status in ("done", "error"):
                return job
            job.status = "done" if status == "ok" else "error"
            job.image_urls = image_urls or []
            job.error = error
            job.finished = time.time()
            # BEFORE on_complete (fired below) — that callback builds the Slack
            # message and is the only consumer.
            if stats:
                job.batch_stats = dict(stats)
        self.save()
        # fire outside the lock
        if self.on_complete:
            try:
                self.on_complete(job)
            except Exception:
                pass
        return job

    # -- housekeeping: drop jobs older than an hour ------------------------ #
    def gc(self, max_age: float = 3600) -> None:
        """Drop old FINISHED jobs only.

        This used to evict any job older than an hour regardless of status, which
        silently destroyed queued work: the board clears roughly one job every few
        minutes, so a long batch backs up, and 22 jobs of a 42-job run aged out and
        were deleted before they ever ran — no result, no error, just gone. Work
        that has not finished is never garbage; only completed jobs are.
        """
        cutoff = time.time() - max_age
        with self._lock:
            for jid in [j.id for j in self._jobs.values()
                        if j.created < cutoff and j.status in ("done", "error")]:
                self._jobs.pop(jid, None)
        self.save()


STORE = JobStore()
