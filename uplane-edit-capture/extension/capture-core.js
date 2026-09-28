/* Uplane Edit Capture — core.
 *
 * Passively watches a page for the edits a human makes (text changes, control
 * frames), diffs them, and offers a small panel to
 * annotate WHAT was fixed and WHY before posting the session to the local
 * helper (:8791) → context graph `edit_event` rows.
 *
 * Design rules:
 *  - passive only: capture-phase listeners, never preventDefault, everything
 *    inside try/catch — this script must be unable to break the editor.
 *  - `event.isTrusted` filter: synthetic events (e.g. the Ad-Bot driver's
 *    execCommand typing) are ignored — we record humans, not bots.
 *  - shadow DOM UI so page CSS and our CSS can't fight.
 *
 * Loadable two ways: as an extension content script (content.js calls init),
 * or directly via <script> in a test page (auto-init when ?autoinit=1).
 */
(function () {
  // Injected twice (declarative match + the worker adopting an open tab) must be a
  // no-op, not a second UI.
  if (window.__uplaneEditCaptureBooted) return;
  window.__uplaneEditCaptureBooted = true;
  "use strict";
  if (window.__uplaneEditCapture) return; // double-inject guard

  const ASPECTS = ["ai_look", "text_render", "product_wrong", "margins", "copy", "offer", "none"];
  // teardown() nulls the session id and init() never regenerated it — the id was only
  // ever minted when the script first loaded. Leaving and re-entering the editor
  // (which the SPA does on its own) therefore posted `session: null`, which fails the
  // helper's SESSION_RE and comes back as a bare 400.
  const newSession = () =>
    "es-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 7);

  const state = {
    endpoint: "http://127.0.0.1:8791/edit-session",
    by: "sun",
    session: newSession(),
    ui: null,
    open: false,
    framesSent: 0,
    summary: null,          // {note, backend} once fetched
    geom: null,             // artboard design size + zoom of the last frame
    baseGeom: null,
    waitReason: "",
    watch: null,
    ctx: null,              // {client_id, client_name, source, known[]} from the helper
    clientPick: "",         // only set when inference genuinely failed
    status: "arming",       // arming | ready | blocked | comparing
    statusMsg: "",          // the sentence shown to the user, never only a tooltip
    postError: "",          // helper would not store it (distinct from a view problem)
    userTouched: false,     // has the user interacted since arming began?
    sawBoard: false,        // don't count clicks from before the ad even rendered
    held: null,             // a good frame, kept until it is the right one to commit
    bypassClip: false,      // ONLY set by an explicit "Capture now"
    untouch: null,
    unswallow: null,
    okUntil: 0,             // keep the "go ahead" confirmation up briefly
    frameHow: "",           // what the last frame actually framed
    baseHow: "",            // …and what the baseline framed, to catch a mismatch
    grabError: "",
  };
  const api = () => state.endpoint.replace(/\/edit-session\/?$/, "");

  // ---------- frames (before/after snapshots of the creative) ----------
  // Find the creative itself. Two things make this harder than "grab the big image":
  //  1. A full-tab screenshot is the wrong thing to diff — it carries the sidebar, the
  //     properties panel and this extension's own pill, so a panel opening between the
  //     two frames reads as "the creative changed".
  //  2. In the Uplane editor the creative is NOT one image. It is a composition of
  //     separate elements (that is why the properties panel has per-element Fill and
  //     Stroke), so picking the largest <img> framed ONE PHOTO out of the ad — measured
  //     260x300 out of a 700x700 artboard.
  // So: find the artboard. It is the element whose box matches one of the formats the
  // editor itself offers, which is a far stronger signal than size alone.
  const AD_RATIOS = [1, 9 / 16, 4 / 5, 1.91, 16 / 9];   // the format bar: 1:1 9:16 4:5 1.91:1 16:9
  const RATIO_TOL = 0.025;

  function standardRatio(r) {
    if (r.height < 1) return 0;
    const a = r.width / r.height;
    for (const t of AD_RATIOS) if (Math.abs(a - t) / t <= RATIO_TOL) return t;
    return 0;
  }

  // visible area actually inside the viewport — an artboard zoomed past the window edge
  // cannot be captured beyond what is on screen, so score it by what we can see
  function shownArea(r) {
    const w = Math.min(r.right, innerWidth) - Math.max(r.left, 0);
    const h = Math.min(r.bottom, innerHeight) - Math.max(r.top, 0);
    return w > 0 && h > 0 ? w * h : 0;
  }

  function mine(el) { return !!(el.closest && el.closest("#uplane-edit-capture")); }

  // Anchors, strongest first. The generic "big element with a standard aspect ratio"
  // heuristic is LAST because it is genuinely unreliable: measured at 57% zoom it
  // picked the 653x823 stage area (ratio 0.793, within tolerance of 4:5) over the
  // shrunken 423px artboard, because the stage was larger. Size cannot identify the
  // board once the board can be zoomed smaller than its own container.
  //
  // Sun's observation is the fix: the artboard is the thing with the drop shadow. In
  // the Uplane DOM that is `DIV.relative shadow-xl`, sitting inside
  // `DIV.static-editor-root` under a transformed `origin-top-left` zoom wrapper. Those
  // are structural, so they hold at any zoom and any format.
  const EDITOR_ROOT = '[class*="static-editor-root"]';
  // Panels to ignore. This MUST be size-aware: Uplane wraps the whole app in
  // `DIV.group/sidebar-wrapper`, so a plain closest('[class*="sidebar"]') matched every
  // element on the page and the picker rejected everything ("Looking for the ad on the
  // page…" forever). A real panel is a minority of the viewport; a layout wrapper is
  // not, so anything covering 45%+ of the screen is a wrapper and never a panel.
  const PANEL_RE = /inspector|sidebar|rail|thumb|toolbar/i;

  function inPanel(el) {
    const vp = innerWidth * innerHeight;
    for (let p = el; p && p.nodeType === 1; p = p.parentElement) {
      const cls = (p.getAttribute && p.getAttribute("class")) || "";
      if (!PANEL_RE.test(cls)) continue;
      const r = p.getBoundingClientRect();
      if (r.width * r.height < vp * 0.45) return true;   // a genuine side panel
    }
    return false;
  }

  // THE decisive signal, and the one I kept missing: the artboard lives INSIDE the
  // canvas's zoom/pan transform. Everything else on screen — the canvas viewport, the
  // rails, the inspector — does not. Two failures came from ignoring it:
  //   * picked a 1496x811 canvas viewport for a 1:1 ad, so the frame moved every time
  //     the zoom changed and the diff reported "canvas zoom adjusted from 81% to 87%"
  //     instead of the edit;
  //   * picked an off-screen shadowed decoy and reported "0% of the ad is on screen".
  // In both, the tell was `at 100% zoom` while the control read 102%/81%: a scale of
  // exactly 1 means the element is not under the transform, so it is not the artboard.
  //
  // Uplane composes `translate(x,y) scale(s)` on the wrapper, so its transform is
  // non-identity at EVERY zoom, 100% included.
  function underZoom(el) {
    for (let p = el.parentElement, n = 0; p && n < 6; p = p.parentElement, n++) {
      const tr = getComputedStyle(p).transform;
      if (tr && tr !== "none") return true;
    }
    return false;
  }

  function score(el, how) {
    const rect = el.getBoundingClientRect();
    if (rect.width < 80 || rect.height < 80) return null;
    const area = shownArea(rect);
    if (area < 5000) return null;                        // effectively off-screen
    return { el, rect, how, area, cov: coverage(el, rect).ratio,
             inZoom: underZoom(el), ratio: !!standardRatio(rect) };
  }

  // Preference order, strongest first: under the zoom transform, then matching one of
  // the format chips, then bigger. Ratio is a preference and not a requirement, because
  // a custom canvas size is legitimate and must still be capturable.
  const rankOf = (s) => (s.inZoom ? 4 : 0) + (s.ratio ? 2 : 0);

  function bestOf(els, how) {
    let best = null;
    for (const el of els) {
      const s = score(el, how);
      if (!s) continue;
      if (!best) { best = s; continue; }
      const a = [s.cov >= VISIBLE_MIN ? 1 : 0, rankOf(s), s.cov >= VISIBLE_MIN ? s.area : s.cov];
      const b = [best.cov >= VISIBLE_MIN ? 1 : 0, rankOf(best), best.cov >= VISIBLE_MIN ? best.area : best.cov];
      for (let i = 0; i < 3; i++) {
        if (a[i] > b[i]) { best = s; break; }
        if (a[i] < b[i]) break;
      }
    }
    return best;
  }

  function shadowedBoard(root) {
    const out = [];
    for (const el of root.querySelectorAll("*")) {
      const r = el.getBoundingClientRect();
      if (r.width < 80 || r.height < 80) continue;       // cheap reject before styles
      if (mine(el) || inPanel(el)) continue;
      const cs = getComputedStyle(el);
      if (!cs.boxShadow || cs.boxShadow === "none") continue;
      if (cs.visibility === "hidden" || cs.opacity === "0") continue;
      if (!el.querySelector("img, canvas, svg")) continue;
      out.push(el);
    }
    return out;
  }

  // Everything under the canvas transform that could be the board. Cheap, and it is
  // tried before any shadow/ratio guessing.
  function zoomedBoards(root) {
    const out = [];
    for (const el of root.querySelectorAll("*")) {
      const r = el.getBoundingClientRect();
      if (r.width < 120 || r.height < 120) continue;
      if (mine(el) || inPanel(el)) continue;
      if (!underZoom(el)) continue;
      if (!el.querySelector("img, canvas, svg") && el.children.length === 0) continue;
      out.push(el);
    }
    // tightest first: the board is the innermost element that still holds the artwork
    out.sort((a, b) => shownArea(b.getBoundingClientRect()) - shownArea(a.getBoundingClientRect()));
    return out;
  }

  function stageTarget() {
    const vp = innerWidth * innerHeight;
    const root = document.querySelector(EDITOR_ROOT) || document.body;
    let fallback = null;
    const consider = (cand) => {
      if (!cand) return null;
      if (cand.cov >= VISIBLE_MIN) return cand;
      if (!fallback || cand.cov > fallback.cov) fallback = cand;
      return null;                                      // not really visible: keep looking
    };

    // (a0) under the canvas zoom transform — the only signal that cannot be confused
    // with the viewport that contains it.
    //
    // If such a board exists we return it AND STOP, even when it is only partly on
    // screen. Falling through on low coverage looked reasonable and was wrong: in the
    // editor build where the canvas viewport also carries a shadow, zooming the board
    // out of view handed the capture to that fully-visible viewport instead — silently
    // reframing onto the wrong element and, worse, stopping the "your ad is out of
    // view" warning entirely. A partly-visible board beats a perfectly-visible
    // not-the-board every time; low coverage is something to REPORT, not to route
    // around. (Caught by eval "newui 1:1 zoom in+out".)
    const zoomed = zoomedBoards(root);
    if (zoomed.length) {
      return bestOf(zoomed.filter((el) => standardRatio(el.getBoundingClientRect())),
                    "artboard (in canvas)")
          || bestOf(zoomed, "artboard (in canvas, custom size)");
    }

    // (a) shadowed artboard — only reached when nothing sits under a canvas transform
    let hit = consider(bestOf(shadowedBoard(root), "artboard (shadow)"));
    if (hit) return hit;
    if (root !== document.body) {
      hit = consider(bestOf(shadowedBoard(document.body), "artboard (shadow, page-wide)"));
      if (hit) return hit;
    }

    // (b) a canvas, when the editor is canvas-based
    hit = consider(bestOf(
      Array.prototype.filter.call(document.querySelectorAll("canvas"), (c) => !mine(c)),
      "editor canvas"));
    if (hit) return hit;

    // (c) standard ad ratio, big, holding artwork
    let cands = [];
    for (const el of root.querySelectorAll("*")) {
      const r = el.getBoundingClientRect();
      if (r.width < 160 || r.height < 160) continue;
      if (mine(el) || inPanel(el)) continue;
      if (shownArea(r) < vp * 0.06) continue;
      if (!standardRatio(r)) continue;
      if (el === document.body || el === document.documentElement) continue;
      if (!el.querySelector("img, canvas, svg")) {
        const bg = getComputedStyle(el);
        const painted = (bg.backgroundImage && bg.backgroundImage !== "none")
          || (bg.backgroundColor && !/^rgba\(0, 0, 0, 0\)$|^transparent$/.test(bg.backgroundColor));
        if (!painted || el.children.length === 0) continue;
      }
      cands.push(el);
    }
    if (cands.length) {
      const box = (el) => { const r = el.getBoundingClientRect();
        return Math.round(r.width) + "x" + Math.round(r.height); };
      const at = (el) => { const r = el.getBoundingClientRect();
        return Math.round(r.left) + "," + Math.round(r.top); };
      const seen = new Map();
      for (const el of cands) {
        if (!seen.has(box(el))) seen.set(box(el), new Set());
        seen.get(box(el)).add(at(el));
      }
      const solo = cands.filter((el) => seen.get(box(el)).size < 3);   // not a tile gallery
      if (solo.length) cands = solo;
      cands.sort((a, b) => shownArea(b.getBoundingClientRect())
                         - shownArea(a.getBoundingClientRect()));
      let pick = cands[0];
      for (;;) {   // descend to the tightest box, so an outer wrapper cannot win
        const inner = cands.find((c) => c !== pick && pick.contains(c)
          && shownArea(c.getBoundingClientRect())
             >= shownArea(pick.getBoundingClientRect()) * 0.85);
        if (!inner) break;
        pick = inner;
      }
      hit = consider(bestOf([pick], "artboard (ratio guess)"));
      if (hit) return hit;
    }

    // (d) the biggest single piece of artwork
    hit = consider(bestOf(Array.prototype.filter.call(document.querySelectorAll("img"),
      (el) => !mine(el) && el.offsetParent !== null && !inPanel(el)), "largest image"));
    if (hit) return hit;

    return fallback;   // nothing fully visible: hand back the most-visible thing found
  }

  // The editor's canvas zooms (Sun was at 57%). getBoundingClientRect() reports the
  // ZOOMED box; offsetWidth reports the unzoomed layout box, so their ratio IS the
  // zoom factor — no need to scrape the "57%" readout, which is UI text and would
  // break the moment that control changes.
  //
  // This matters because a before-frame shot at one zoom and an after-frame at
  // another are different crops of different sizes, and the interpreter then reports
  // the REFRAMING as the edit ("cropped/reframed from a square 1:1 composition to a
  // taller/narrower frame, clipping the headline"). Both frames are therefore
  // resampled to one canonical size derived from the DESIGN aspect, so zoom cancels
  // out entirely.
  const CANON = 1200;

  // The rect alone is not what you can see: the editor's stage uses overflow:hidden,
  // so at 100% zoom a 742px board inside a 653px stage is VISUALLY cut off while its
  // rect still reports the full 742 — cropping to that rect pastes sidebar pixels into
  // the frame.
  //
  // Intersecting ancestor rects geometrically does NOT work here, and that is not a
  // detail: overflow:hidden on a statically-positioned ancestor does not clip
  // absolutely-positioned descendants at all, so the arithmetic answer disagrees with
  // what is on screen (it reported the board clipped at every zoom). Reproducing CSS
  // containing-block rules is the wrong amount of work for the question.
  //
  // So hit-test instead: probe a grid of points inside the board and ask the document
  // what is actually painted there. That is the same thing the screenshot will see, and
  // it catches occlusion by floating panels too, which geometry never would.
  // Which container actually bounds what you can see of the board — the nearest
  // scrolling/clipping ancestor. Anything drawn inside it is on screen; anything
  // outside it is another region of the app.
  function stageContainer(el) {
    for (let p = el.parentElement; p; p = p.parentElement) {
      const cs = getComputedStyle(p);
      if (/hidden|auto|scroll|clip/.test(cs.overflow + cs.overflowX + cs.overflowY)) return p;
    }
    return document.querySelector(EDITOR_ROOT) || document.body;
  }

  // Is the board actually on screen? Probe a grid of points and ask the document what
  // is painted there — the same thing the screenshot will see, and unlike arithmetic it
  // needs no model of CSS containing blocks.
  //
  // The subtlety that bit once: a hit is NOT required to be the board itself. Selecting
  // elements in the editor puts a selection overlay (marquee, handles, the floating
  // toolbar) above the board in a SEPARATE layer, so with 12 elements selected every
  // probe hit the overlay, none matched the board, and a fully visible ad scored 17%
  // and was declared clipped. Editor chrome drawn over the board is not the board being
  // cut off. What counts as hidden is the board being replaced by a DIFFERENT REGION of
  // the app — the inspector, a rail — or being off-viewport entirely.
  // A probe counts as HIDDEN only when the board has really been replaced there: the
  // point is off-viewport, or a different region of the app (inspector, rail) is drawn
  // over it, or a full-screen modal covers it. Anything else painted on top — the
  // selection marquee, a floating toolbar, snap guides — is editor chrome over a board
  // that is still on screen. Requiring hits to belong to the board's own container was
  // too strict and produced "only 3% of the ad is on screen" with elements selected,
  // and "94%" (below the old 95% gate) whenever a floating toolbar overlapped the top.
  const VISIBLE_MIN = 0.85;

  function coverage(el, r) {
    const N = 6, inset = 2;
    const vp = innerWidth * innerHeight;
    let seen = 0, covered = 0, off = 0, total = 0;
    for (let iy = 0; iy < N; iy++) {
      for (let ix = 0; ix < N; ix++) {
        const x = r.left + inset + (r.width - inset * 2) * (ix / (N - 1));
        const y = r.top + inset + (r.height - inset * 2) * (iy / (N - 1));
        total++;
        if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) { off++; continue; }
        const hit = document.elementFromPoint(x, y);
        if (!hit) continue;
        if (hit === el || el.contains(hit) || mine(hit)) { seen++; continue; }
        if (inPanel(hit)) { covered++; continue; }
        const hr = hit.getBoundingClientRect();
        // A full-screen element that does not contain the board = an open drawer,
        // dialog or dimming backdrop sitting OVER it. Different situation from
        // "zoomed out of view" — the old scalar lumped both into one number and told
        // the user "0% of the ad is on screen" while the ad sat fully visible under a
        // folder-picker backdrop.
        if (hr.width * hr.height >= vp * 0.6 && !hit.contains(el)) { covered++; continue; }
        seen++;                                       // small chrome over a visible board
      }
    }
    return { ratio: total ? seen / total : 0,
             covered: total ? covered / total : 0,
             off: total ? off / total : 0 };
  }

  // Design size vs on-screen size. getBoundingClientRect() reports the ZOOMED box;
  // offsetWidth reports the unzoomed layout box, so their ratio IS the canvas zoom —
  // no need to scrape the "57%" readout, which is UI text.
  function boardGeom(el, rect) {
    const ow = el.offsetWidth || Math.round(rect.width);
    const oh = el.offsetHeight || Math.round(rect.height);
    const scale = ow ? rect.width / ow : 1;
    const c = coverage(el, rect);
    const cov = c.ratio;
    const long = Math.max(ow, oh) || 1;
    return { designW: ow, designH: oh, scale,
             clipped: cov < VISIBLE_MIN,
             coveredBy: cov < VISIBLE_MIN && c.covered > c.off,   // an overlay, not the zoom
             cov: Math.round(cov * 100),
             canonW: Math.max(1, Math.round(CANON * ow / long)),
             canonH: Math.max(1, Math.round(CANON * oh / long)) };
  }

  // Is everything in the frame actually rendered? Sun, 2026-08-23: "might take some
  // time since it takes time for image to load". A frame shot mid-load bakes in blank
  // boxes, and then the diff reports the images appearing as if they were the edit.
  function targetSettled(t) {
    if (!t) return false;
    const imgs = [t.el, ...t.el.querySelectorAll("img")].filter((e) => e.tagName === "IMG");
    for (const im of imgs) if (!im.complete || im.naturalWidth === 0) return false;
    return true;
  }

  // Records why a capture failed, and what it framed. Returning a bare null meant a
  // missing permission, a tainted canvas and "no change" all looked the same
  // downstream — the panel said "need a before and an after frame" and gave you no
  // way to know which of the three had happened.
  let lastGrabError = "";

  const raf = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

  function loadImage(dataURL) {
    return new Promise((res, rej) => {
      const im = new Image();
      im.onload = () => res(im);
      im.onerror = () => rej(new Error("screenshot did not decode"));
      im.src = dataURL;
    });
  }

  // Crop a viewport screenshot down to a CSS rect. captureVisibleTab returns physical
  // pixels, so everything scales by naturalWidth/innerWidth rather than by
  // devicePixelRatio, which is wrong under browser zoom.
  async function cropTo(dataURL, rect, g) {
    const im = await loadImage(dataURL);
    const s = im.naturalWidth / innerWidth;
    const x = Math.max(0, Math.round(rect.left * s));
    const y = Math.max(0, Math.round(rect.top * s));
    const w = Math.min(im.naturalWidth - x, Math.round(rect.width * s));
    const h = Math.min(im.naturalHeight - y, Math.round(rect.height * s));
    if (w < 40 || h < 40) return dataURL;   // off-screen or collapsed: keep the frame
    const cv = document.createElement("canvas");
    // canonical output, so a 57% zoom and a 100% zoom yield identical frames
    cv.width = g ? g.canonW : w;
    cv.height = g ? g.canonH : h;
    const cx = cv.getContext("2d");
    cx.imageSmoothingQuality = "high";
    cx.drawImage(im, x, y, w, h, 0, 0, cv.width, cv.height);
    return cv.toDataURL("image/png");
  }

  async function grabFrame() {
    lastGrabError = "";
    const target = stageTarget();

    // 1st choice: read the canvas directly — already exactly the creative.
    if (target && target.how === "editor canvas") {
      try {
        state.frameHow = `${target.how} \u2192 ${g.canonW}x${g.canonH}`;
        state.geom = g;
        return target.el.toDataURL("image/png");
      }
      catch (e) { lastGrabError = "editor canvas is cross-origin tainted"; }
    }

    // Otherwise screenshot the tab and crop. Needs host permission for the page —
    // without it chrome.runtime.lastError is set and dataURL comes back null.
    if (typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.sendMessage) {
      lastGrabError = joinErr(lastGrabError, "no extension context for a tab screenshot");
      return null;
    }
    let shot = null;
    const host = state.ui && state.ui.host;
    try {
      if (host) host.style.visibility = "hidden";   // keep our own UI out of the frame
      await raf();
      const resp = await chrome.runtime.sendMessage({ type: "capture" });
      if (resp && resp.dataURL) shot = resp.dataURL;
      else lastGrabError = joinErr(lastGrabError, resp && resp.err
        ? `tab screenshot refused: ${resp.err}`
        : "tab screenshot returned nothing");
    } catch (e) {
      lastGrabError = joinErr(lastGrabError, `tab screenshot threw: ${String(e).slice(0, 90)}`);
    } finally {
      if (host) host.style.visibility = "";
    }
    if (!shot) return null;

    const g = target ? boardGeom(target.el, target.rect) : null;
    if (g && g.clipped && !state.bypassClip) {
      state.grabError = lastGrabError = g.coveredBy
        ? "a panel or dialog is covering the ad — close it, then try again "
          + "(a frame of the overlay cannot be compared against the ad)"
        : `only part of the artboard is visible at ${Math.round(g.scale * 100)}% zoom — `
          + "use the editor's zoom control to fit the whole ad on screen, then try again "
          + `(only ${g.cov}% of it is on screen, and a partial frame cannot be compared `
          + "against a full one)";
      return null;
    }
    state.geom = g;
    if (!target) {
      // nothing identifiable to frame: the whole viewport is still comparable, and
      // saying so is better than silently diffing the browser UI
      state.frameHow = "whole viewport (no creative element found)";
      return shot;
    }
    try {
      const out = await cropTo(shot, target.rect, g);
      // A deliberately partial frame must SAY so, or the diff that follows reads as
      // if it covered the whole ad.
      const partial = g.clipped ? ` · PARTIAL VIEW (${g.cov}% of the ad)` : "";
      state.frameHow = `${target.how}${partial} · ${g.designW}x${g.designH} at `
                     + `${Math.round(g.scale * 100)}% zoom \u2192 ${g.canonW}x${g.canonH}`;
      return out;
    } catch (e) {
      state.frameHow = "whole viewport (crop failed)";
      return shot;
    }
  }

  function joinErr(a, b) { return a ? `${a}; ${b}` : b; }

  // The baseline used to fire on a fixed 1.2s timer. If the creative had not rendered
  // by then the "before" frame was a whole-viewport shot while "after" was a crop of
  // the artwork, so the two frames were not comparable and every diff was noise.
  // Wait for something framable, then baseline; give up after 8s and shoot anyway.
  // ARMING.
  //
  // The old design made arming a gate: wait until the board holds still for two
  // consecutive readings, then capture. Two problems, both reported by Sun:
  //   - if the window (or the editor's Auto zoom) keeps moving, "still" never happens
  //     and it sits in the looking phase forever;
  //   - if he starts editing before the capture lands, the "before" frame already
  //     contains the edit, so the diff silently shows nothing. That is the worst
  //     possible failure: a confident answer about nothing.
  //
  // So: grab a PROVISIONAL frame the moment one is possible, keep replacing it while
  // the page is still changing, and FREEZE it the instant the user touches the editor.
  // Whatever was on screen before their first interaction is by definition the
  // "before" frame, however busy the page was. Editing early is now safe, and there is
  // nothing to wait for.
  const QUIET_MS = 350;      // no mutations for this long counts as settled
  const FORCE_AFTER = 6000;  // never hold a good frame longer than this
  let lastActivity = 0, mo = null, ro = null, watching = null;

  const bump = () => { lastActivity = Date.now(); };

  function watchStage(el) {
    if (watching === el) return;
    stopWatching();
    try {
      mo = new MutationObserver(bump);
      mo.observe(el, { subtree: true, childList: true, attributes: true, characterData: true });
      ro = new ResizeObserver(bump); ro.observe(el);
      watching = el;
    } catch (e) { /* observers unavailable: the deadline still applies */ }
  }

  function stopWatching() {
    try { if (mo) mo.disconnect(); if (ro) ro.disconnect(); } catch (e) {}
    mo = ro = watching = null;
  }

  // NOT action tracking. These listeners record nothing about what was done — they set
  // one boolean, so the held frame can be frozen before the first edit lands. The old
  // click/focus/label capture (which mislabelled a "To review" click as an edit) stays
  // deleted.
  function watchTouch() {
    const touch = (e) => {
      if (mine(e.target) || !state.sawBoard) return;    // ignore pre-load clicks
      const root = document.querySelector(EDITOR_ROOT);
      if (root && !root.contains(e.target)) return;     // the browser chrome is not an edit
      state.userTouched = true;
    };
    addEventListener("pointerdown", touch, true);
    addEventListener("keydown", touch, true);
    state.untouch = () => {
      removeEventListener("pointerdown", touch, true);
      removeEventListener("keydown", touch, true);
    };
  }

  function setStatus(status, msg) {
    if (state.status === status && state.statusMsg === msg) return;
    state.status = status; state.statusMsg = msg;
    renderPill();
  }

  // A frame we could take, held in memory until it is the right one to commit.
  async function grabHeld() {
    const image = await grabFrame();
    if (!image) return null;
    return { image, geom: state.geom, how: state.frameHow };
  }

  async function captureNow() {
    if (state.held) return commit(state.held, "manual");
    // Nothing held: take whatever is on screen, and if the only obstacle is that the
    // ad is partly out of view, override that too. Being stuck with no way forward is
    // worse than a partial frame that is labelled as partial.
    let fresh = await grabHeld();
    if (!fresh) {
      state.bypassClip = true;
      try { fresh = await grabHeld(); } finally { state.bypassClip = false; }
    }
    if (fresh) return commit(fresh, "manual");
    setStatus("blocked", state.grabError || lastGrabError || "Could not capture the ad.");
    return false;
  }

  // The after-frame equivalent: force the comparison even from a partial view.
  async function finishNow() {
    state.bypassClip = true;
    try { await finish(); } finally { state.bypassClip = false; }
  }

  async function commit(held, trigger) {
    if (!held) return false;
    const ok = await postFrame("before", trigger, held.image, held.geom);
    if (!ok) { setStatus("blocked", state.postError || "Could not store the baseline."); return false; }
    state.baseHow = held.how; state.baseGeom = held.geom; state.held = null;
    state.okUntil = Date.now() + 9000;
    setStatus("ready", "");
    setTimeout(() => { if (state.status === "ready") renderNote(); }, 9200);
    return true;
  }

  // An exception anywhere in the arming loop used to kill it SILENTLY: no status was
  // ever set, so the pill sat on "Arming…" with an empty message forever. That is how a
  // deleted boardGeom() slipped through a whole eval run — 26 scenarios, every one
  // "never armed", and not one error surfaced anywhere. Never again: an internal fault
  // is now reported as an internal fault, so it can never be mistaken for a view
  // problem the user is supposed to fix.
  async function armBaseline() {
    try {
      await armLoop();
    } catch (e) {
      setStatus("blocked", "Capture hit an internal error: "
        + String((e && e.message) || e).slice(0, 140)
        + " — press Try again; if it repeats, this is a bug, not your view.");
    }
  }

  async function armLoop() {
    state.userTouched = false;
    let held = null, heldAt = 0, firstOk = 0;
    try { if (document.fonts && document.fonts.ready) {
      await Promise.race([document.fonts.ready, new Promise((r) => setTimeout(r, 1500))]);
    } } catch (e) {}

    for (let i = 0; i < 900 && state.ui && state.status !== "ready"; i++) {
      const tgt = stageTarget();
      const g = tgt ? boardGeom(tgt.el, tgt.rect, true) : null;
      if (tgt) { watchStage(tgt.el); state.sawBoard = true; }

      const blockedBecause = !tgt
        ? "Looking for the ad on the page\u2026"
        : g.clipped
        ? (g.coveredBy
            ? "A panel or dialog is covering the ad — close it and I’ll arm on my own."
            : `Only ${g.cov}% of the ad is on screen at ${Math.round(g.scale * 100)}% zoom. `
              + "Zoom out (or use Auto) until all of it is visible.")
        : !targetSettled(tgt)
        ? "Waiting for the ad\u2019s images to finish loading\u2026"
        : "";

      if (blockedBecause) {
        if (state.userTouched && held) { await commit(held, "frozen-on-first-edit"); return; }
        setStatus(state.userTouched && !held ? "blocked" : "arming",
          state.userTouched && !held
            ? blockedBecause + " You have already started editing, so I have no "
              + "\u201cbefore\u201d to compare against \u2014 fix the view and press "
              + "Capture now to start from the current state."
            : blockedBecause);
        await new Promise((r) => setTimeout(r, 400));
        continue;
      }

      // The touch check MUST come before the refresh. The refresh exists to keep the
      // held frame current while the page settles, but on a page that never settles it
      // fired on the same tick as the first interaction and overwrote the pre-edit
      // frame with a post-edit one — the baseline then contained the very edit it was
      // supposed to predate (measured: the "before" frame had the edit in it).
      if (state.userTouched && held) { await commit(held, "frozen-on-first-edit"); return; }

      // capturable. Take a frame if we have none, or if something changed since ours.
      if (!held || lastActivity > heldAt) {
        const fresh = await grabHeld();
        if (fresh) { held = state.held = fresh; heldAt = Date.now(); firstOk = firstOk || heldAt; }
      }
      if (!held) {                       // capturable but the screenshot itself failed
        setStatus("blocked", state.grabError || lastGrabError || "Could not capture the ad.");
        await new Promise((r) => setTimeout(r, 1200));
        continue;
      }

      const quiet = Date.now() - lastActivity;
      // Commit when: the user just touched the editor (freeze the pre-edit frame), the
      // page has gone quiet, or we have held a good frame long enough that waiting for
      // quiet is not worth it any more.
      if (state.userTouched) { await commit(held, "frozen-on-first-edit"); return; }
      if (quiet >= QUIET_MS || Date.now() - firstOk > FORCE_AFTER) {
        await commit(held, quiet >= QUIET_MS ? "init" : "init-forced");
        return;
      }
      setStatus("arming", "Reference frame captured \u2014 the page is still settling, "
        + "but you can start editing now: I freeze the reference the moment you do.");
      await new Promise((r) => setTimeout(r, 200));
    }
  }

  async function postFrame(kind, trigger, image, geom) {
    if (!/^es-[a-z0-9-]{4,40}$/.test(state.session || "")) state.session = newSession();
    // Two failure modes with two different fixes, so they get two different messages:
    // the screenshot could not be taken (a view problem the user can fix), or it was
    // taken and the helper would not store it (a service problem they cannot).
    state.postError = "";
    try {
      const r = await fetch(api() + "/frame", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session: state.session, kind, image,
                               trigger: trigger || "", board: geom || null }),
      });
      if (r.ok) { state.framesSent += 1; return true; }
      // The helper says WHICH check failed; throwing that away turned a one-word
      // diagnosis into a guessing game.
      let why = "";
      try { const j = await r.json(); why = j && j.error ? ` \u2014 ${j.error}` : ""; }
      catch (e) { /* not json */ }
      state.postError = `The capture helper answered ${r.status}${why}`;
      return false;
    } catch (e) {
      state.postError = "The capture helper is not answering on :8791 \u2014 start "
                      + "\u201cEdit Capture\u201d from the Control Room, then press Try again.";
      return false;
    }
  }

  async function sendFrame(kind, trigger) {
    state.grabError = ""; state.postError = "";
    const image = await grabFrame();
    if (!image) { state.grabError = lastGrabError || "Could not capture the ad."; return false; }
    return postFrame(kind, trigger, image, state.geom);
  }

  async function fetchSummary() {
    // Every branch sets an explicit state. Returning null on failure meant the panel
    // rendered no AI block at all, so a dead helper looked identical to a session
    // with nothing to say — you would fill the form never knowing the analyzer had
    // not run. Silence is not an acceptable answer from a capture tool.
    try {
      const r = await fetch(api() + "/session-summary", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session: state.session }),
      });
      if (!r.ok) { state.summary = { error: `helper returned ${r.status}` }; return; }
      const j = await r.json();
      if (j && j.note) state.summary = { note: j.note, backend: j.backend, edited: false };
      else if (j && j.why_empty) state.summary = { error: j.why_empty };
      else state.summary = { error: "the analyzer returned nothing" };
    } catch (e) {
      state.summary = { error: "capture helper is not running on 8791 \u2014 the before/after "
                             + "frames were not saved and nothing was compared" };
    }
  }

  const CSS = `
    :host{all:initial}
    *{box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
    .pill{position:fixed;right:18px;bottom:18px;z-index:2147483000;background:#12211c;color:#c9efe2;
      border:1px solid #2a4a3f;border-radius:100px;padding:8px 14px;font-size:12.5px;font-weight:600;
      cursor:pointer;box-shadow:0 4px 18px rgba(0,0,0,.35);display:flex;gap:8px;align-items:center}
    .pill .dot{width:8px;height:8px;border-radius:50%;background:#39d98a}
    .panel{position:fixed;right:18px;bottom:64px;z-index:2147483001;width:400px;max-height:74vh;overflow:auto;
      background:#0f1815;color:#dcefe7;border:1px solid #2a4a3f;border-radius:12px;padding:14px;
      box-shadow:0 12px 40px rgba(0,0,0,.5);font-size:13px}
    h3{margin:0 0 4px;font-size:14px} .sub{color:#7fa294;font-size:11.5px;margin-bottom:10px}
    .crumb{border:1px solid #223b32;border-radius:8px;padding:7px 9px;margin-bottom:6px;background:#121e19}
    .crumb b{font-size:12px} .crumb .diff{color:#9fc4b5;font-size:11.5px;word-break:break-word;margin-top:2px}
    .crumb .x{float:right;cursor:pointer;color:#68877b;border:0;background:none;font-size:13px}
    .chips{display:flex;flex-wrap:wrap;gap:5px;margin:8px 0}
    .chip{border:1px solid #2a4a3f;border-radius:100px;padding:3px 10px;font-size:11.5px;cursor:pointer;
      background:#121e19;color:#a9cabc}
    .chip.on{background:#1d5c46;color:#e8fff5;border-color:#2f8a68}
    textarea{width:100%;min-height:56px;background:#121e19;color:#dcefe7;border:1px solid #2a4a3f;
      border-radius:8px;padding:8px;font-size:12.5px;resize:vertical}
    .row{display:flex;gap:6px;margin-top:10px}
    .btn{flex:1;border:0;border-radius:8px;padding:9px 0;font-weight:600;font-size:12.5px;cursor:pointer}
    .save{background:#1d8a5f;color:#fff} .dismiss{background:#1a2a24;color:#8fb0a3}
    .verd{display:flex;gap:5px;margin:2px 0 0}
    .empty{color:#68877b;font-size:12px;padding:8px 0}
    .ok{color:#39d98a;font-size:12px;margin-top:8px}
    label.small{font-size:11px;color:#7fa294;text-transform:uppercase;letter-spacing:.8px;display:block;margin-top:10px}
  `;

  // added 2026-08-24 with the visible status card
  const CSS_EXTRA = `
    .pill .dot.wait{background:#e0a33c!important}
    .pill .dot.bad{background:#e8635a!important}
    .note{position:fixed;right:18px;bottom:60px;z-index:2147483000;width:290px;
      background:#12211c;color:#cfe9de;border:1px solid #2a4a3f;border-radius:10px;
      padding:10px 12px;font-size:12.5px;line-height:1.45;
      box-shadow:0 4px 18px rgba(0,0,0,.35)}
    .note.bad{border-color:#7d3a35;background:#20140f;color:#f3d3cf}
    .note.good{border-color:#2f8a68}
    .note b{color:#eafff6}
    .note .rb{margin-top:8px;background:#1d7a5c;color:#eafff6;border:0;border-radius:7px;
      padding:6px 11px;font-size:12px;font-weight:600;cursor:pointer}
  `;

  function mountUi() {
    const host = document.createElement("div");
    host.id = "uplane-edit-capture";
    const root = host.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = CSS + CSS_EXTRA;
    root.appendChild(style);
    const pill = document.createElement("div");
    pill.className = "pill";
    // The ONLY thing on screen while you edit. Clicking it is the "done" signal:
    // grab the after-frame, diff it against the before-frame, then open the panel
    // with the change already described.
    pill.onclick = () => (state.open ? closePanel() : finish());
    root.appendChild(pill);
    // Status has to be READABLE. It used to live in the pill's title attribute, so a
    // stuck "Arming…" told you nothing without hovering — and a failed POST left it
    // stuck there with no explanation at all.
    const note = document.createElement("div");
    note.className = "note";
    note.style.display = "none";
    root.appendChild(note);
    const panel = document.createElement("div");
    panel.className = "panel";
    panel.style.display = "none";
    root.appendChild(panel);
    // The editor binds Backspace/Delete to "delete selected elements" with a
    // document-level handler that preventDefault()s. Our fields live in a shadow root,
    // so event retargeting makes them look like an ordinary element to that handler and
    // it cancels the keystroke — you could type into the "why" box but not erase.
    //
    // Capture on WINDOW runs before any document-level capture listener, and
    // stopPropagation there keeps the app from ever seeing the key. The DEFAULT ACTION
    // still happens (only preventDefault cancels that), which is exactly what we want:
    // the browser edits the field, the editor never learns a key was pressed.
    const swallowKeys = (e) => {
      const path = e.composedPath ? e.composedPath() : [];
      if (path.indexOf(host) === -1) return;
      e.stopPropagation();
      // The editor also binds single-key TOOL SHORTCUTS (T = text, C = comment, …) on
      // a window-capture listener registered before this one, so its preventDefault()
      // has already landed by the time we run — stopPropagation can't undo that, and
      // the cancelled default action is the text insertion itself: typing "client
      // feedback" arrived as "lien feedbak". When the page has cancelled the default
      // on a key aimed at one of our fields, perform the edit ourselves.
      if (e.type !== "keydown" || !e.defaultPrevented) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const el = path[0];
      if (!el || !el.tagName || (el.tagName !== "TEXTAREA" && el.tagName !== "INPUT")) return;
      const v = el.value, s = el.selectionStart ?? v.length, t2 = el.selectionEnd ?? v.length;
      const put = (txt, caret) => {
        el.value = txt;
        el.selectionStart = el.selectionEnd = caret;
        el.dispatchEvent(new Event("input", { bubbles: true }));
      };
      if (e.key.length === 1) put(v.slice(0, s) + e.key + v.slice(t2), s + 1);
      else if (e.key === "Enter" && el.tagName === "TEXTAREA")
        put(v.slice(0, s) + "\n" + v.slice(t2), s + 1);
      else if (e.key === "Backspace") {
        const a = s === t2 ? Math.max(0, s - 1) : s; put(v.slice(0, a) + v.slice(t2), a);
      } else if (e.key === "Delete") {
        const b = s === t2 ? Math.min(v.length, t2 + 1) : t2; put(v.slice(0, s) + v.slice(b), s);
      } else if (e.key === "ArrowLeft") el.selectionStart = el.selectionEnd = Math.max(0, s - 1);
      else if (e.key === "ArrowRight") el.selectionStart = el.selectionEnd = Math.min(v.length, t2 + 1);
    };
    const KEY_EVENTS = ["keydown", "keyup", "keypress"];
    KEY_EVENTS.forEach((k) => addEventListener(k, swallowKeys, true));
    state.unswallow = () => KEY_EVENTS.forEach((k) => removeEventListener(k, swallowKeys, true));


    document.documentElement.appendChild(host);
    state.ui = { host, root, pill, note, panel };
    state.sel = { aspects: new Set(), verdict: "" };
    renderPill();
  }

  function renderPill() {
    if (!state.ui) return;
    const st = state.finishing ? "comparing" : state.status;
    const dot = st === "ready" ? "dot" : st === "blocked" ? "dot bad" : "dot wait";
    const label = st === "comparing" ? "Comparing\u2026"
                : st === "ready" ? "I\u2019m done"
                : st === "blocked" ? (state.framesSent > 0 ? "Fix view" : "Can\u2019t capture")
                : "Arming\u2026";
    state.ui.pill.innerHTML = `<span class="${dot}"></span> ${escapeHtml(label)}`;
    renderNote();
  }

  function renderNote() {
    const n = state.ui && state.ui.note;
    if (!n) return;
    const st = state.finishing ? "comparing" : state.status;
    const showOk = st === "ready" && Date.now() < state.okUntil;
    if (st === "comparing" || (st === "ready" && !showOk)) { n.style.display = "none"; return; }
    n.className = "note" + (st === "blocked" ? " bad" : showOk ? " good" : "");
    n.innerHTML = showOk
      ? `<b>Baseline captured.</b> Go ahead and edit \u2014 click <b>Done</b> when you are
         finished and I will compare it against this.`
      : escapeHtml(state.statusMsg || "Getting ready\u2026")
        + `<div>`
        + (st === "blocked" ? `<button class="rb again">Try again</button>` : "")
        + `<button class="rb force">Capture now</button></div>`;
    n.style.display = "block";
    const again = n.querySelector(".rb.again");
    if (again) again.onclick = () => (state.framesSent > 0 ? finish() : armBaseline());
    const force = n.querySelector(".rb.force");
    if (force) force.onclick = () => (state.framesSent > 0 ? finishNow() : captureNow());
  }

  // Which client is this ad for? Sun, 2026-08-23: "shouldn't u intake what client a
  // specific ad is for first? i don't think i need to type that in anyways." Right —
  // the board knows. The helper resolves it from the board UUID through the graph
  // (creative -> product -> client), falling back to the concept title. Only when
  // BOTH fail does the panel ask, and then as one click, never as typing.
  function titleHint() {
    const bits = [document.title];
    // the concept name as shown in the editor chrome, if there is one
    const h = document.querySelector('[class*="static-editor"] h1, [class*="static-editor"] h2');
    if (h) bits.push(h.textContent || "");
    return bits.join(" ").slice(0, 300);
  }

  async function fetchContext() {
    try {
      const u = `${api()}/context?board_url=${encodeURIComponent(location.href)}`
              + `&hint=${encodeURIComponent(titleHint())}`;
      const r = await fetch(u);
      if (!r.ok) return;
      state.ctx = await r.json();
    } catch (e) { state.ctx = null; }
    if (state.ui && state.open) renderPanel();
  }

  // Everything the capture knows comes from two screenshots, so pressing Done is
  // the whole interaction: after-frame -> phash/vision diff -> prefilled panel.
  async function finish() {
    if (state.finishing) return;
    if (state.framesSent === 0) {           // nothing to compare against yet
      setStatus(state.status === "ready" ? "arming" : state.status,
                state.statusMsg || "No baseline yet \u2014 waiting for a full view of the ad.");
      if (state.status !== "blocked") armBaseline();
      return;
    }
    state.finishing = true; renderPill();
    try {
      const ok = await sendFrame("after", "done");
      if (!ok) {
        // Keep everything. Zoom, then Try again — the baseline is untouched.
        setStatus("blocked", state.grabError
          ? state.grabError + " Your edit is safe \u2014 fix the view, then press Try again."
          : (state.postError || "Could not store the after-frame.")
            + " Your edit is safe \u2014 press Try again once it is up.");
        state.finishing = false; renderPill();
        return;
      }
      if (!ok && state.grabError) {
        state.summary = { error: `could not screenshot the creative — ${state.grabError}` };
        openPanel();
        state.finishing = false; renderPill(); renderPanel();
        return;
      }
      state.summary = { pending: true };
      openPanel();
      await fetchSummary();
    } catch (e) {
      state.summary = null;
    }
    state.finishing = false; renderPill();
    if (state.open) renderPanel();
  }

  function openPanel() {
    state.open = true;
    state.ui.panel.style.display = "block";
    renderPanel();
    // Snapshot the current creative and draft the visual note while the user
    // reviews the breadcrumbs — the panel re-renders when the draft arrives.
    if (!state.summary) {
      sendFrame("after", "panel-open").then((ok) => {
        if (!ok && state.framesSent === 0) return; // nothing to interpret
        state.summary = { pending: true };
        if (state.open) renderPanel();
        fetchSummary().then(() => { if (state.open) renderPanel(); });
      });
    }
  }
  function closePanel() { state.open = false; state.ui.panel.style.display = "none"; }

  // Read-only when known; a row of one-click chips only when it is not.
  function clientLine() {
    const c = state.ctx;
    const chosen = state.clientPick
      || (c && c.client_id ? c.client_id : "");
    if (chosen) {
      const name = state.clientPick
        ? ((c && (c.known || []).find((k) => k.id === state.clientPick) || {}).name || state.clientPick)
        : c.client_name;
      const how = state.clientPick ? "you picked"
        : c.source === "board" ? "from the board"
        : "from the concept title";
      return `<div class="sub" style="margin-bottom:6px">Client: <b>${escapeHtml(name)}</b>`
           + ` <span style="opacity:.6">(${how})</span></div>`;
    }
    if (!c) return "";
    return `<label class="small">Client — could not tell from the board, pick one</label>
      <div class="chips cpick">${(c.known || []).map((k) =>
        `<span class="chip" data-c="${escapeHtml(k.id)}">${escapeHtml(k.name)}</span>`).join("")}</div>`;
  }

  function renderPanel(msg) {
    const p = state.ui.panel;
    const s = state.summary;
    const aiBlock = !s
      ? ""
      : s.pending
      ? '<label class="small">AI draft — what changed visually</label><div class="empty">analyzing before/after frames…</div>'
      : s.error
      ? `<label class="small">AI draft — unavailable</label><div class="empty">${escapeHtml(s.error)}</div>`
      : `<label class="small">AI draft — what changed visually <span style="text-transform:none;letter-spacing:0">(${escapeHtml(s.backend || "")} — correct it if wrong)</span></label>
         <textarea class="ai" style="min-height:44px">${escapeHtml(s.note || "")}</textarea>`;
    p.innerHTML = `
      <h3>What did you fix?</h3>
      ${clientLine()}
      <div class="sub">Read from the before/after screenshots. Correct the draft, then say why.${
        state.frameHow ? ` Framed: ${escapeHtml(state.frameHow)}.` : ""
      }${
        state.baseGeom && state.geom
          && (state.baseGeom.designW !== state.geom.designW
              || state.baseGeom.designH !== state.geom.designH)
          ? ` <b>The format changed between frames (${state.baseGeom.designW}x${state.baseGeom.designH}`
            + ` \u2192 ${state.geom.designW}x${state.geom.designH}) — the diff will describe that, not your edit.</b>`
          : ""
      }</div>
      ${aiBlock}
      <label class="small">Aspect</label>
      <div class="chips">${ASPECTS.map((a) => `<span class="chip ${state.sel.aspects.has(a) ? "on" : ""}" data-a="${a}">${a}</span>`).join("")}</div>
      <label class="small">Verdict on this creative (optional)</label>
      <div class="verd chips">${["approve", "revise", "reject"].map((v) => `<span class="chip ${state.sel.verdict === v ? "on" : ""}" data-v="${v}">${v}</span>`).join("")}</div>
      <label class="small">Why — what was wrong, in your words</label>
      <textarea placeholder="e.g. skin looked airbrushed; softened highlights and added grain">${escapeHtml(state.sel.why || "")}</textarea>
      <div class="row">
        <button class="btn dismiss">Discard</button>
        <button class="btn save">Log to graph</button>
      </div>
      ${msg ? `<div class="ok">${escapeHtml(msg)}</div>` : ""}
    `;
    p.querySelectorAll("[data-a]").forEach((c) => (c.onclick = () => {
      const a = c.dataset.a;
      state.sel.aspects.has(a) ? state.sel.aspects.delete(a) : state.sel.aspects.add(a);
      renderPanel();
    }));
    p.querySelectorAll("[data-c]").forEach((c) => (c.onclick = () => {
      state.clientPick = c.dataset.c; renderPanel();
    }));
    p.querySelectorAll("[data-v]").forEach((c) => (c.onclick = () => {
      state.sel.verdict = state.sel.verdict === c.dataset.v ? "" : c.dataset.v; renderPanel();
    }));
    p.querySelector("textarea:not(.ai)").oninput = (e) => (state.sel.why = e.target.value);
    const ai = p.querySelector("textarea.ai");
    if (ai) ai.oninput = (e) => { state.summary.note = e.target.value; state.summary.edited = true; };
    p.querySelector(".dismiss").onclick = () => {
      // Discard the note, not the session: re-baseline from the creative as it is NOW
      // so the next edit in this sitting is captured too.
      closePanel();
      restartSession();
    };
    p.querySelector(".save").onclick = save;
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  }

  async function save() {
    const s = state.summary;
    const payload = {
      board_url: location.href,
      by: state.by,
      surface: "uplane-editor",
      verdict: state.sel.verdict || "",
      aspects: [...state.sel.aspects],
      why: (state.sel.why || "").trim(),
      edits: [],   // frames-only capture: no action trail is collected any more
      session_id: state.session,
      client_id: state.clientPick || (state.ctx && state.ctx.client_id) || "",
      ai_note: s && !s.pending && !s.error ? (s.note || "").trim() : "",
      ai_backend: s && !s.pending && !s.error ? s.backend || "" : "",
      ai_note_edited: !!(s && s.edited),
    };
    try {
      const r = await fetch(state.endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const j = await r.json();
      state.sel = { aspects: new Set(), verdict: "", why: "" };
      // A logged session is closed: rotate to a fresh one and re-baseline so
      // the next round of edits diffs against the creative as it is NOW.
      restartSession();
      renderPanel(`Logged ${j.edit_events || 0} edits${j.feedback ? " + verdict" : ""} to the graph ✓`);
      setTimeout(closePanel, 1600);
    } catch (e) {
      renderPanel("Helper not reachable on :8791 — start “Edit Capture” from the Control Room.");
    }
  }

  // Once armed, the view can still go bad — the editor zooms, the panel opens, the
  // window changes. Re-check quietly so the pill never claims to be ready when a click
  // would fail. Only while idle and visible, so it costs nothing during editing.
  // While a baseline is safely stored, the view is the user's business — browsing
  // folders, opening drawers, zooming around must NOT flip the pill to "Fix view"
  // (Sun, 2026-08-25: "the refusal shouldn't happen once a capture has been captured
  // already"). The view only matters again at the moment Done is clicked, and
  // finish() checks it there. This watcher keeps ONE job: after a refused Done left
  // the state blocked, notice the view becoming good again and clear it on its own.
  function startWatch() {
    stopWatch();
    state.watch = setInterval(() => {
      if (!state.ui || state.finishing || state.open) return;
      if (document.visibilityState !== "visible") return;
      if (state.framesSent === 0) return;              // arming loop owns that case
      if (state.status !== "blocked") return;          // never CREATE blocked here
      const tgt = stageTarget();
      const gg = tgt ? boardGeom(tgt.el, tgt.rect) : null;
      if (tgt && !gg.clipped) setStatus("ready", "");
    }, 2000);
  }

  function stopWatch() {
    if (state.watch) { clearInterval(state.watch); state.watch = null; }
  }

  function init(opts) {
    Object.assign(state, opts || {});
    if (!state.session) state.session = newSession();   // see newSession above
    mountUi();
    // NO click/focus listeners. Reading your actions is what made a click on the
    // editor's own "To review" button register as an edit: labelFor() prefers a
    // hidden aria-label over the visible text, and focusout treats any field whose
    // value differs as something you typed. Both are gone. What changed is derived
    // from the before/after frames instead, which cannot mistake navigation for work.
    watchTouch();
    fetchContext();
    armBaseline();
    startWatch();
  }

  // A session covers ONE edit -> one graph row. After logging or discarding, rotate to
  // a fresh session and re-baseline against the current state, so a second, third and
  // fourth edit in the same sitting are each captured on their own.
  function restartSession() {
    state.session = newSession();
    state.framesSent = 0;
    state.summary = null;
    state.sel = { aspects: new Set(), verdict: "", why: "" };
    state.baseHow = ""; state.baseGeom = null; state.geom = null;
    state.held = null; state.userTouched = false;
    setStatus("arming", "Re-arming for your next edit\u2026");
    armBaseline();
  }

  function teardown() {
    try {
      if (state.ui && state.ui.host && state.ui.host.parentNode) {
        state.ui.host.parentNode.removeChild(state.ui.host);
      }
    } catch (e) {}
    stopWatching();
    try { if (state.untouch) state.untouch(); } catch (e) {}
    try { if (state.unswallow) state.unswallow(); } catch (e) {}
    state.unswallow = null;
    state.untouch = null; state.held = null;
    state.userTouched = false; state.sawBoard = false;
    stopWatch();
    state.ui = null; state.open = false; state.finishing = false;
    state.summary = null;
    // Reset the STATUS too, not just the counters. Leaving it on "ready" made the pill
    // claim "I'm done" on re-entry with no baseline behind it, and armBaseline()'s loop
    // (which runs only while status !== "ready") exited immediately — so the session
    // could never arm and Done silently did nothing.
    state.status = "arming"; state.statusMsg = ""; state.okUntil = 0;
    state.baseHow = ""; state.baseGeom = null; state.geom = null;
    state.grabError = ""; state.postError = ""; state.clientPick = "";
    state.session = null; state.framesSent = 0;
  }

  window.__uplaneEditCapture = { init, teardown, finish, restartSession, captureNow,
                                 _state: state };
  if (/[?&]autoinit=1/.test(location.search)) init();
})();
