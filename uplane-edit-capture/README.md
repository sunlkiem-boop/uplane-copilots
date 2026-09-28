# Uplane Edit Capture (+ Client Review)

The context graph's richest inputs, captured at the two moments judgment
actually happens:

- **Edit capture** — what you fixed in the Uplane editor and why (Chrome
  extension + this helper).
- **Client review** (`http://127.0.0.1:8791/review`) — structured verdicts on
  a batch of creatives: two taps (approve/revise/reject + what's off) per
  creative, written straight into the graph. Batches come from
  `~/Desktop/uplane/review/<batch>/`, the Ad-Bot's `batch_ready/`, and Video
  Lab handoffs — drop files in a folder, open the page, review. Use it live
  with a client on a call, or to relay their Slack prose as structured verdicts.

A rating says good/bad; these say what was wrong, what the correction was, and
the stated reason — the signal that promotes rules and, later, trains models.

## How it works

1. The Chrome extension injects a small pill (bottom-right) on `app.uplane.com`.
2. As you work it passively notes your actions — text edits (with before → after),
   regenerate/delete/replace-image clicks. Bot-driven events (the Ad-Bot's
   synthetic typing) are ignored via `event.isTrusted`.
3. **v2 — frames**: the creative itself is snapshotted at session start and when
   the panel opens (canvas readback first; tab-screenshot fallback via the
   service worker). Canvas edits that DOM listeners can't see are caught this way.
4. Click the pill — or it pops up on Save/Done/Export — and the panel shows an
   **AI draft of what changed visually** (before/after frames interpreted via the
   `claude` CLI using the adbot's working config, `~/.claude-adbot`; local
   pixel-diff fallback if that's unavailable). Correct the draft, tag the aspect,
   optionally give a verdict, and say **why** in one line.
5. "Log to graph" posts to the local helper (:8791): one `edit_event` row per
   edit — carrying `session_id`, the `ai_note` (+`ai_backend`, marked `+human`
   when you corrected it) and your why — plus `FRAME_BEFORE`/`FRAME_AFTER` edges
   to the stored PNGs under `context-graph/media/edit-sessions/<session>/`, and
   a `feedback` row for the verdict. Each logged session is a
   **(before image, what+why, after image) triple** — training-grade data.
   When the editor URL contains a concepts UUID the Ad-Bot has seen, the
   session links to that job's creative.

Cost control: interpretation runs only when the panel opens, is cached per
frame-pair, and discarded sessions never call the model.

## Setup (one-time)

1. **Helper**: starts from the Control Room (🚦 → Edit Capture → Start), or:
   ```bash
   ~/Desktop/copilots/uplane-edit-capture/helper/run.sh
   ```
2. **Extension**: `chrome://extensions` → Developer mode → *Load unpacked* →
   pick `~/Desktop/copilots/uplane-edit-capture/extension/`.

## Test bench (no Uplane needed)

With the helper running, open <http://127.0.0.1:8791/test> — a fake editor page
wired to the real capture core. Edit the fields, click the controls, log, then:

```bash
sqlite3 ~/Desktop/uplane/context-graph/graph.db "SELECT action, element, before, after, why FROM edit_event ORDER BY at DESC LIMIT 5;"
```

## Files

| File | Role |
|---|---|
| `extension/capture-core.js` | all logic: breadcrumb collector + panel + POST (testable standalone) |
| `extension/content.js` | thin entrypoint that calls the core with the real endpoint |
| `extension/manifest.json` | MV3, matches `app.uplane.com`, host permission for `127.0.0.1:8791` |
| `helper/app.py` | stdlib HTTP server → graph writes; `/health`, `/test` |

## Design rules

- **Passive only** — capture-phase listeners, never `preventDefault`, everything
  try/caught. This must be unable to break the editor.
- **Humans only** — `isTrusted` filter drops synthetic (bot) events.
- **Shadow DOM** — page CSS and panel CSS can't fight.
- The DOM heuristics are deliberately generic (labels from aria/placeholder/
  button text) so Uplane UI changes degrade capture quality, never break the page.
