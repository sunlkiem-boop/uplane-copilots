#!/usr/bin/env python3
"""
Uplane Control Room — a SwiftBar menu-bar plugin.

No args  -> prints the SwiftBar menu (one line per copilot, live status dot,
            submenu with Open / Start / Stop).
With args -> performs an action SwiftBar invoked on click:
            start|stop <name> , start-all , stop-all

Config: copilots.json next to this file. Refresh interval = the ".5s." in the
filename (every 5 seconds). Uses only the standard library, so any python3 works.
"""
import json
import os
import socket
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
SELF = os.path.realpath(__file__)
CONFIG = os.path.join(HERE, "copilots.json")


def load_config():
    with open(CONFIG) as f:
        return json.load(f)


def load_tools():
    return load_config()["tools"]


def fileurl(path) -> str:
    import urllib.parse

    return "file://" + urllib.parse.quote(os.path.expanduser(path))


def port_up(port) -> bool:
    try:
        with socket.socket() as s:
            s.settimeout(0.25)
            return s.connect_ex(("127.0.0.1", int(port))) == 0
    except Exception:
        return False


def _slug(name):
    import re

    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "tool"


def _logpath(tool):
    return os.path.join(HERE, "logs", f"{_slug(tool['name'])}.log")


# Tool dirs SwiftBar cannot see. `/bin/bash -lc` reads BASH's profile, but this
# machine's shell is zsh, so PATH additions in ~/.zshrc/~/.zprofile never load and
# `uv` came back "command not found" — Start looked like it did nothing at all
# (the only trace was one line in logs/<tool>.log). Every run.sh here needs uv, so
# this hit all four tools. Inject the paths rather than switching to `zsh -lc`, so
# it stays correct whichever login shell this machine ends up using.
_TOOL_PATHS = ["~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "~/.cargo/bin"]


def _launch_env():
    env = dict(os.environ)
    extra = [os.path.expanduser(p) for p in _TOOL_PATHS if os.path.isdir(os.path.expanduser(p))]
    have = (env.get("PATH") or "").split(":")
    env["PATH"] = ":".join([p for p in extra if p not in have] + have)
    return env


def start(tool):
    d = os.path.expanduser(tool["dir"])
    # Don't start a second copy. Two Ad-Bot helpers both connect to Slack and both
    # answer every event, so one dropped creative got two "Set direction &
    # Generate" buttons — and only one of them held the port, so the port-based
    # stop below could not clean the other up.
    if tool.get("port") and _running_pids(tool):
        return
    os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
    log = open(_logpath(tool), "a")
    subprocess.Popen(
        ["/bin/bash", "-lc", tool.get("start", "./run.sh")],
        cwd=d, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        env=_launch_env(),
    )


def _running_pids(tool) -> list:
    """Every pid of this tool, found two ways.

    By port AND by the command line running out of the tool's own directory: an
    instance whose port bind lost the race still runs (and, for the Ad-Bot, still
    serves Slack), so a port-only lookup reports it as absent and leaves it
    running forever."""
    pids = set()
    port = tool.get("port")
    if port:
        r = subprocess.run(f"lsof -ti:{port}", shell=True, capture_output=True, text=True)
        pids |= {p for p in r.stdout.split() if p.strip()}
    d = os.path.realpath(os.path.expanduser(tool.get("dir", "")))
    if d and os.path.isdir(d):
        # run.sh execs a RELATIVE interpreter path (".venv/bin/python app.py"), so
        # matching an absolute path finds nothing. Match the script name, then keep
        # only pids whose CWD is this tool's dir — several copilots run an app.py.
        r = subprocess.run(["pgrep", "-f", r"\.venv/bin/python app\.py"],
                           capture_output=True, text=True)
        for pid in r.stdout.split():
            c = subprocess.run(["lsof", "-a", "-d", "cwd", "-p", pid, "-Fn"],
                               capture_output=True, text=True)
            cwd = next((l[1:] for l in c.stdout.splitlines() if l.startswith("n")), "")
            if os.path.realpath(cwd) == d:
                pids.add(pid)
    return sorted(pids)


def stop(tool):
    pids = _running_pids(tool)
    if pids:
        subprocess.run(["kill"] + pids)


def open_terminal(tool):
    """Pop a Terminal window running the helper (visible logs) via a .command."""
    d = os.path.expanduser(tool.get("dir", ""))
    if not os.path.isdir(d):
        return
    launchers = os.path.join(HERE, "launchers")
    os.makedirs(launchers, exist_ok=True)
    f = os.path.join(launchers, f"start-{_slug(tool['name'])}.command")
    with open(f, "w") as fh:
        fh.write(f'#!/bin/bash\ncd "{d}" && exec {tool.get("start", "./run.sh")}\n')
    os.chmod(f, 0o755)
    subprocess.run(["open", f])


# --------------------------------------------------------------- actions ---
def run_action(argv):
    tools = load_tools()
    by_name = {t["name"]: t for t in tools}
    act = argv[1]
    if act in ("start-all", "stop-all"):
        for t in tools:
            if t.get("type") == "server":
                (start if act == "start-all" else stop)(t)
        return
    name = " ".join(argv[2:])
    t = by_name.get(name)
    if not t:
        return
    if act == "open-terminal":
        open_terminal(t)
    elif t.get("type") == "server":
        (start if act == "start" else stop)(t)


# ------------------------------------------------------- client memory ----
def memory_section(cfg):
    """Scaffold: per-client cross-copilot memory. Today it surfaces the shared
    learnings the copilots/agents log; a richer per-client view comes later."""
    mem = cfg.get("memory") or {}
    clients_dir = os.path.expanduser(mem.get("clients_dir", "~/Desktop/uplane/clients"))
    csv_path = os.path.expanduser(mem.get("learnings_csv", "~/Desktop/uplane/shared-learnings.csv"))

    counts = {}
    if os.path.isfile(csv_path):
        import csv as _csv
        try:
            with open(csv_path, newline="") as f:
                for row in _csv.DictReader(f):
                    c = (row.get("client") or "").strip().lower()
                    if c and (row.get("learning") or "").strip():
                        counts[c] = counts.get(c, 0) + 1
        except Exception:
            pass

    clients = []
    if os.path.isdir(clients_dir):
        clients = sorted(
            d for d in os.listdir(clients_dir)
            if os.path.isdir(os.path.join(clients_dir, d)) and not d.startswith(".")
        )

    print("---")
    print("📓 Client Memory | size=12 color=#8b93a3")
    print("🗒️ Open Notes & Memory console | href=http://localhost:8787/notes")
    if not clients:
        print("No clients yet | color=#8b93a3")
    for c in clients:
        n = counts.get(c.lower(), 0)
        print(f"{c}   ({n} learning{'' if n == 1 else 's'})")
        print(f"--Open shared learnings | href={fileurl(csv_path)}")
        print(f"--Client folder | href={fileurl(os.path.join(clients_dir, c))}")
    print(f"All shared learnings (CSV)… | href={fileurl(csv_path)} color=#8b93a3")
    print("Richer per-client memory — coming soon | color=#8b93a3 size=11")


# --------------------------------------------------------------- render ----
def render():
    try:
        cfg = load_config()
        tools = cfg["tools"]
    except Exception as e:
        print("🚦 !")
        print("---")
        print(f"Config error: {e} | color=red")
        return

    servers = [t for t in tools if t.get("type") == "server"]
    running = sum(1 for t in servers if port_up(t["port"]))
    print(f"🚦 {running}")  # menu-bar title: number of servers up
    print("---")
    print(f"Uplane Control Room  ·  {running}/{len(servers)} running | size=12 color=#8b93a3")
    print("---")

    for t in tools:
        name = t["name"]
        ty = t.get("type", "info")
        if ty == "server":
            up = port_up(t["port"])
            dot = "🟢" if up else "⚪️"
            print(f"{dot}  {name}   :{t['port']}")
            print(f"--Open in browser | href={t['url']}")
            print(f'--▶︎ Start | shell="{SELF}" param1=start param2="{name}" terminal=false refresh=true')
            print(f'--■ Stop | shell="{SELF}" param1=stop param2="{name}" terminal=false refresh=true')
            print(f'--⌨ Open in Terminal | shell="{SELF}" param1=open-terminal param2="{name}" terminal=false')
            print(f"--View log | color=#8b93a3 href={fileurl(_logpath(t))}")
        elif ty == "chrome":
            print(f"🧩  {name}")
            url = t.get("url", "chrome://extensions")
            print(f'--Open in Chrome | shell=open param1=-a param2="Google Chrome" param3="{url}" terminal=false')
            if t.get("note"):
                print(f"--{t['note']} | color=#8b93a3")
        else:
            print(f"•  {name} | color=#8b93a3")
            if t.get("note"):
                print(f"--{t['note']} | color=#8b93a3")

    memory_section(cfg)

    print("---")
    print(f'▶︎ Start all servers | shell="{SELF}" param1=start-all terminal=false refresh=true')
    print(f'■ Stop all servers | shell="{SELF}" param1=stop-all terminal=false refresh=true')
    print("↻ Refresh | refresh=true")
    print(f"Edit copilots… | href=file://{CONFIG}")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] in ("start", "stop", "start-all", "stop-all", "open-terminal"):
        run_action(sys.argv)
    else:
        render()
