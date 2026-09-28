#!/usr/bin/env python3
"""Aggressive evals for the Edit Capture picker + lifecycle.

Every assertion is on what the extension ACTUALLY captured, read back from the saved
PNG: the board carries magenta/cyan corner markers, so a frame that is the board (and
nothing else) has magenta at its top-left and cyan at its bottom-right. That catches
framing the canvas viewport, framing a decoy, and partial crops — the three failures
that kept shipping.

Run:  python3 run_evals.py [--only substring]
"""
import base64, glob, json, os, shutil, subprocess, sys, tempfile, time
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "frames")
EXT_SRC = os.path.abspath(os.path.join(HERE, "..", "extension"))
PORT = 8804
BASE = f"http://127.0.0.1:{PORT}"
SR = "document.getElementById('uplane-edit-capture').shadowRoot"
ASPECT = {"1x1": 1.0, "4x5": 1080 / 1350, "9x16": 1080 / 1920,
          "191x1": 1200 / 628, "16x9": 1920 / 1080, "custom": 1000 / 700}


def build_ext():
    d = tempfile.mkdtemp(prefix="ext-eval-")
    ext = os.path.join(d, "extension")
    shutil.copytree(EXT_SRC, ext)
    m = json.load(open(os.path.join(ext, "manifest.json")))
    m["content_scripts"][0]["matches"] = [f"http://127.0.0.1:{PORT}/*"]
    with open(os.path.join(ext, "manifest.json"), "w") as f: json.dump(m, f, indent=2)
    c = open(os.path.join(ext, "content.js")).read().replace(
        "http://127.0.0.1:8791/edit-session", f"http://127.0.0.1:{PORT}/edit-session")
    with open(os.path.join(ext, "content.js"), "w") as f: f.write(c)
    b = open(os.path.join(ext, "background.js")).read().replace(
        r"/^https:\/\/app\.uplane\.com\/.*[?&]editor=[^&]+/",
        r"/^https?:\/\/(app\.uplane\.com|127\.0\.0\.1:" + str(PORT) + r")\/.*[?&]editor=[^&]+/"
    ).replace('chrome.tabs.query({ url: "https://app.uplane.com/*" })',
              f'chrome.tabs.query({{ url: "http://127.0.0.1:{PORT}/*" }})')
    with open(os.path.join(ext, "background.js"), "w") as f: f.write(b)
    return ext


def frames(kind=None):
    fs = sorted(glob.glob(os.path.join(OUT, "*.png")))
    return [f for f in fs if kind is None or f"-{kind}-" in os.path.basename(f)]


def reset():
    for f in glob.glob(os.path.join(OUT, "*.png")):
        os.remove(f)


def sample(pg, path):
    """Decode a saved PNG inside the browser and report size + corner colours."""
    b64 = base64.b64encode(open(path, "rb").read()).decode()
    return pg.evaluate("""async (b64) => {
      const im = new Image(); im.src = "data:image/png;base64," + b64; await im.decode();
      const c = document.createElement('canvas'); c.width = im.width; c.height = im.height;
      const g = c.getContext('2d'); g.drawImage(im, 0, 0);
      const px = (x, y) => [...g.getImageData(x, y, 1, 1).data].slice(0, 3);
      const near = (a, b, t=40) => a.every((v, i) => Math.abs(v - b[i]) <= t);
      const w = im.width, h = im.height;
      return { w, h, tl: px(4, 4), br: px(w - 5, h - 5),
               magentaTL: near(px(4, 4), [255, 0, 255]),
               cyanBR: near(px(w - 5, h - 5), [0, 255, 255]),
               centre: px(Math.round(w / 2), Math.round(h / 2)),
               hasEdit: near(px(Math.round(w / 2), Math.round(h / 2)), [0, 0, 255], 60) };
    }""", b64)


def framed(pg):
    try:
        txt = pg.evaluate(f"{SR}.querySelector('.panel').textContent.replace(/\\s+/g,' ')")
        i = txt.find("Framed:")
        return txt[i:i + 110] if i >= 0 else ""
    except Exception:
        return ""


def ui(pg):
    return pg.evaluate(f"""(() => {{ const r = {SR}; const n = r.querySelector('.note');
      return {{ pill: r.querySelector('.pill').textContent.trim(),
               note: (n && n.style.display !== 'none')
                     ? n.textContent.replace(/\\s+/g, ' ').trim() : '' }}; }})()""")


def wait_frames(kind, n=1, timeout=18):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if len(frames(kind)) >= n: return True
        time.sleep(0.4)
    return False


