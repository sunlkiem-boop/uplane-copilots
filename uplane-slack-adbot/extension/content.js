/* Uplane Slack Ad-Bot Driver — content script (app.uplane.com/concepts/*).
 *
 * Long-polls the local helper for a job, then drives the board with a
 * VERIFY-BEFORE-GENERATE gate (the board URL is unreliable — the same folder
 * URL renders a different client depending on the active Uplane login, so we
 * target everything by on-screen NAME and refuse to generate on any mismatch):
 *
 *   1. open the target folder  (an <a> whose text === job.folder)
 *   2. select client -> product in the .bg-popover dropdown
 *   3. VERIFY on-screen folder title + product chip match the job
 *        └─ any mismatch  ->  ABORT, report to Slack, DO NOT generate
 *   4. upload creative -> set prompt -> set count
 *   5. Generate -> watch for new concept cards -> report previews back
 *
 * ── SELECTOR CALIBRATION ──────────────────────────────────────────────────
 * React app with generated class names; we target by stable signals collected
 * in SEL below. Run `__adbotCalibrate()` in the page console to check them.
 */
(() => {
  if (window.__adbotDriverLoaded) return;
  window.__adbotDriverLoaded = true;

  const POLL_MS = 2500;
  const GEN_TIMEOUT_MS = 5 * 60 * 1000;
  // A stable per-tab id lets the helper LEASE the board to THIS tab and refuse a
  // second tab (two tabs firing on one board eat each other's attached refs). This
  // is the multi-tab guard; the tab still drives ONE job at a time (see pollOnce).
  const DRIVER_ID = "d" + Math.random().toString(36).slice(2, 10);
  const NAV_TIMEOUT_MS = 15 * 1000;
  // Switching CLIENT re-renders the toolbar and refetches, so the chip can lag
  // well past a second. Polled, not slept — this returns as soon as it lands.
  const PRODUCT_SWITCH_MS = 12 * 1000;
  const CONFIRM_TIMEOUT_MS = 10 * 60 * 1000;   // how long to wait on the Slack gate
  // The compose bar renders a BEAT AFTER a folder route settles. Snapshotting it
  // once and giving up is what caused "couldn't find the product selector" — so
  // every critical control is now awaited, not read once.
  const COMPOSE_READY_MS = 20 * 1000;
  const CONTROL_WAIT_MS = 8 * 1000;

  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // Poll pred() until it returns something truthy, or give up. Returns the value
  // (so callers get the element) or null. Never throws — a predicate that blows
  // up (DOM mid-teardown) just counts as "not ready yet".
  async function waitFor(pred, timeoutMs, intervalMs = 200) {
    const t0 = Date.now();
    for (;;) {
      let v = null;
      try { v = pred(); } catch (e) { v = null; }
      if (v) return v;
      if (Date.now() - t0 >= timeoutMs) return null;
      await sleep(intervalMs);
    }
  }

  // ---- SELECTORS (calibrate here) ---------------------------------------- #
  const SEL = {
    promptBox: () =>
      document.querySelector('textarea[placeholder*="Describe your idea"]') ||
      document.querySelector('[placeholder*="Describe your idea"]'),
    fileInput: () =>
      document.querySelector('input[type="file"][accept*="image"]') ||
      document.querySelector('input[type="file"]'),
    // Exact "Generate" first; then a decorated variant ("Generate ✨") — but NEVER
    // the in-progress "Generating…" label (btnByText's substring fallback would
    // match it, and clicking it does nothing / cancels).
    generateBtn: () => {
      const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
      const exact = btns.find((b) => norm(b.textContent).toLowerCase() === "generate");
      if (exact) return exact;
      return btns.find((b) => {
        const t = norm(b.textContent).toLowerCase();
        return t.startsWith("generate") && !t.startsWith("generating");
      }) || null;
    },
    countReadout: () => findCountReadout(),
    // the generate-bar product chip = the button immediately before "Brainstorming"
    productChip: () => {
      const brain = btnByText(["Brainstorming"]);
      if (!brain) return null;
      const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
      const i = btns.indexOf(brain);
      return i > 0 ? btns[i - 1] : null;
    },
    popover: () =>
      document.querySelector('[class*="bg-popover"]') ||
      document.querySelector('[role="menu"], [role="listbox"]'),
    folderAnchor: (name) =>
      Array.from(document.querySelectorAll('a[href*="/concepts/"]'))
        .find((a) => norm(a.textContent) === name),
    conceptImages: () =>
      Array.from(document.querySelectorAll("img")).filter(isConceptImage),
  };

  // ---- generic helpers --------------------------------------------------- #
  function btnByText(texts) {
    const wanted = texts.map((t) => t.toLowerCase());
    const els = Array.from(document.querySelectorAll('button, [role="button"]'));
    return (
      els.find((b) => wanted.some((w) => norm(b.textContent).toLowerCase() === w)) ||
      els.find((b) => wanted.some((w) => norm(b.textContent).toLowerCase().includes(w)))
    );
  }

  function isConceptImage(img) {
    const r = img.getBoundingClientRect();
    if (r.width < 120 || r.height < 120) return false;
    const src = img.currentSrc || img.src || "";
    return /^https?:/.test(src) && !/avatar|logo|icon/i.test(src);
  }

  function findCountReadout() {
    const leaves = Array.from(document.querySelectorAll("span, div, p")).filter(
      (n) => /^\d{1,2}$/.test(norm(n.textContent)) && n.children.length === 0
    );
    for (const node of leaves) {
      const near = stepperFor(node);
      if (near.minus && near.plus) return { node, ...near };
    }
    return null;
  }
  function stepperFor(node) {
    const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
    let minus = null, plus = null;
    for (const b of btns) {
      const pos = node.compareDocumentPosition(b);
      if (pos & Node.DOCUMENT_POSITION_PRECEDING) minus = b;
      else if (pos & Node.DOCUMENT_POSITION_FOLLOWING && !plus) plus = b;
    }
    if (!minus || !plus) return {};
    const nr = node.getBoundingClientRect();
    const mr = minus.getBoundingClientRect();
    const pr = plus.getBoundingClientRect();
    const sameRow = Math.abs(mr.top - nr.top) < 24 && Math.abs(pr.top - nr.top) < 24;
    const flanking = mr.right <= nr.left + 4 && pr.left >= nr.right - 4;
    const close = nr.left - mr.right < 80 && pr.left - nr.right < 80;
    return sameRow && flanking && close ? { minus, plus } : {};
  }

  // Set a form field's value in a way the board's React ACTUALLY registers.
  //
  // VERIFIED LIVE 2026-07-24: the old prototype-setter + synthetic input event set
  // the textarea's DOM value (it showed on screen) but React's internal state never
  // updated — so the Generate button read an "empty" prompt and every click silently
  // did nothing. THAT was the "it won't generate" bug, from the very start.
  //
  // execCommand("insertText") inserts as if typed, firing the beforeinput/input
  // events React's value-tracker honours — a plain synthetic click then generates.
  // Falls back to the old setter if execCommand is unavailable.
  function setNativeValue(el, value) {
    if (el.isContentEditable) {
      el.focus();
      try {
        const range = document.createRange(); range.selectNodeContents(el);
        const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
        if (value) document.execCommand("insertText", false, value);
        else document.execCommand("delete", false);
      } catch (e) {
        el.textContent = value;
        el.dispatchEvent(new InputEvent("input", { bubbles: true }));
      }
      return;
    }
    el.focus();
    try {
      el.setSelectionRange(0, (el.value || "").length);   // select existing, then replace
      const ok = value ? document.execCommand("insertText", false, value)
                       : document.execCommand("delete", false);
      if (ok && (el.value || "") === (value || "")) return;
    } catch (e) { /* fall through to the setter */ }
    // fallback: prototype setter + synthetic events (works on some inputs)
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // ---- board state readers ---------------------------------------------- #
  // On-screen board (folder) title — the small heading at the top-left.
  function currentFolderTitle() {
    const els = Array.from(document.querySelectorAll("h1, h2, h3, span, div, p"));
    for (const e of els) {
      if (e.children.length) continue;
      const r = e.getBoundingClientRect();
      const t = norm(e.textContent);
      if (r.top >= 6 && r.top < 60 && r.left >= 4 && r.left < 420 && r.width > 0 && t && t.length < 48) {
        return t;
      }
    }
    return null;
  }
  const folderMatches = (name) => currentFolderTitle() === name;

  // Extract the folder id from a /concepts/<uuid> URL.
  function folderIdFromUrl(url) {
    const m = String(url || "").match(/concepts\/([0-9a-fA-F-]{16,})/);
    return m ? m[1] : null;
  }
  const onFolder = (id) => !!id && location.pathname.indexOf(id) !== -1;

  // Currently-selected product = the chip text contains the product label.
  function productMatches(product) {
    const chip = SEL.productChip();
    return !!chip && norm(chip.textContent).includes(product);
  }

  // The bottom compose bar the driver actually drives. ALL THREE controls must
  // be present for the board to count as "ready" — they render a beat after a
  // folder route settles, and acting before then is what produced the spurious
  // "couldn't find the product selector" abort. runJob waits on this.
  function composeReady() {
    const chip = SEL.productChip();
    const gen = SEL.generateBtn();
    const box = SEL.promptBox();
    return chip && gen && box ? { chip, gen, box } : null;
  }

  // ---- board actions ----------------------------------------------------- #
  // Navigate to the EXACT folder the user pasted (by its id), via the folder
  // list anchor (SPA route, no full reload). If the folder isn't in this
  // session's list, it belongs to another login → caller aborts.
  async function openFolderUrl(url) {
    const id = folderIdFromUrl(url);
    if (!id) return false;
    if (onFolder(id)) return true;                          // already there
    const a = Array.from(document.querySelectorAll('a[href*="/concepts/"]'))
      .find((x) => (x.getAttribute("href") || "").indexOf(id) !== -1);
    if (!a) return false;                                   // not in this session
    a.click();                                              // SPA route
    const t0 = Date.now();
    while (Date.now() - t0 < NAV_TIMEOUT_MS) {
      if (onFolder(id)) { await sleep(600); return true; }  // settle after route
      await sleep(400);
    }
    return onFolder(id);
  }

  const BTN_SEL = 'button, [role="menuitem"], [role="option"]';
  const isBtnEl = (n) =>
    n.tagName === "BUTTON" || n.getAttribute("role") === "menuitem" || n.getAttribute("role") === "option";
  // An element's OWN text — direct text-node children only, ignoring descendants.
  const ownText = (n) =>
    norm(Array.from(n.childNodes).filter((c) => c.nodeType === 3).map((c) => c.nodeValue).join(" "));

  // Returns "ok" or a SPECIFIC reason — the caller turns it into the Slack
  // message, so "not in this login" only ever means exactly that.
  async function selectProduct(clientLabel, productLabel) {
    if (productMatches(productLabel)) return "ok";
    // WAIT for the chip — don't snapshot once. The toolbar can still be rendering.
    const chip = await waitFor(() => SEL.productChip(), CONTROL_WAIT_MS);
    if (!chip) return "no-chip";
    chip.click();                               // open dropdown
    await sleep(500);
    const pop = SEL.popover();
    if (!pop) return "no-popover";

    // Scoped pick: the product button under the matching client header.
    // The header is NOT a childless leaf — it's a row carrying the client name
    // as its own text plus a logo <img> child, e.g.
    //   <div own="Product Alliance"><img></div> … <button><span>PM jobs</span>
    // so match on OWN text and ignore anything inside a button (a button's inner
    // <span> repeats the product name and would masquerade as a header).
    let lastHeader = null, target = null;
    const walker = document.createTreeWalker(pop, NodeFilter.SHOW_ELEMENT);
    let node = walker.currentNode;
    while (node) {
      const inBtn = node.closest(BTN_SEL);
      const own = ownText(node);
      if (!inBtn && own) lastHeader = own;
      if (isBtnEl(node) && norm(node.textContent) === productLabel &&
          (!clientLabel || lastHeader === clientLabel)) {
        target = node; break;
      }
      node = walker.nextNode();
    }
    if (!target) {
      // Unscoped fallback, but ONLY when the label is unambiguous in the popover
      // — otherwise we could silently pick another client's identically-named
      // product, which is the exact wrong-brand bug the verify-gate exists for.
      const hits = Array.from(pop.querySelectorAll(BTN_SEL))
        .filter((b) => norm(b.textContent) === productLabel);
      if (hits.length === 1) target = hits[0];
      else if (hits.length > 1) { document.body.click(); return "ambiguous"; }
    }
    if (!target) { document.body.click(); return "absent"; }

    target.click();
    // Switching client re-renders the whole toolbar and refetches — 600ms was
    // far too impatient and reported a healthy switch as "not in this login".
    const t0 = Date.now();
    while (Date.now() - t0 < PRODUCT_SWITCH_MS) {
      if (productMatches(productLabel)) return "ok";
      await sleep(300);
    }
    return "no-switch";
  }

  async function setCount(target) {
    const rd = SEL.countReadout();
    if (!rd) return false;
    let guard = 0;
    while (guard++ < 20) {
      const cur = parseInt(norm(rd.node.textContent), 10);
      if (isNaN(cur) || cur === target) break;
      (cur < target ? rd.plus : rd.minus).click();
      await sleep(250);
    }
    return true;
  }

  async function injectFile(input, blob, name, type) {
    const dt = new DataTransfer();
    dt.items.add(new File([blob], name, { type }));
    input.files = dt.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    await sleep(1200);
  }

  // An access-denied / not-permitted board. We require BOTH a denial phrase and
  // the absence of the compose bar, so normal copy containing "not found" can't
  // trigger a false positive.
  function accessDenied() {
    if (SEL.promptBox()) return false;                  // board loaded fine
    const t = norm(document.body ? document.body.innerText : "").toLowerCase();
    if (!t) return false;
    return /access denied|don'?t have access|do not have access|not authorized|unauthorized|no permission|forbidden|you don'?t have permission|sign in to continue|log in to continue/.test(t);
  }

  // Ask Slack to approve this folder+brand, then wait for the answer. Returns
  // "confirmed" | "cancelled" | "timeout". The helper auto-confirms if this
  // folder+product was already approved (so a bulk drop only asks once).
  async function confirmGate(job) {
    const title = currentFolderTitle() || "";
    const req = await server("POST", "/job-confirm-request", { job_id: job.id, folder_title: title });
    let state = (req && req.json && req.json.confirm) || "awaiting";
    if (state !== "awaiting") return state;
    const t0 = Date.now();
    while (Date.now() - t0 < CONFIRM_TIMEOUT_MS) {
      await sleep(3000);
      const r = await server("GET", "/job-state/" + job.id);
      const c = r && r.json && r.json.confirm;
      if (c && c !== "awaiting") return c;
    }
    return "timeout";
  }

  // Report the current step so the helper can narrate it live in Slack — and so
  // the job's lease stays fresh (proving this driver is still alive).
  const progress = (job, phase) => server("POST", "/job-progress", { job_id: job.id, phase });

  // ---- job execution (the gate) ----------------------------------------- #
  async function runJob(job) {
    // RE-ATTACH: this job already clicked Generate but its driver died (tab/
    // extension reload). The concepts kept generating on the board — just open
    // the folder and COLLECT them. Never re-select/re-generate (no double-bill).
    if (job.collect_only) {
      await progress(job, "collect");
      await openFolderUrl(job.folder_url);          // best-effort; may already be here
      const prior = new Set(job.prior_ids || []);
      const urls = await collectNew(job.count, prior, job.id);
      if (!urls.length) {
        return finish(job.id, "error", [], "🛑 Re-attached after a reload but couldn't find the " +
          "generated concepts on the board. They may still be rendering — open the board to check. " +
          "(Not re-generated, so you weren't billed twice.)");
      }
      const imgs = [];
      for (const u of urls) { const d = await urlToDataUrl(u); if (d) imgs.push({ data: d, media_type: "image/png" }); }
      return finish(job.id, "ok", urls, "", imgs);
    }
    // 0. access check: if the board says denied, reload ONCE and retry. The
    // reload destroys this content script, so re-queue the job first so the
    // fresh driver can claim it again.
    if (accessDenied()) {
      if ((job.attempts || 0) < 1) {
        await server("POST", "/job-requeue", { job_id: job.id });
        location.reload();
        return;                                          // fresh driver retries
      }
      return abort(job, "Access denied on the board even after a reload. You're " +
        "likely on the wrong Chrome profile or Uplane login for this brand/folder — " +
        "open the folder URL manually to check, then retry.");
    }
    // 1. navigate to the exact folder URL the user pasted
    await progress(job, "folder");
    if (!(await openFolderUrl(job.folder_url))) {
      return abort(job, `Couldn't open the folder URL in this Uplane session — the ` +
        `folder isn't in this login's list. Check the URL / that you're on the right account.`);
    }
    // 1b. the folder itself may be restricted — same reload-once-then-report
    if (accessDenied()) {
      if ((job.attempts || 0) < 1) {
        await server("POST", "/job-requeue", { job_id: job.id });
        location.reload();
        return;
      }
      return abort(job, `Access denied on that folder even after a reload — this ` +
        `Uplane login probably can't see ${job.client_label}'s folder. Check the Chrome profile / account.`);
    }
    // 1c. HUMAN GATE: Uplane doesn't link folders to brands, so ask in Slack
    // whether this folder really is the right one for this brand before spending
    // credits. Confirmed once per folder+product, so a bulk drop asks once.
    await progress(job, "confirm");
    const gate = await confirmGate(job);
    if (gate === "cancelled") {
      return finish(job.id, "error", [], "🚫 Cancelled — you declined the folder/brand confirmation.");
    }
    if (gate !== "confirmed") {
      return finish(job.id, "error", [], "🚫 Timed out waiting for the Slack confirmation — nothing generated.");
    }

    // 2. target client -> product
    await progress(job, "product");
    // READINESS GATE: the compose bar renders a beat after the folder route
    // settles. Wait for prompt box + product chip + Generate to all exist before
    // touching them — this is the fix for the spurious "couldn't find the product
    // selector" abort that fired when we acted on a half-rendered toolbar.
    if (!(await waitFor(composeReady, COMPOSE_READY_MS))) {
      return abort(job, `The board's compose bar didn't finish loading within ` +
        `${Math.round(COMPOSE_READY_MS / 1000)}s (missing prompt box, product selector, or ` +
        `Generate button). The board may be slow to load, logged out, or Uplane changed its layout.`);
    }
    const picked = await selectProduct(job.client_label, job.product_label);
    if (picked !== "ok") {
      const why = {
        "no-chip": `couldn't find the product selector on the board — the page may still be loading or Uplane changed its layout.`,
        "no-popover": `the product dropdown didn't open when clicked.`,
        "absent": `it isn't in this Uplane login's dropdown — nothing generated. Switch accounts and retry.`,
        "ambiguous": `several clients list a product called "${job.product_label}" and none sit under "${job.client_label}" — refusing to guess.`,
        "no-switch": `the board didn't switch to it within ${Math.round(PRODUCT_SWITCH_MS / 1000)}s (clicked, but the chip never updated).`,
      }[picked] || `selection failed (${picked}).`;
      return abort(job, `Product "${job.product_label}" (client "${job.client_label}") — ${why}`);
    }
    // 3. VERIFY both before spending credits
    if (!onFolder(folderIdFromUrl(job.folder_url))) {
      return abort(job, `Folder verification failed — not on the target folder (${job.folder_url}).`);
    }
    if (!productMatches(job.product_label)) {
      return abort(job, `Product verification failed (expected "${job.product_label}", chip shows "${norm((SEL.productChip()||{}).textContent||'')}").`);
    }

    // 3b. auto-attach the product's reference images (bundle shot, packs, logo)
    // BEFORE the prompt — verified live that references and the prompt coexist.
    if (job.reference_b64 && job.reference_b64.length) {
      await progress(job, "references");
      if (!(await attachReferences(job))) {
        return abort(job, `Couldn't attach the ${job.reference_b64.length} product reference image(s) ` +
          `— the board didn't show all the thumbnails. Nothing generated.`);
      }
    }

    // 4. set prompt + count.
    // We deliberately DO NOT upload the creative to the board: the dropped
    // creative is already fully encoded into job.prompt by the vision analysis,
    // and uploading pops a "Copy Concept / Use Image" chooser that re-renders the
    // compose area and WIPES the prompt box — firing Generate with nothing.
    const box = await waitFor(() => SEL.promptBox(), CONTROL_WAIT_MS);
    if (!box) return abort(job, "Prompt box not found on the board (compose bar didn't render).");
    setNativeValue(box, job.prompt);
    await sleep(500);
    if (!norm(box.value)) { setNativeValue(box, job.prompt); await sleep(600); }  // one retry
    if (!norm(box.value)) return abort(job, "Prompt didn't stick in the box.");
    await setCount(job.count);

    // 5. generate — snapshot existing ad-ids first so we can identify THIS job's
    // new concepts by identity (not position).
    const priorIds = adIdSet();
    const gen = await waitFor(() => SEL.generateBtn(), CONTROL_WAIT_MS);
    if (!gen) return abort(job, "Generate button not found on the board (compose bar didn't render).");
    // A bare gen.click() was silently doing NOTHING: the board's button often
    // ignores a synthetic click, and if React hasn't registered the prompt the
    // button is disabled. So: make sure it's enabled, fire a REAL pointer sequence,
    // and VERIFY generation actually started (new crafting card or the button
    // flips to "Generating…"); retry a few times before giving up.
    if (!(await fireGenerate(gen, box, job.prompt, priorIds))) {
      return abort(job, "Clicked Generate but the board never started generating " +
        "(button stayed idle/disabled after 3 tries — the prompt may not have registered).");
    }
    // Persist "credits spent + baseline snapshot" BEFORE we wait — if this driver
    // dies now, a fresh one re-attaches and collects using prior_ids (no re-run).
    await server("POST", "/job-generated", { job_id: job.id, prior_ids: Array.from(priorIds) });
    await progress(job, "generate");

    // FAST PATH — batch jobs (no Slack thread): the generation is already SUBMITTED
    // and rendering on the board, which IS the deliverable. Don't burn ~5 minutes
    // waiting to scrape the images back to a thread that doesn't exist. Give the
    // board a beat to save this prompt into "Recent prompts" (so the next job can
    // re-clone its packs), then finish and move on. ~5x faster across a batch.
    if (job.skip_collect) {
      await sleep(4500);
      return finish(job.id, "ok", [], "");
    }

    // 6. collect THIS job's batch: the `want` NEW ad-ids, once crafted + finished.
    // (Blocking, one job at a time — the proven-stable model. Fire-ahead pipelining
    // was reverted after a live canary showed it double-fired under multi-tab +
    // tab-reload churn; the board-lease driver id below is what safely keeps a
    // second tab off this board.)
    lastBatchStats = { collected: 0, inferred: 0, stillCrafting: 0, failed: 0 };
    const urls = await waitForBatch(job.count, priorIds, job.id);
    const batchStats = lastBatchStats;
    await progress(job, "collect");
    if (!urls.length) return finish(job.id, "error", [], noImagesReason(job, priorIds));
    const images = [];
    for (const u of urls) {
      const dataUrl = await urlToDataUrl(u);
      if (dataUrl) images.push({ data: dataUrl, media_type: "image/png" });
    }
    return finish(job.id, "ok", urls, "", images, batchStats);
  }

  function abort(job, reason) {
    return finish(job.id, "error", [], "🚫 Not generated — " + reason);
  }

  // Fire a REAL click on an element the way a user would: React commonly ignores
  // element.click() but responds to a pointer/mouse event sequence.
  function hardClick(el) {
    try { el.scrollIntoView({ block: "center" }); } catch (e) {}
    const r = el.getBoundingClientRect();
    const o = { bubbles: true, cancelable: true, view: window,
                clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 };
    for (const type of ["pointerover", "pointerenter", "pointerdown", "mousedown",
                        "pointerup", "mouseup", "click"]) {
      try {
        const E = type.startsWith("pointer") ? PointerEvent : MouseEvent;
        el.dispatchEvent(new E(type, o));
      } catch (e) { try { el.dispatchEvent(new MouseEvent(type.replace("pointer", "mouse"), o)); } catch (_) {} }
    }
  }

  // Click Generate and confirm the board ACCEPTED the generation.
  //
  // SUBMIT SIGNAL (verified live on the board, 4/4 times): the prompt box CLEARS
  // the instant a generation is accepted. The two old signals were both wrong —
  // the button does NOT flip to "Generating…" on this board, and new crafting
  // cards can lag past any reasonable wait (the card list is virtualized). So the
  // old code decided "not started", RE-SET the prompt, and clicked again — firing
  // 2-3 REAL generations per job (the first fire consumed the attached packs, so
  // the extras rendered pack-less junk) and then still reported failure.
  //
  // Rule: after any click, if the box hasn't cleared then NOTHING was submitted —
  // clicking again is safe. NEVER re-set the prompt inside this loop.
  async function fireGenerate(gen, box, promptText, priorIds) {
    const submitted = () => {
      const b = SEL.promptBox();
      return !!b && norm(b.value || "") === "";
    };
    for (let attempt = 0; attempt < 3; attempt++) {
      hardClick(SEL.generateBtn() || gen);
      for (let i = 0; i < 10; i++) {
        await sleep(700);
        if (submitted()) return true;
      }
    }
    return submitted();
  }

  // Every concept card carries a STABLE `data-ad-id`. But reading its image is
  // subtle — verified live on the board:
  //   * A card that is still rendering shows a PLACEHOLDER image that is SHARED
  //     across many cards (seen: one utfs.io URL on 17 of 20 cards). Grabbing the
  //     first <img> returned the placeholder — a spinner, not the concept.
  //   * The finished render is UNIQUE to its card (a cdn.uplane.com/cdn-cgi/image
  //     URL). Its filename does NOT reliably contain the ad-id, so we can't match
  //     on that — but "unique across all cards" cleanly separates real from
  //     placeholder, whatever host Uplane uses.
  // So: a URL is a placeholder if it repeats across cards OR sits on a crafting
  // card; a card's real image is any of its <img> URLs that ISN'T a placeholder.
  // WHY no images? "Nothing appeared" was the single catch-all, and it was wrong in
  // the case that actually happened: the board CREATED both concept cards and
  // Uplane's own renderer failed them ("Something went wrong…"), while its API
  // 404'd on the folder's concepts endpoint. Reporting a timeout sent debugging
  // after our prompt and our driver instead of the platform.
  //
  // Text-matched on purpose, not class-matched: an error string is stable across
  // Uplane's frequent class churn, and this is STRICTLY ADDITIVE — if nothing
  // matches, the message is exactly what it was before.
  const FAIL_RE = /went wrong|failed to generate|generation failed|couldn'?t generate|try again/i;

  function noImagesReason(job, priorIds) {
    let failed = 0, total = 0;
    try {
      // Query the DOM directly: adCards() returns {id, crafting, src} with no node
      // attached, so there is no element to read error text off. (Wrote it that way
      // first; the message silently degraded to the old one.)
      for (const el of document.querySelectorAll("[data-ad-id]")) {
        const id = el.getAttribute("data-ad-id");
        if (!id || (priorIds && priorIds.has && priorIds.has(id))) continue;  // pre-existing
        total++;
        if (FAIL_RE.test(norm(el.innerText || el.textContent || ""))) failed++;
      }
    } catch (e) { /* diagnosis must never throw over the real error */ }
    if (failed > 0) {
      // State only what the card SHOWS. An earlier version asserted the cause
      // ("its API was returning 404s") — true of the one incident it was written
      // for, wrong to claim for every future failure, and misleading besides:
      // Uplane has no public API, so that phrasing reads as nonsense in Slack.
      return `🛑 The board created ${total || job.count} concept(s) and Uplane failed to ` +
        `render ${failed} of them — the card itself shows an error, so this is not a ` +
        "missing generation and not the prompt or the driver. Check the board tab's " +
        "console for the cause. Credits were spent; retry once the board renders normally.";
    }
    if (total > 0) {
      return `⏳ ${total} concept(s) appeared but never finished rendering before the ` +
        "timeout. They may still complete on the board — check it before re-running, " +
        "because credits were already spent.";
    }
    return "No new concepts appeared before timeout.";
  }

  function adCards() {
    const nodes = Array.from(document.querySelectorAll("[data-ad-id]"));
    const imgsOf = (c) => Array.from(c.querySelectorAll("img"))
      .map((i) => i.currentSrc || i.src || "").filter((u) => /^https?:/.test(u));
    const freq = {};
    const craftingUrls = new Set();
    const rows = nodes.map((c) => {
      const t = norm(c.textContent).toLowerCase();
      const crafting = t.includes("crafting") || t.includes("generating");
      const urls = imgsOf(c);
      urls.forEach((u) => { freq[u] = (freq[u] || 0) + 1; if (crafting) craftingUrls.add(u); });
      return { id: c.getAttribute("data-ad-id"), crafting, urls };
    });
    const isPlaceholder = (u) => freq[u] > 1 || craftingUrls.has(u);
    return rows.map((r) => ({
      id: r.id,
      crafting: r.crafting,
      // the real render = a unique, non-placeholder image; "" while still loading
      src: r.urls.find((u) => !isPlaceholder(u)) || "",
    }));
  }
  const adIdSet = () => new Set(adCards().map((c) => c.id));

  // Wait for THIS job's batch. Identity is NOT "ad-id absent from the pre-Generate
  // snapshot" — the board only renders a fixed ~20-card window, so a pre-existing
  // concept that merely REFLOWS into view looks new and would be mis-collected
  // (verified: generating 1 concept made 5 cards "appear", only 1 truly new).
  // Instead: MINE = new ad-ids we actually observe in a CRAFTING state after we
  // fired. Reflowed old concepts are never crafting, so they can't sneak in. A
  // card is done when it has a real (unique, non-placeholder) image.
  // Set by the last waitForBatch run so the caller can report HOW the batch was
  // assembled — collected-by-crafting vs rescued-by-inference vs genuinely absent.
  let lastBatchStats = { collected: 0, inferred: 0, stillCrafting: 0, failed: 0 };

  async function waitForBatch(want, priorIds, jobId) {
    const mine = new Set();
    let firstPollIds = null;
    const t0 = Date.now();
    let lastBeat = 0;
    const collect = () => {
      const cards = adCards();
      cards.forEach((c) => { if (!priorIds.has(c.id) && c.crafting) mine.add(c.id); });
      return cards.filter((c) => mine.has(c.id) && !c.crafting && c.src);
    };
    // Ids present on the FIRST poll after Generate. A card that shows up later
    // APPEARED while we were watching — the discriminator that lets us rescue a
    // fast render without re-opening the door to collecting reflowed old cards.
    if (!firstPollIds) {
      firstPollIds = new Set(adCards().map((c) => c.id));
    }
    while (Date.now() - t0 < GEN_TIMEOUT_MS) {
      const done = collect();
      if (done.length >= want) {
        // Record the happy path too, or a full batch reports collected:0 in the log
        // (harmless for the message, since short==false, but wrong in the record).
        lastBatchStats = { collected: done.length, inferred: 0, stillCrafting: 0, failed: 0 };
        return done.slice(0, want).map((c) => c.src);
      }
      // HEARTBEAT: a render takes 1-2 min during which we'd otherwise make no
      // server call — the helper would think the driver died and the background
      // self-heal would RELOAD the tab mid-render, killing the job. Ping every
      // ~10s so the driver stays visibly alive while it waits.
      if (jobId && Date.now() - lastBeat > 10000) {
        lastBeat = Date.now();
        server("POST", "/job-progress", { job_id: jobId, phase: "generate" }).catch(() => {});
      }
      await sleep(2000);
    }
    // TIMEOUT. Everything attributed the strict way (seen crafting) comes first.
    const cards = adCards();
    const done = collect();
    const out = done.slice(0, want).map((c) => c.src);

    // RESCUE the shortfall. A concept that rendered between two 2s polls — or whose
    // "crafting" label wasn't showing when we sampled — never entered `mine`, so it
    // could never be collected even though it is finished and on the board. That is
    // what reported "1 of 2" for a pair that both rendered.
    //
    // The old blanket rule ("ad-id absent from the pre-Generate snapshot = mine") was
    // removed because the board renders ~20 cards, so an OLD concept scrolling into
    // view looked new. This is narrower on four counts: only at timeout, only to fill
    // a shortfall, only cards that APPEARED after our first poll (a reflowed old card
    // was already there), and only ones with a unique finished image. Anything taken
    // this way is reported as `inferred` so the preview is never passed off as certain.
    let inferred = 0, stillCrafting = 0, failed = 0;
    for (const c of cards) {
      if (priorIds.has(c.id) || mine.has(c.id)) continue;
      const el = document.querySelector(`[data-ad-id="${c.id}"]`);
      const txt = el ? norm(el.innerText || el.textContent || "") : "";
      if (FAIL_RE.test(txt)) { failed++; continue; }
      if (c.crafting) { stillCrafting++; continue; }
      if (out.length < want && c.src && firstPollIds && !firstPollIds.has(c.id)) {
        out.push(c.src);
        inferred++;
      }
    }
    lastBatchStats = { collected: done.length, inferred, stillCrafting, failed };
    return out;
  }

  // Re-attach collection (after a mid-generation reload): the crafting phase is
  // likely already over, so we can't use the crafting signal. Instead take the
  // NEW-since-baseline cards that now have a real (unique, non-placeholder)
  // image. priorIds is the snapshot persisted at Generate time.
  // RE-ATTACH collector. Deliberately uses the BLANKET rule (absent from prior_ids =
  // mine) that waitForBatch avoids: we arrive AFTER the concepts finished, so there is
  // no crafting state left to observe and no "appeared while watching" signal — those
  // guards would reject every genuine card. The compensating signal is that prior_ids
  // was persisted at Generate time rather than sampled now. Residual risk stands: an
  // old concept outside the ~20-card window at Generate time could reflow in and be
  // collected here. Do not "unify" this with waitForBatch; the situations differ.
  async function collectNew(want, priorIds, jobId) {
    const grab = () => adCards().filter((c) => !priorIds.has(c.id) && !c.crafting && c.src);
    const t0 = Date.now();
    let lastBeat = 0;
    while (Date.now() - t0 < GEN_TIMEOUT_MS) {
      const done = grab();
      if (done.length >= want) return done.slice(0, want).map((c) => c.src);
      if (jobId && Date.now() - lastBeat > 10000) {      // heartbeat (see waitForBatch)
        lastBeat = Date.now();
        server("POST", "/job-progress", { job_id: jobId, phase: "collect" }).catch(() => {});
      }
      await sleep(2000);
    }
    return grab().slice(0, want).map((c) => c.src);
  }

  // sendBg that can't hang forever: MV3 can kill the background worker mid-fetch
  // and its sendResponse never fires, so we race it against a timeout.
  function sendBgTimed(msg, ms) {
    return Promise.race([
      sendBg(msg),
      new Promise((res) => setTimeout(() => res({ ok: false, error: "timeout" }), ms)),
    ]);
  }

  async function urlToDataUrl(url) {
    // Route through the background worker: concept CDNs (cdn.uplane.com,
    // utfs.io) are CORS-locked to a content-script fetch. The worker bypasses
    // CORS and picks the right credentials mode per host (see background.js).
    // Retry once — the first call may just have been waking a slept worker.
    for (let attempt = 0; attempt < 2; attempt++) {
      const resp = await sendBgTimed({ type: "fetchImage", url }, 12000);
      if (resp && resp.ok && resp.dataUrl) return resp.dataUrl;
    }
    return null;
  }
  async function b64ToBlob(b64, type) {
    if (!b64) return null;
    const bin = atob(b64);
    const arr = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    return new Blob([arr], { type: type || "image/png" });
  }
  function dataUrlToBlob(dataUrl) {
    const comma = dataUrl.indexOf(",");
    const head = dataUrl.slice(0, comma), b64 = dataUrl.slice(comma + 1);
    const mime = (head.match(/data:([^;]+)/) || [])[1] || "image/png";
    const bin = atob(b64);
    const arr = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    return new Blob([arr], { type: mime });
  }

  // ---- per-product reference images -------------------------------------- #
  // Uplane copies dropped files OUT of the <input> and keeps its own list, shown
  // as thumbnails <img alt="Uploaded" src="blob:…"> each with a corner remove
  // badge. Verified live: attaching references does NOT wipe the prompt.
  // Reference thumbnails in the compose bar. VERIFIED LIVE 2026-07-24: history-
  // restored refs are <img alt="Uploaded"> served from cdn.uplane.com — NOT blob:.
  // The old `[src^="blob:"]` selector missed them entirely, which is why the driver
  // fell back to injecting Files into the hidden input. Match by alt only.
  const refThumbs = () => Array.from(document.querySelectorAll('img[alt="Uploaded"]'));

  // True count of attached references = visible thumbnails PLUS the "+N" overflow
  // badge the compose bar shows when more are attached than fit (e.g. 2 shown + "+3").
  function countAttached() {
    const box = SEL.promptBox();
    const bar = box && box.closest("div") && box.closest("div").parentElement;
    let extra = 0;
    if (bar) {
      const t = Array.from(bar.querySelectorAll("*"))
        .map((e) => (e.textContent || "").trim())
        .find((s) => /^\+\d+$/.test(s));
      if (t) extra = parseInt(t.slice(1), 10);
    }
    return refThumbs().length + extra;
  }

  // Attach the product packs by CLONING a recent prompt that still carries them.
  //
  // WHY NOT inject Files (what this used to do): verified live, the board only
  // registered ~2 of 5 files dropped into the hidden input in one change event,
  // then FROZE the renderer — so every job stalled in the attach step and got
  // orphaned before it could ever click Generate. That was the real "it's not
  // generating" bug, not the Generate click.
  //
  // WHAT WORKS (the same action a human takes): Uplane keeps every prompt's
  // attachments in its "Recent prompts" history. Clear the box to surface that
  // panel, click the newest row — it restores that prompt's text AND its packs —
  // then clear the cloned text (the packs stay attached, independent of the text).
  // Confirmed end-to-end on the live board: 1 click → prompt + all 5 packs attached.
  async function attachReferences(job) {
    const want = (job && job.reference_b64 && job.reference_b64.length) || 5;
    if (countAttached() >= want) return true;         // still attached from a prior fire
    const box = SEL.promptBox();
    if (!box) return false;
    setNativeValue(box, "");                          // an empty box surfaces "Recent prompts"
    box.focus();
    hardClick(box);
    // newest history row = a role=button holding a long prompt body + a "Nm ago" stamp
    const row = await waitFor(() => {
      const rows = Array.from(document.querySelectorAll('[role="button"]'))
        .filter((b) => (b.textContent || "").length > 500 &&
                       /\d+\s*(m|h|d)\s*ago/i.test(b.textContent || ""));
      return rows[0] || null;
    }, 6000);
    if (!row) return false;                            // no history to clone from
    hardClick(row);
    const ok = await waitFor(() => (countAttached() >= want ? true : null), 8000);
    setNativeValue(SEL.promptBox() || box, "");        // drop the cloned text; packs remain
    return !!ok;
  }

  // ---- bridge + poll loop ------------------------------------------------ #
  function sendBg(msg) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(msg, (resp) => {
          if (chrome.runtime.lastError) resolve({ ok: false, error: chrome.runtime.lastError.message });
          else resolve(resp);
        });
      } catch (e) { resolve({ ok: false, error: String(e) }); }
    });
  }
  const server = (method, path, body) => sendBg({ type: "server", method, path, body });
  function finish(jobId, status, image_urls, error, images, stats) {
    // `stats` lets the helper say WHY a batch is short instead of asserting a cause
    // it can't know. Optional: older/other call sites simply omit it.
    return server("POST", "/job-result",
                  { job_id: jobId, status, image_urls, error, images, stats });
  }

  // ONE job at a time (proven-stable). `busy` blocks a second job from starting
  // before the current one finishes; the driver id on the poll is what lets the
  // helper lease the board to THIS tab and refuse a second tab (two tabs firing on
  // one board eat each other's attached references).
  let busy = false;
  let polling = false;
  async function pollOnce() {
    if (busy || polling) return;
    polling = true;
    try {
      const here = folderIdFromUrl(location.href);
      const q = "/next-job?driver=" + DRIVER_ID + (here ? "&folder=" + encodeURIComponent(here) : "");
      const resp = await server("GET", q);
      const job = resp && resp.json && resp.json.job;
      if (job) {
        busy = true;
        try { await runJob(job); }
        catch (e) { await finish(job.id, "error", [], "🚫 Not generated — driver error: " + String(e)); }
        finally { busy = false; }
      }
    } finally {
      polling = false;
    }
  }

  // Drive the poll from a Web Worker: Chrome heavily throttles setTimeout in
  // background/unfocused tabs, which would stall the driver until you click the
  // board tab. Worker timers are NOT background-throttled. Fall back to
  // setTimeout if the page's CSP blocks blob workers.
  function startPolling() {
    try {
      const url = URL.createObjectURL(
        new Blob([`setInterval(() => postMessage(0), ${POLL_MS});`], { type: "text/javascript" })
      );
      const w = new Worker(url);
      w.onmessage = () => pollOnce();
      console.log("[adbot] polling via Web Worker (background-tab safe)");
    } catch (e) {
      console.log("[adbot] Web Worker unavailable, using setTimeout", e);
      (function loop() { pollOnce().finally(() => setTimeout(loop, POLL_MS)); })();
    }
  }
  // Also poll the moment the tab regains focus, for instant catch-up.
  document.addEventListener("visibilitychange", () => { if (!document.hidden) pollOnce(); });
  startPolling();

  // ---- calibration ------------------------------------------------------- #
  window.__adbotCalibrate = () => {
    const rd = SEL.countReadout();
    const chip = SEL.productChip();
    return {
      folderTitle: currentFolderTitle(),
      promptBox: !!SEL.promptBox(),
      fileInput: !!SEL.fileInput(),
      generateBtn: SEL.generateBtn() ? norm(SEL.generateBtn().textContent) : null,
      countReadout: rd ? norm(rd.node.textContent) : null,
      productChip: chip ? norm(chip.textContent) : null,
      testSlackbotFolder: !!SEL.folderAnchor("test_slackbot"),
      conceptImages: SEL.conceptImages().length,
    };
  };
  console.log("[adbot] driver loaded (verify-gated) — run __adbotCalibrate()");
})();
