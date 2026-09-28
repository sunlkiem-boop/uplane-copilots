#!/usr/bin/env python3
"""Demo Agent — the stage-gated Slack coworker (spec §1-3, §5-6).

Contract, enforced in code rather than remembered:
  * plan → confirm → execute: every stage run is proposed first (what, how, ETA)
    and starts only on "go".
  * typed verdicts only: approve / revise: … / reject: … — recorded per stage in
    data/state/, and a stage will not run unless the one before it was approved.
  * DM-only twice over: the Slack app can only receive message.im, AND every event
    is gated on (my DM channel) + (Sun's user id); everything outbound goes through
    the slack_out chokepoint.
  * every run writes a TRACE file, and deliverables land in the client tree with
    the paths echoed back into the DM.

Runs under Socket Mode (no public URL). Started by ./run.sh --bot.
"""
from __future__ import annotations

import re
import threading
import time
import traceback

import config
import probes
import runner
import slack_out
import state

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

HELP = (
    "*Demo Agent — what I answer to*\n"
    "• `install <client>` — stage 0: scaffold + intake (plan first, runs on `go`)\n"
    "• `run stage <0-4> for <client>` — any stage, gated on the previous approval\n"
    "• `status <client>` / `status` — where every client stands\n"
    "• `go` / `cancel` — execute or drop the plan I proposed\n"
    "• `approve` · `revise: <notes>` · `reject: <reason>` — your verdict on the "
    "deliverable under review (typed only, no emoji)\n"
    "• `probes` — everything that could silently be broken, checked live\n"
    f"\nStages: " + " → ".join(f"{i} {n}" for i, (n, _, _) in sorted(config.STAGES.items()))
)

VERDICT_RE = re.compile(r"^\s*(approve|revise|reject)\b[:,]?\s*(.*)$", re.I | re.S)
RUN_RE = re.compile(r"^\s*run\s+stage\s+(\d)\s*(?:for\s+)?(.+?)\s*$", re.I)
INSTALL_RE = re.compile(r"^\s*install\s+(.+?)\s*$", re.I)
STATUS_RE = re.compile(r"^\s*status\s*(.*)$", re.I)

_busy = threading.Lock()      # one stage run at a time — honest, and cheaper to reason about


def _plan_message(client: str, stage: int) -> str:
    name, skill, eta = config.STAGES[stage]
    return (f"*Plan — stage {stage} ({name}) for `{client}`*\n"
            f"• what: run `{skill}` end to end; deliverable rendered from the fixed "
            f"stage-{stage} template into the DM and the client tree\n"
            f"• ETA: {eta} (opus does the heavy stages — I'll say when it starts)\n"
            f"• gate: stage advances only on your typed `approve` afterwards\n"
            f"Reply `go` to run it, `cancel` to drop it.")


def _stage_prompt(client: str, stage: int, notes: str = "") -> str:
    name, skill, _ = config.STAGES[stage]
    return (
        f"You are the Demo Agent pipeline runner. Run stage {stage} ({name}) of the "
        f"client-demo pipeline for the client '{client}', using the {skill} skill(s) "
        f"from this login. Follow the skill's own instructions and the fixed stage "
        f"template in ~/Desktop/copilots/uplane-demo-agent/templates/. Work inside "
        f"~/Desktop/uplane. The deliverable file(s) must be written into the client's "
        f"tree. {('Revision notes from Sun: ' + notes) if notes else ''} "
        f"End your reply with one line per deliverable file, exactly:\n"
        f"DELIVERABLE: /absolute/path\n"
        f"and nothing after those lines.")