def open_editor(ctx, layout="classic", fmt="1x1", decoy="", delay=0):
    pg = ctx.new_page(); pg.set_default_timeout(30000)
    pg.set_viewport_size({"width": 1440, "height": 900})
    pg.goto(f"{BASE}/?editor=ev1&layout={layout}&fmt={fmt}&decoy={decoy}&delay={delay}",
            wait_until="load")
    for _ in range(40):
        pg.wait_for_timeout(300)
        if pg.evaluate("!!document.getElementById('uplane-edit-capture')"): break
    return pg


# ---------------------------------------------------------------- checks
def check_board_frame(pg, path, fmt, label, errs, allow_partial=False):
    s = sample(pg, path)
    if not s["magentaTL"] or not s["cyanBR"]:
        errs.append(f"{label}: frame is not the board (tl={s['tl']} br={s['br']}, "
                    f"{s['w']}x{s['h']}) — framed something larger or cropped")
    ar = s["w"] / s["h"]
    want = ASPECT[fmt]
    if abs(ar - want) / want > 0.02:
        errs.append(f"{label}: aspect {ar:.3f} != format {want:.3f} ({s['w']}x{s['h']})")
    if max(s["w"], s["h"]) != 1200 and not allow_partial:
        errs.append(f"{label}: long edge {max(s['w'], s['h'])} != canonical 1200")
    return s


# ---------------------------------------------------------------- flows
def flow_basic(ctx, layout, fmt, decoy, delay=0):
    errs = []; reset()
    pg = open_editor(ctx, layout, fmt, decoy, delay)
    try:
        if not wait_frames("before", 1, 18):
            return errs + [f"never armed (pill={ui(pg)['pill']!r} note={ui(pg)['note'][:80]!r})"]
        u = ui(pg)
        if u["pill"] != "I’m done":
            errs.append(f"pill is {u['pill']!r}, expected armed")
        b = check_board_frame(pg, frames("before")[0], fmt, "before", errs)
        pg.evaluate("window.editArt('b')"); pg.wait_for_timeout(600)
        pg.evaluate(f"{SR}.querySelector('.pill').click()")
        if not wait_frames("after", 1, 14):
            return errs + [f"Done produced no after-frame (note={ui(pg)['note'][:100]!r})"]
        a = check_board_frame(pg, frames("after")[0], fmt, "after", errs)
        if (a["w"], a["h"]) != (b["w"], b["h"]):
            errs.append(f"before {b['w']}x{b['h']} != after {a['w']}x{a['h']}")
        if errs: errs.append(framed(pg))
    finally:
        pg.close()
    return errs


def flow_zoomswap(ctx, layout, fmt, decoy):
    errs = []; reset()
    pg = open_editor(ctx, layout, fmt, decoy)
    try:
        if not wait_frames("before", 1, 18): return ["never armed"]
        b = check_board_frame(pg, frames("before")[0], fmt, "before", errs)
        pg.evaluate("window.setZoom(window.__zoom * 0.55)"); pg.wait_for_timeout(1200)
        pg.evaluate("window.editArt('b')"); pg.wait_for_timeout(600)
        pg.evaluate(f"{SR}.querySelector('.pill').click()")
        if not wait_frames("after", 1, 14):
            return errs + [f"no after-frame at a different zoom (note={ui(pg)['note'][:90]!r})"]
        a = check_board_frame(pg, frames("after")[0], fmt, "after", errs)
        if (a["w"], a["h"]) != (b["w"], b["h"]):
            errs.append(f"zoom changed the frame size: {b['w']}x{b['h']} -> {a['w']}x{a['h']}")
    finally:
        pg.close()
    return errs


def flow_immediate(ctx, layout, fmt, decoy, delay=0):
    """Start editing at once, never waiting: the baseline must still predate the edit."""
    errs = []; reset()
    pg = open_editor(ctx, layout, fmt, decoy, delay)
    try:
        pg.wait_for_timeout(1600)          # long enough for a provisional frame to be held
        pg.evaluate("""() => { document.getElementById('board').dispatchEvent(
            new PointerEvent('pointerdown', {bubbles:true})); window.editArt('b'); }""")
        if not wait_frames("before", 1, 18): return ["never armed after an immediate edit"]
        s = sample(pg, frames("before")[0])
        if s["hasEdit"]:
            errs.append("baseline contains the edit — it was taken too late")
        if not s["magentaTL"]:
            errs.append(f"baseline is not the board (tl={s['tl']})")
    finally:
        pg.close()
    return errs


