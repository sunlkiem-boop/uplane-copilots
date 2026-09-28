#!/usr/bin/env python3
"""
Uplane Daily Digest — orchestrator.

    python3 digest.py                 # build + print (nothing is posted)
    python3 digest.py --post          # build + post to DIGEST_CHANNEL
    python3 digest.py --cache-only    # skip Notion, use notes already on disk
    python3 digest.py --health         # what's wired up, what isn't (no tokens spent)

Default is dry-run on purpose: this thing posts to a shared workspace, so posting
is the flag, not the default.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

import config


def health() -> dict:
    import crosschannel
    import llm
    out = {"llm": llm.backend_info(), "slack_scan": crosschannel.mode()}
    s, e = config.window()
    out["window"] = {"since": s.isoformat(), "until": e.isoformat()}
    out["destination"] = config.DIGEST_CHANNEL or "(unset — DIGEST_CHANNEL in helper/.env)"
    out["post_token"] = "set" if config.SLACK_BOT_TOKEN else "MISSING (SLACK_BOT_TOKEN)"
    import notion_scrape
    out["cached_notes_in_window"] = len(notion_scrape.load_cached(s, e))
    out["clients_registered"] = list(config.clients())
    import batch_status
    out["batch_sources"] = {
        "adbot_jobs_log": {"path": str(batch_status.ADBOT_JOBS_LOG),
                           "found": batch_status.ADBOT_JOBS_LOG.is_file()},
        "pipeline_runs": {"path": str(batch_status.PIPELINE_RUNS_DIR),
                          "found": batch_status.PIPELINE_RUNS_DIR.is_dir()},
        "assembler_manifests": {"path": str(batch_status.ASSEMBLER_MANIFESTS_DIR),
                                "found": batch_status.ASSEMBLER_MANIFESTS_DIR.is_dir()},
        "stuck_after_hours": batch_status.STUCK_AFTER_HOURS,
    }
    import comms_sweep
    cfg = comms_sweep.sources_config()
    out["comms_sweep"] = {
        "configured": bool(cfg),
        "channels": len(cfg.get("channels") or []),
        "daniel_dm": bool(cfg.get("include_daniel_dm")),
        "notion_cache": bool(cfg.get("include_notion_cache", True)) if cfg else False,
        "state": str(comms_sweep.STATE_PATH),
        "uplane_dir": {"path": str(comms_sweep.UPLANE_DIR),
                       "found": comms_sweep.UPLANE_DIR.is_dir()},
        "model": comms_sweep.SWEEP_MODEL,
    }
    return out


def build(today: date, since: str | None, refresh: bool, scan: bool,
          pulse_file: str | None = None, sweep: bool = True) -> dict:
    import crosschannel
    import notion_scrape
    import synthesize

    start, end = config.window(today=today, since=since)
    notes, status = notion_scrape.ensure(start, end, refresh=refresh)
    if not notes:
        raise SystemExit(
            f"No daily notes for {start} .. {end}.\n"
            + (f"Reason: {status['degraded']}\n" if status.get("degraded") else "")
            + "Nothing to summarise — not posting an empty digest.")

    # The Slack pulse is gathered BEFORE synthesis, not after: reconciling "what I
    # was told to do" against "what has already happened" has to be one pass, or
    # the briefing lists work a colleague finished on Friday.
    pulse = None
    if pulse_file:
        # Escape hatch for testing the reconciliation without a live Slack token.
        with open(pulse_file, encoding="utf-8") as f:
            pulse = json.load(f) or None
    elif scan:
        try:
            pulse = crosschannel.client_pulse(start) or None
        except Exception as e:
            print(f"warning: Slack pulse failed ({type(e).__name__}: {e})", file=sys.stderr)

    digest = synthesize.run(notes, today=end, start=start, pulse=pulse)

    # RECONCILE (2026-09-10): a separate narrow pass verifies every focus item
    # and every blocker against the traffic, because the producer asking itself
    # "did this already ship?" demonstrably does not work — see reconcile.py.
    recon = {"checked": 0, "shipped": [], "resolved": [], "note": "not run"}
    if pulse:
        try:
            import reconcile
            recon = reconcile.run(digest, pulse)
            if recon.get("shipped") or recon.get("resolved"):
                print("reconcile: %d item(s) already shipped, %d blocker(s) resolved"
                      % (len(recon["shipped"]), len(recon["resolved"])), file=sys.stderr)
        except Exception as e:
            recon = {"checked": 0, "shipped": [], "resolved": [],
                     "note": f"reconcile failed ({type(e).__name__}: {e})"}
            print("warning: " + recon["note"], file=sys.stderr)
    digest["_reconcile"] = recon
    digest["_source"] = {"notes": len(notes), "since": start.isoformat(),
                         "until": end.isoformat(), "source": status.get("source"),
                         "degraded": status.get("degraded"),
                         "skipped": status.get("skipped") or [],
                         "pulse_msgs": sum(len(v) for v in (pulse or {}).values())}

    # Batch status: local fired/done/stuck ground truth (Ad-Bot job log, pipeline
    # runs, assembler manifests) cross-checked against the bot's own Slack view and
    # its cached Notion notes. Attached BEFORE the raw dump on purpose — bot.py
    # re-renders from raw.json after every chat edit, and a section that only
    # existed post-dump would silently vanish the first time he says "drop X".
    # Never fatal: a batch check must not stop the briefing going out.
    try:
        import batch_status
        digest["batches"] = batch_status.gather(start, end, slack=scan)
    except Exception as e:
        print(f"warning: batch status failed ({type(e).__name__}: {e})", file=sys.stderr)
        digest["batches"] = {"items": [], "more": 0, "total": 0,
                             "window": {"since": start.isoformat(),
                                        "until": end.isoformat()},
                             "coverage": f"check FAILED — {type(e).__name__}: {e}"[:200]}

    # DAILY COMMS SWEEP (creative-process sensor 1, CREATIVE-PROCESS-2026-09-09
    # Stage 5): last 24h from the FIXED source list -> one model call -> each
    # keep routed (proposed ruling / graph verdict / shared learning / unclear
    # quoted for Sun). Rides THIS build — no schedule or credentials of its
    # own — and attaches BEFORE the raw dump for the same reason batches does.
    # Never fatal: a sweep failure must not stop the briefing, and the sweep's
    # own watermarking means a failed day re-sweeps tomorrow.
    if sweep:
        try:
            import comms_sweep
            digest["comms"] = comms_sweep.sweep(today=end, slack=scan)
        except Exception as e:
            print(f"warning: comms sweep failed ({type(e).__name__}: {e})",
                  file=sys.stderr)
            digest["comms"] = {
                "error": f"sweep FAILED — {type(e).__name__}: {e}"[:300],
                "zero_capture": False,
                "counts": {"instructions": 0, "verdicts": 0, "signals": 0,
                           "unclear": 0},
                "instructions": [], "verdicts": [], "signals": [],
                "unclear": [], "coverage": ""}
    # Keep the un-edited synthesis on disk. Every later edit re-applies the WHOLE
    # override list to this, which is what makes editing idempotent — patching the
    # already-patched copy would double every `add` each time you touched it.
    import store
    with open(config.OUT_DIR / f"{end.isoformat()}.raw.json", "w", encoding="utf-8") as f:
        json.dump(digest, f, indent=2, ensure_ascii=False)

    # His hand edits are already in the synthesis prompt; applying them again here
    # is belt-and-braces. The prompt makes a drop survive tomorrow's rewording; this
    # makes it take effect even when the model ignored the instruction.
    store.prune(end)
    digest = store.apply(digest, end)

    if scan:
        digest = crosschannel.enrich(digest, start)
    else:
        digest["scan"] = {"mode": "off", "coverage": "cross-channel scan skipped (--no-scan)"}
    return digest


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the daily to-do digest.")
    ap.add_argument("--since", help="window start, YYYY-MM-DD (default: last week Monday)")
    ap.add_argument("--today", help="pretend today is this date, YYYY-MM-DD")
    ap.add_argument("--post", action="store_true", help="post it to Slack")
    ap.add_argument("--cache-only", action="store_true", help="don't call Notion")
    ap.add_argument("--no-scan", action="store_true", help="skip the Slack cross-channel scan")
    ap.add_argument("--no-sweep", action="store_true",
                    help="skip the client-feedback comms sweep (sensor 1)")
    ap.add_argument("--pulse-file", help="JSON file of pre-gathered Slack traffic "
                                         "(testing, or when no Slack token is wired yet)")
    ap.add_argument("--health", action="store_true", help="print wiring status and exit")
    ap.add_argument("--json", action="store_true", help="print the raw digest JSON")
    args = ap.parse_args()

    if args.health:
        print(json.dumps(health(), indent=2, ensure_ascii=False))
        return 0

    today = date.fromisoformat(args.today) if args.today else date.today()
    digest = build(today, args.since, refresh=not args.cache_only, scan=not args.no_scan,
                   pulse_file=args.pulse_file, sweep=not args.no_sweep)

    import slack_post
    # The FILE gets Markdown, not the Slack text. Slack mrkdwn `<url|label>` renders
    # as literal junk anywhere else, which made the saved digest unreadable exactly
    # where the source links mattered most.
    text = slack_post.render_markdown(digest, today)

    stem = config.OUT_DIR / today.isoformat()
    with open(stem.with_suffix(".json"), "w", encoding="utf-8") as f:
        json.dump(digest, f, indent=2, ensure_ascii=False)
    with open(stem.with_suffix(".md"), "w", encoding="utf-8") as f:
        f.write(text + "\n")

    print(json.dumps(digest, indent=2, ensure_ascii=False) if args.json else text)
    print(f"\n--- saved: {stem}.md / .json", file=sys.stderr)

    if args.post:
        res = slack_post.post(digest, today)
        print(f"--- posted to {res.get('channel')} at {res.get('ts')}", file=sys.stderr)
    else:
        print("--- dry run: nothing posted. Add --post to send it.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
