# Demo Agent

A standalone Slack DM coworker that runs the client-demo process as a stage-gated
pipeline: install → assets → research → strategy → static prompts, every stage ending in
a fixed-template deliverable that advances only on Sun's typed sign-off.
Spec (canonical, all decisions resolved 2026-08-18): `SPEC.md` in this folder.

Build status: §10.1 foundations + §10.2 skills + **§10.3 bot BUILT** (2026-08-25):
manifest, auth.sh (login + --skills + --notion), Socket Mode bot with the digest's
containment (outbound chokepoint, double inbound gate, DM-only scopes), plan→confirm→
execute, typed-verdict sign-off + stage gates in data/state/, TRACE files per run,
startup probes, SwiftBar keepalive, Control Room entry. Offline-verified end to end
(14 checks). PNG card rendering deferred — deliverables post as files + summaries.
Blocked ONLY on Sun's 3 manual steps below (SETUP.md walks them).

Sun must create (the agent cannot):
1. The Slack app from a manifest + its 3 tokens (clone the Daily Digest pattern).
2. The `~/.claude-demo` Claude login via `auth.sh` (CLAUDE_CONFIG_DIR isolation).
3. Notion MCP browser auth on that login (`auth.sh --notion`) — required for stage 0.