def _execute(client: str, stage: int, notes: str = "") -> None:
    if not _busy.acquire(blocking=False):
        slack_out.post("⏳ Already running a stage — I'll take the next one after it lands.")
        return
    try:
        name, _, _ = config.STAGES[stage]
        model = config.BUILD_MODEL if stage >= 2 else config.CHAT_MODEL
        slack_out.post(f"🏃 Stage {stage} ({name}) for `{client}` started on "
                       f"{model.split('-')[1]} — I'll post the deliverable here.")
        prompt = _stage_prompt(client, stage, notes)
        out = runner.run_claude(prompt, model)
        trace = runner.write_trace(client, stage, prompt, out)
        files = re.findall(r"^DELIVERABLE:\s*(\S+)\s*$", out, re.M)
        summary = re.sub(r"^DELIVERABLE:.*$", "", out, flags=re.M).strip()
        slack_out.post(f"*Stage {stage} ({name}) — `{client}` — deliverable ready*\n"
                       f"{summary[:1500]}\n"
                       + ("".join(f"\n📄 `{f}`" for f in files) or "\n⚠️ no DELIVERABLE lines — read the trace")
                       + f"\n🧾 trace: `{trace}`\n"
                       f"\nYour verdict (typed): `approve` · `revise: <notes>` · `reject: <reason>`")
        import os
        for f in files[:5]:
            if os.path.exists(f):
                try:
                    slack_out.upload(f, title=f.rsplit("/", 1)[-1])
                except Exception:
                    slack_out.post(f"(couldn't upload `{f}` — it exists on disk)")
        st = state.load(client)
        st["pending"] = {"kind": "review", "stage": stage, "at": time.time()}
        state.save(st)
    except runner.SetupError as e:
        slack_out.post(f"🛑 {e.detail}\nFix: {e.fix}")
    except Exception:
        slack_out.post("🛑 Stage run crashed:\n```" + traceback.format_exc()[-900:] + "```")
    finally:
        _busy.release()


def handle_text(text: str) -> None:
    t = text.strip()

    if re.fullmatch(r"help|\?", t, re.I):
        slack_out.post(HELP); return
    if re.fullmatch(r"probes|health", t, re.I):
        slack_out.post("Running probes (incl. a real inference call)…")
        slack_out.post(probes.render(probes.run_probes(deep=True))); return

    m = STATUS_RE.fullmatch(t)
    if m:
        target = state._norm(m.group(1)) if m.group(1).strip() else ""
        clients = [target] if target else state.known_clients()
        if not clients:
            slack_out.post("No clients installed yet — `install <client>` starts one."); return
        lines = []
        for c in clients:
            st = state.load(c)
            pend = st.get("pending") or {}
            lines.append(f"• `{c}` — highest approved stage: {st['stage']}"
                         + (f" · awaiting your verdict on stage {pend['stage']}"
                            if pend.get("kind") == "review" else "")
                         + (f" · plan proposed for stage {pend['stage']} (say `go`)"
                            if pend.get("kind") == "plan" else ""))
        slack_out.post("\n".join(lines)); return

    m = INSTALL_RE.fullmatch(t)
    if m and not RUN_RE.fullmatch(t):
        client = state._norm(m.group(1))
        st = state.load(client)
        st["pending"] = {"kind": "plan", "stage": 0, "at": time.time()}
        state.save(st)
        slack_out.post(_plan_message(client, 0)); return

    m = RUN_RE.fullmatch(t)
    if m:
        stage, client = int(m.group(1)), state._norm(m.group(2))
        if stage not in config.STAGES:
            slack_out.post(f"Stages are 0-4. ({stage} isn't one.)"); return
        st = state.load(client)
        if stage > 0 and st["stage"] < stage - 1:
            slack_out.post(
                f"🚧 Gate: stage {stage} needs stage {stage-1} approved first — `{client}` "
                f"is at {st['stage']}. The gates are the product; I don't skip them. "
                f"(`run stage {st['stage']+1} for {client}` is the next legal move.)")
            return
        st["pending"] = {"kind": "plan", "stage": stage, "at": time.time()}
        state.save(st)
        slack_out.post(_plan_message(client, stage)); return

    if re.fullmatch(r"go|yes|do it", t, re.I):
        for c in state.known_clients():
            st = state.load(c)
            p = st.get("pending") or {}
            if p.get("kind") == "plan":
                threading.Thread(target=_execute, args=(c, p["stage"]), daemon=True).start()
                return
        slack_out.post("Nothing planned. `install <client>` or `run stage <n> for <client>` first.")
        return
    if re.fullmatch(r"cancel|stop", t, re.I):
        for c in state.known_clients():
            st = state.load(c)
            if (st.get("pending") or {}).get("kind") == "plan":
                st["pending"] = None; state.save(st)
                slack_out.post(f"Dropped the plan for `{c}`."); return
        slack_out.post("Nothing pending to cancel."); return

    m = VERDICT_RE.fullmatch(t)
    if m:
        verdict, notes = m.group(1).lower(), m.group(2).strip()
        for c in state.known_clients():
            st = state.load(c)
            p = st.get("pending") or {}
            if p.get("kind") == "review":
                state.record_verdict(c, p["stage"], verdict, notes)
                if verdict == "approve":
                    nxt = p["stage"] + 1
                    slack_out.post(f"✅ Stage {p['stage']} approved for `{c}` — on record."
                                   + (f" Next: `run stage {nxt} for {c}`." if nxt in config.STAGES else " Pipeline complete."))
                elif verdict == "revise":
                    slack_out.post(f"🔁 Revision recorded for `{c}` stage {p['stage']} — rerunning with your notes.")
                    threading.Thread(target=_execute, args=(c, p["stage"], notes), daemon=True).start()
                else:
                    slack_out.post(f"❌ Stage {p['stage']} rejected for `{c}` — on record. "
                                   f"`run stage {p['stage']} for {c}` starts a fresh attempt.")
                return
        slack_out.post("No deliverable is awaiting a verdict right now."); return

    # Free text → the chat model routes it to ONE of the commands above (or asks).
    try:
        reply = runner.run_claude(
            "You are the Demo Agent's router. Sun said: \"" + t.replace('"', "'") + "\"\n"
            "Commands you may map to: install <client> · run stage <0-4> for <client> · "
            "status [<client>] · approve/revise/reject · probes · help.\n"
            "If the message clearly maps to one command, reply with EXACTLY that command "
            "on one line and nothing else. If not, reply with ONE self-contained "
            "clarifying question (per the coworker contract).",
            config.CHAT_MODEL, timeout=120).strip()
        first = reply.splitlines()[0].strip()
        if (VERDICT_RE.fullmatch(first) or RUN_RE.fullmatch(first)
                or INSTALL_RE.fullmatch(first) or STATUS_RE.fullmatch(first)
                or re.fullmatch(r"probes|help", first, re.I)):
            slack_out.post(f"(reading that as `{first}`)")
            handle_text(first)
        else:
            slack_out.post(reply[:800])
    except runner.SetupError as e:
        slack_out.post(f"🛑 {e.detail}\nFix: {e.fix}")


