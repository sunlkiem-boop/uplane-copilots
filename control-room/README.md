# Uplane Control Room (menu-bar)

A 🚦 icon in your macOS menu bar that lists every copilot/agent with a live
status dot and **Open / Start / Stop** — no terminal, no remembering ports.
Clicking **Start** launches that tool's server in the background; clicking
**Open** opens its web page.

It's a [SwiftBar](https://github.com/swiftbar/SwiftBar) plugin — SwiftBar is a
tiny, free menu-bar host that runs the little script in this folder.

## One-time setup (2 minutes)

1. **Install SwiftBar** — either:
   ```bash
   brew install --cask swiftbar
   ```
   or download the `.dmg` from <https://github.com/swiftbar/SwiftBar/releases> and drag it to Applications.

2. **Open SwiftBar.** The first time, it asks for a **Plugin Folder** — choose:
   ```
   ~/Desktop/apps/swiftbar-plugins
   ```
   That folder holds symlinks to each plugin (this control room, the Desktop
   Librarian, the Ad-Bot keepalive), so one plugin folder hosts them all. To add
   this plugin there: `ln -s ~/Desktop/copilots/control-room/uplane-control-room.5s.py ~/Desktop/apps/swiftbar-plugins/`

3. The **🚦** appears in your menu bar. Click it — you'll see your copilots.

4. (Optional) SwiftBar → **Preferences → Start at Login** so the control room is always there.

That's it. Click **Start** on a tool → its server boots in the background and the
dot goes 🟢; click **Open in browser**.

## What's in the menu

| Tool | Type | Action |
|---|---|---|
| Compliance Copilot | server `:8787` | Start / Stop / Open (the Excel-review page) |
| Creative Studio | server `:8788` | Start / Stop / Open |
| Video Lab | server `:8789` | Start / Stop / Open |
| Slack Ad-Bot | server `:8790` | Start / Stop / Open (normally supervised by the SwiftBar keepalive) |
| Ads Scraper | Claude skill | Info only (runs inside Claude Code) |

The dot is live: 🟢 = the server is up on its port, ⚪️ = stopped. Refreshes every 5s.

## 📓 Client Memory (scaffold)

Below the tools, a **Client Memory** section lists each client and how much the
copilots have learned about it so far (the cross-copilot **shared learnings**).
Each client links to the learnings CSV and its folder. Right now it's the room +
the counts; a richer per-client memory view is the planned next step — as you add
more services and they log more learnings, this section fills in automatically.

It reads from (set in `copilots.json` → `memory`):
- `clients_dir` — `~/Desktop/uplane/clients` (the client folders)
- `learnings_csv` — `~/Desktop/uplane/shared-learnings.csv` (what the copilots write)

## Adding / changing a tool

Edit **`copilots.json`** (the menu has an "Edit copilots…" shortcut). Copy a
`server` block and set `name`, `dir`, `start`, `port`, `url`. Save — the menu
picks it up on the next refresh. No restart needed.

```json
{ "name": "My Tool", "type": "server", "dir": "~/path/to/it", "start": "./run.sh", "port": 9001, "url": "http://localhost:9001" }
```

## Notes
- First Start of a tool whose `.venv` isn't built yet takes ~30s (it sets up the
  environment); the dot turns 🟢 when it's ready. Check `/tmp/control-room-<port>.log` if one won't start.
- Stop uses the port (`lsof -ti:<port> | xargs kill`), so it works no matter how
  the server was started.
- Prefer no extra app at all? I can rebuild this as a self-contained Python
  menu-bar app (`rumps`) instead — just ask.