def flow_leave_return(ctx, layout, fmt, decoy):
    errs = []; reset()
    pg = open_editor(ctx, layout, fmt, decoy)
    try:
        if not wait_frames("before", 1, 18): return ["never armed"]
        pg.evaluate("history.pushState({},'','/?gone=1')"); pg.wait_for_timeout(1500)
        if pg.evaluate("!!document.getElementById('uplane-edit-capture')"):
            errs.append("UI still mounted after leaving the editor")
        pg.evaluate(f"history.pushState({{}},'','/?editor=ev1&layout={layout}&fmt={fmt}&decoy={decoy}')")
        for _ in range(40):
            pg.wait_for_timeout(300)
            if pg.evaluate("!!document.getElementById('uplane-edit-capture')"): break
        pg.evaluate("window.fitZoom()")
        if not wait_frames("before", 2, 18):
            return errs + [f"did not re-arm on return (pill={ui(pg)['pill']!r})"]
        pg.evaluate("window.editArt('b')"); pg.wait_for_timeout(600)
        pg.evaluate(f"{SR}.querySelector('.pill').click()")
        if not wait_frames("after", 1, 14):
            errs.append(f"Done dead after re-entry (note={ui(pg)['note'][:90]!r})")
    finally:
        pg.close()
    return errs


def flow_reload(ctx, layout, fmt, decoy):
    errs = []; reset()
    pg = open_editor(ctx, layout, fmt, decoy)
    try:
        if not wait_frames("before", 1, 18): return ["never armed"]
        pg.reload(wait_until="load")
        for _ in range(40):
            pg.wait_for_timeout(300)
            if pg.evaluate("!!document.getElementById('uplane-edit-capture')"): break
        if not wait_frames("before", 2, 18):
            return errs + [f"did not arm after reload (pill={ui(pg)['pill']!r})"]
        pg.evaluate("window.editArt('b')"); pg.wait_for_timeout(600)
        pg.evaluate(f"{SR}.querySelector('.pill').click()")
        if not wait_frames("after", 1, 14):
            errs.append("Done dead after reload")
    finally:
        pg.close()
    return errs


def flow_selectall(ctx, layout, fmt, decoy):
    errs = []; reset()
    pg = open_editor(ctx, layout, fmt, decoy)
    try:
        if not wait_frames("before", 1, 18): return ["never armed"]
        pg.evaluate("window.selectAll()"); pg.wait_for_timeout(3000)
        u = ui(pg)
        if u["pill"] != "I’m done":
            errs.append(f"selection made it think the ad is hidden: pill={u['pill']!r} "
                        f"note={u['note'][:90]!r}")
        pg.evaluate("window.editArt('b')"); pg.wait_for_timeout(600)
        pg.evaluate(f"{SR}.querySelector('.pill').click()")
        if not wait_frames("after", 1, 14):
            errs.append("Done blocked while elements were selected")
    finally:
        pg.close()
    return errs


def flow_zoom_in_out(ctx, layout, fmt, decoy):
    errs = []; reset()
    pg = open_editor(ctx, layout, fmt, decoy)
    try:
        if not wait_frames("before", 1, 18): return ["never armed"]
        pg.evaluate("window.setZoom(3.0)"); pg.wait_for_timeout(3200)
        if ui(pg)["pill"] not in ("Fix view",):
            errs.append(f"zoomed far in but pill={ui(pg)['pill']!r} (should warn)")
        pg.evaluate("window.fitZoom()"); pg.wait_for_timeout(3200)
        if ui(pg)["pill"] != "I’m done":
            errs.append(f"did not recover after zooming back: pill={ui(pg)['pill']!r}")
        pg.evaluate("window.editArt('b')"); pg.wait_for_timeout(600)
        pg.evaluate(f"{SR}.querySelector('.pill').click()")
        if not wait_frames("after", 1, 14):
            errs.append("Done dead after a zoom round-trip")
    finally:
        pg.close()
    return errs


def flow_keys(ctx, layout, fmt, decoy):
    errs = []; reset()
    pg = open_editor(ctx, layout, fmt, decoy)
    try:
        if not wait_frames("before", 1, 18): return ["never armed"]
        pg.evaluate("window.editArt('b')"); pg.wait_for_timeout(500)
        pg.evaluate(f"{SR}.querySelector('.pill').click()")
        if not wait_frames("after", 1, 14): return ["no after-frame"]
        pg.wait_for_timeout(800)
        pg.evaluate(f"{SR}.querySelector('textarea:not(.ai)').focus()")
        pg.keyboard.type("hello")
        pg.keyboard.press("Backspace"); pg.keyboard.press("Backspace")
        pg.wait_for_timeout(300)
        v = pg.evaluate(f"{SR}.querySelector('textarea:not(.ai)').value")
        if v != "hel":
            errs.append(f"backspace broken in the why box: value={v!r}")
        if pg.evaluate("window.__deleted") != 0:
            errs.append("our typing triggered the editor's delete-elements shortcut")
    finally:
        pg.close()
    return errs