def gate(event) -> bool:
    """Double inbound gate: only Sun, only in the bot's own DM, never other bots."""
    if event.get("bot_id") or event.get("subtype"):
        return False
    if event.get("user") != config.ME_SLACK_ID:
        return False
    if event.get("channel") not in slack_out.allowed():
        return False
    return bool((event.get("text") or "").strip())


def main():
    boot = probes.run_probes(deep=False)
    print(probes.render(boot))
    if not (config.SLACK_BOT_TOKEN and config.SLACK_APP_TOKEN):
        print("\n❌ Slack tokens missing — see SETUP.md (helper/.env)."); return 1
    # Constructed HERE, not at import: Bolt hard-requires a token at construction,
    # and an import-time App makes the module untestable offline (the digest's
    # lesson, relearned empirically).
    app = App(token=config.SLACK_BOT_TOKEN, token_verification_enabled=False)

    @app.event("message")
    def on_message(event, logger):
        if not gate(event):
            return
        try:
            handle_text((event.get("text") or "").strip())
        except Exception:
            try:
                slack_out.post("🛑 That crashed:\n```" + traceback.format_exc()[-900:] + "```")
            except Exception:
                pass

    auth = app.client.auth_test(token=config.SLACK_BOT_TOKEN)
    print(f"Slack: connected as {auth.get('user')} in {auth.get('team')}")
    slack_out.post("👋 Demo Agent up. `help` for the contract, `probes` for a full self-check.")
    SocketModeHandler(app, config.SLACK_APP_TOKEN).start()


if __name__ == "__main__":
    raise SystemExit(main())