SCENARIOS = [
    ("classic 1:1  basic",          lambda c: flow_basic(c, "classic", "1x1", "")),
    ("newui  1:1  basic",           lambda c: flow_basic(c, "newui", "1x1", "")),
    ("canvas 1:1  basic",           lambda c: flow_basic(c, "canvas", "1x1", "")),
    ("cssbg  1:1  basic",           lambda c: flow_basic(c, "cssbg", "1x1", "")),
    ("classic 4:5  basic",          lambda c: flow_basic(c, "classic", "4x5", "")),
    ("classic 9:16 basic",          lambda c: flow_basic(c, "classic", "9x16", "")),
    ("classic 1.91 basic",          lambda c: flow_basic(c, "classic", "191x1", "")),
    ("classic 16:9 basic",          lambda c: flow_basic(c, "classic", "16x9", "")),
    ("classic custom-size basic",   lambda c: flow_basic(c, "classic", "custom", "")),
    ("newui  1:1  all decoys",      lambda c: flow_basic(c, "newui", "1x1", "offscreen,tiles,wrapper,shadowbar")),
    ("classic 1:1 all decoys",      lambda c: flow_basic(c, "classic", "1x1", "offscreen,tiles,wrapper,shadowbar")),
    ("classic 1:1 slow image",      lambda c: flow_basic(c, "classic", "1x1", "", 1800)),
    ("newui  4:5  zoom swap",       lambda c: flow_zoomswap(c, "newui", "4x5", "")),
    ("classic 1:1 zoom swap",       lambda c: flow_zoomswap(c, "classic", "1x1", "tiles")),
    ("canvas 16:9 zoom swap",       lambda c: flow_zoomswap(c, "canvas", "16x9", "")),
    ("classic 1:1 edit immediately", lambda c: flow_immediate(c, "classic", "1x1", "")),
    ("newui  1:1 edit immediately",  lambda c: flow_immediate(c, "newui", "1x1", "wrapper")),
    ("classic 1:1 leave+return",    lambda c: flow_leave_return(c, "classic", "1x1", "")),
    ("newui  1:1 leave+return",     lambda c: flow_leave_return(c, "newui", "1x1", "")),
    ("classic 1:1 reload",          lambda c: flow_reload(c, "classic", "1x1", "")),
    ("newui  4:5 reload",           lambda c: flow_reload(c, "newui", "4x5", "")),
    ("classic 1:1 select-all",      lambda c: flow_selectall(c, "classic", "1x1", "")),
    ("newui  1:1 select-all",       lambda c: flow_selectall(c, "newui", "1x1", "tiles")),
    ("classic 1:1 zoom in+out",     lambda c: flow_zoom_in_out(c, "classic", "1x1", "")),
    ("newui  1:1 zoom in+out",      lambda c: flow_zoom_in_out(c, "newui", "1x1", "")),
    ("classic 1:1 backspace",       lambda c: flow_keys(c, "classic", "1x1", "")),
]


def main():
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]
    os.makedirs(OUT, exist_ok=True)
    srv = subprocess.Popen([sys.executable, "-u", os.path.join(HERE, "fixture.py")],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           env={**os.environ, "EVAL_PORT": str(PORT)})
    time.sleep(1.5)
    ext = build_ext()
    passed, failed = [], []
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                tempfile.mkdtemp(prefix="prof-eval-"),
                headless=os.environ.get("EVAL_HEADLESS") == "1",
                timeout=90000,
                viewport={"width": 1440, "height": 900},
                args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}",
                      "--no-first-run", "--no-default-browser-check"])
            time.sleep(2.5)
            for name, fn in SCENARIOS:
                if only and only not in name: continue
                t0 = time.time()
                try:
                    errs = fn(ctx)
                except Exception as e:
                    errs = [f"harness error: {type(e).__name__}: {str(e)[:160]}"]
                dt = time.time() - t0
                if errs:
                    failed.append((name, errs))
                    print(f"  FAIL  {name:30} {dt:4.1f}s")
                    for e in errs: print(f"        - {e}")
                else:
                    passed.append(name)
                    print(f"  pass  {name:30} {dt:4.1f}s")
            ctx.close()
    finally:
        srv.terminate(); time.sleep(0.3)
    print(f"\n  {len(passed)} passed, {len(failed)} failed, of {len(passed)+len(failed)} run")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
