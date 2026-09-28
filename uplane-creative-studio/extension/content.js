/* Runs on Meta Ads Manager. Shows a small panel that:
   - confirms you're logged in (re-check after login),
   - lets you pick the client (Creative Studio folder),
   - scrapes each ad's creative thumbnail + name from the Ads table,
   - fetches the image bytes and sends them to the helper, matched by ad name.

   NOTE: Meta's DOM is obfuscated and changes often. The selectors here are
   heuristic (small fbcdn images = ad thumbnails; longest plausible row text =
   ad name). If a scan finds 0 ads or wrong names, that's the bit to tune. */
(() => {
  if (window.top !== window) return;            // top frame only
  if (document.getElementById("ucs-panel")) return;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const norm = (s) => (s || "").toLowerCase().replace(/◤|◢/g, "").replace(/\s+/g, " ").trim();
  const sendBg = (msg) => new Promise((res) => chrome.runtime.sendMessage(msg, res));
  const server = (method, path, body) => sendBg({ type: "server", method, path, body });

  // ---- panel UI (shadow DOM so Meta's CSS can't touch it) ------------------ #
  const host = document.createElement("div");
  host.id = "ucs-panel";
  Object.assign(host.style, { position: "fixed", right: "16px", bottom: "16px", zIndex: 2147483647 });
  document.documentElement.appendChild(host);
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `
    <style>
      .p{width:320px;background:#16161B;color:#E7E7EC;border:1px solid #2A2A33;border-radius:12px;
         font:13px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;box-shadow:0 12px 36px rgba(0,0,0,.45);overflow:hidden}
      .h{display:flex;align-items:center;gap:8px;padding:11px 14px;border-bottom:1px solid #2A2A33;font-weight:650}
      .dot{width:7px;height:7px;border-radius:50%;background:#8FA0C8}
      .b{padding:12px 14px;display:flex;flex-direction:column;gap:10px}
      select,button{font:inherit}
      select{width:100%;padding:7px 9px;background:#1C1C22;color:#E7E7EC;border:1px solid #34343F;border-radius:8px}
      .row{display:flex;gap:8px}
      button{flex:1;padding:8px 10px;border-radius:8px;border:1px solid #34343F;background:#23232B;color:#E7E7EC;font-weight:600;cursor:pointer}
      button.primary{background:#8FA0C8;color:#11131A;border-color:#8FA0C8}
      button:hover{filter:brightness(1.08)}
      .log{max-height:150px;overflow:auto;background:#0E0E11;border:1px solid #2A2A33;border-radius:8px;padding:8px;font-size:12px;color:#B6B6C0;white-space:pre-wrap;line-height:1.45}
      .muted{color:#8A8A96;font-size:12px}
      .warn{background:rgba(224,114,106,.13);color:#E0726A;border:1px solid rgba(224,114,106,.3);border-radius:8px;padding:8px 10px;font-size:12px}
      .x{margin-left:auto;cursor:pointer;color:#8A8A96;border:0;background:none;font-size:15px;flex:0}
      label.ck{display:flex;gap:7px;align-items:center;font-size:12px;color:#B6B6C0;cursor:pointer}
      .hidden{display:none}
      .prog{height:7px;background:#23232B;border-radius:4px;overflow:hidden;margin:2px 0 4px}
      .prog-bar{height:100%;width:0;background:#8FA0C8;transition:width .25s}
      #progtext{font-size:12px;margin-bottom:4px}
    </style>
    <div class="p">
      <div class="h"><span class="dot"></span>Creative Studio — Meta pull <button class="x" id="x">✕</button></div>
      <div class="b" id="body"></div>
    </div>`;
  root.getElementById("x").onclick = () => host.remove();
  const body = root.getElementById("body");
  const logbox = () => root.getElementById("log");
  const log = (m) => { const l = logbox(); if (l) { l.textContent += (l.textContent ? "\n" : "") + m; l.scrollTop = l.scrollHeight; } };
  function setProgress(done, total, label) {
    const wrap = root.getElementById("progwrap"), bar = root.getElementById("progbar"), txt = root.getElementById("progtext");
    if (!wrap) return;
    if (total == null) { wrap.classList.add("hidden"); if (txt) txt.textContent = ""; return; }
    wrap.classList.remove("hidden");
    if (bar) bar.style.width = total ? Math.round((done / total) * 100) + "%" : "0%";
    if (txt) txt.textContent = label || `${done} / ${total}`;
  }
  async function waitForMatch(name, timeoutMs = 4500) {
    const t0 = Date.now();
    while (Date.now() - t0 < timeoutMs) {
      const rows = findAdRows();
      const m = rows.find((r) => norm(r.name) === norm(name))
        || rows.find((r) => norm(r.name).includes(norm(name)) || norm(name).includes(norm(r.name)));
      if (m) return m;
      await sleep(300);
    }
    const rows = findAdRows();
    return rows.length === 1 ? rows[0] : null;
  }

  // ---- login gate ---------------------------------------------------------- #
  function isLoginPage() {
    return /\/login|checkpoint|\/help\//i.test(location.href) || !!document.querySelector('input[type="password"]');
  }

  function renderLogin() {
    body.innerHTML = `
      <div class="warn">This looks like a Meta login / checkpoint page. Log in to the Google/Meta account that has the client ad accounts, then check the box.</div>
      <label class="ck"><input type="checkbox" id="loggedin"> I'm logged in to Meta Ads Manager</label>
      <button class="primary" id="recheck">Re-check</button>`;
    root.getElementById("recheck").onclick = () => {
      if (root.getElementById("loggedin").checked) render();
      else alert("Tick the box once you've logged in.");
    };
  }

  // ---- main panel ---------------------------------------------------------- #
  async function render() {
    if (isLoginPage()) return renderLogin();
    body.innerHTML = `
      <div class="muted">Open the <b>Ads</b> tab for the right client account (check the "owned by" name in the account switcher), then pull.</div>
      <select id="client"><option value="">Loading clients…</option></select>
      <div class="row">
        <button class="primary" id="auto">Get the view's ads</button>
        <button id="scan">Scan all</button>
      </div>
      <button id="diag">Send diagnostics to Claude</button>
      <div class="muted" id="acct"></div>
      <div class="prog hidden" id="progwrap"><div class="prog-bar" id="progbar"></div></div>
      <div class="muted" id="progtext"></div>
      <div class="log" id="log">In the app, open a view and click “Pull creatives”. Then here click “Get the view's ads” — it scrolls the list to find just those ads and grabs each creative, stopping as soon as it has them all. “Scan all” grabs every ad in the account (slow on big accounts). If something looks off, click “Send diagnostics to Claude”.</div>`;
    root.getElementById("scan").onclick = scanAll;
    root.getElementById("auto").onclick = getViewAds;
    root.getElementById("diag").onclick = sendDiagnostics;
    await loadClients();
    showAccountHint();
  }

  async function loadClients() {
    const sel = root.getElementById("client");
    const r = await server("GET", "/clients");
    if (!r || !r.ok) { sel.innerHTML = `<option value="">helper offline (start run.sh)</option>`; log("Can't reach the helper on 127.0.0.1:8788."); return; }
    const list = r.data.clients || [];
    sel.innerHTML = `<option value="">Select client…</option>` + list.map((c) => `<option value="${c}">${c}</option>`).join("");
    // preselect from the most recent pull request, if any
    const pr = await server("GET", "/pull-request");
    if (pr.ok && pr.data && pr.data.client && list.includes(pr.data.client)) sel.value = pr.data.client;
  }

  function showAccountHint() {
    // best-effort: surface a prominent account-name string near the top bar
    const hint = root.getElementById("acct");
    const guess = guessAccountName();
    if (hint && guess) hint.innerHTML = `Account on screen: <b>${guess}</b> — confirm the client matches its owner.`;
  }
  function guessAccountName() {
    const cand = Array.from(document.querySelectorAll('[role="button"], a, span'))
      .map((e) => (e.textContent || "").trim())
      .find((t) => /ad account|owned by|account:/i.test(t));
    return cand ? cand.slice(0, 80) : null;
  }

  // ---- scraping ------------------------------------------------------------ #
  function looksLikeName(t) {
    if (!t || t.length < 3 || t.length > 180) return false;
    if (/^[\d.,$%\s+\-x×]+$/.test(t)) return false;                       // pure metric
    if (/^(active|inactive|off|on|in review|not delivering|learning|completed|draft|error)$/i.test(t)) return false;
    return /[a-z]/i.test(t);
  }
  function findAdName(img) {
    let row = img.closest('[role="row"]') || img.closest("tr");
    if (!row) { let el = img; for (let i = 0; i < 7 && el.parentElement; i++) { el = el.parentElement; if (el.getBoundingClientRect().width > 380) { row = el; break; } } }
    if (!row) return null;
    const cands = [];
    row.querySelectorAll("a, span, div").forEach((e) => {
      if (e.children.length) return;                                       // leaf text only
      const t = (e.textContent || "").trim();
      if (looksLikeName(t)) cands.push(t);
    });
    cands.sort((a, b) => b.length - a.length);                             // longest plausible = ad name
    return cands[0] || null;
  }
  function findAdRows() {
    const out = [], seen = new Set();
    document.querySelectorAll("img").forEach((img) => {
      const src = img.currentSrc || img.src || "";
      if (!src || src.startsWith("data:")) return;
      if (!/scontent/i.test(src)) return;          // ad creatives are on scontent CDN, not static.xx (nav icons)
      if (/\.svg(\?|$)/i.test(src)) return;         // skip svg icons
      const r = img.getBoundingClientRect();
      if (r.width < 30 || r.width > 160 || r.height < 30 || r.height > 160) return;  // real thumbs ~46px; icons ~24-28
      const name = findAdName(img);
      if (!name || seen.has(norm(name))) return;
      seen.add(norm(name));
      out.push({ name, imgUrl: src, el: img });
    });
    return out;
  }

  async function captureAndSend(client, rows) {
    const batch = [];
    for (const row of rows) {
      const res = await sendBg({ type: "fetchImage", url: row.imgUrl });
      if (res && res.ok) batch.push({ name: row.name, image_base64: res.dataUrl });
      else log(`  · couldn't fetch image for "${row.name}"`);
    }
    if (!batch.length) { log("Nothing to send."); return; }
    const r = await server("POST", "/creatives", { client, ads: batch });
    if (r.ok) log(`✓ Sent ${r.data.saved} creatives for ${client}. Helper now holds ${r.data.total}. Reload the Creative Studio tab to see thumbnails.`);
    else log("Helper error sending creatives: " + (r.error || r.status));
  }

  // The Ads table is virtualized (rows are position:absolute, transform:translate),
  // so rows only exist in the DOM while on screen. Find the element that actually
  // scrolls them — anchored on a REAL ad thumbnail (scontent ~46px), NOT a nav icon
  // (the first fbcdn img on the page is a static.xx.fbcdn nav svg, which broke this).
  function adThumbAnchor() {
    for (const img of document.querySelectorAll('img[src*="scontent"]')) {
      const r = img.getBoundingClientRect();
      if (r.width >= 30 && r.width <= 160) return img;
    }
    return null;
  }
  function getScroller() {
    // Don't trust computed overflow (Meta's container reads oddly). Instead, walk up
    // from a real ad thumbnail, collect ancestors taller than their viewport, and pick
    // the first whose scrollTop ACTUALLY moves when nudged — that's the real scroller.
    const anchor = adThumbAnchor();
    const cands = [];
    let node = anchor ? anchor.parentElement : null;
    while (node && node !== document.documentElement) {
      if (node.scrollHeight > node.clientHeight + 80) cands.push(node);
      node = node.parentElement;
    }
    for (const c of cands) {
      const before = c.scrollTop;
      c.scrollTop = before + 150;
      const moved = c.scrollTop !== before;
      c.scrollTop = before;
      if (moved) return c;
    }
    return document.scrollingElement || document.documentElement;
  }
  function scrollAncestry() {
    const anchor = adThumbAnchor();
    const out = [];
    let node = anchor ? anchor.parentElement : null, depth = 0;
    while (node && node !== document.documentElement && depth < 30) {
      const s = getComputedStyle(node);
      out.push({
        tag: node.tagName, cls: (node.className || "").toString().slice(0, 40),
        overflowY: s.overflowY, scrollH: node.scrollHeight, clientH: node.clientHeight,
        taller: node.scrollHeight > node.clientHeight + 80,
      });
      node = node.parentElement; depth++;
    }
    return out;
  }

  // Walk the virtualized ads list by bringing the LAST visible row to the top with
  // scrollIntoView — container-agnostic: the browser scrolls whatever needs to move,
  // so it works even though Meta's list has no findable scroll container. If `want`
  // (a Set of normalized names) is given, stop as soon as all are found.
  async function scrollCapture(want) {
    log("Walking the ads list…");
    const collected = new Map();
    let stable = 0, last = -1;
    for (let i = 0; i < 600 && stable < 6; i++) {
      const rows = findAdRows();
      rows.forEach((r) => { const k = norm(r.name); if (k && !collected.has(k)) collected.set(k, r); });
      const found = want ? [...collected.keys()].filter((k) => want.has(k)).length : collected.size;
      setProgress(want ? found : Math.min(i, 110), want ? want.size : 120,
        want ? `Found ${found}/${want.size} (scanned ${collected.size})` : `Scanning… ${collected.size} found`);
      if (want && found >= want.size) { log("Found all target ads."); break; }
      if (collected.size === last) stable++; else { last = collected.size; stable = 0; }
      // Rows are absolutely positioned, so DOM order != visual order. Pick the
      // VISUALLY lowest rendered row and bring it to the top to reveal the next batch.
      let lastEl = null, maxTop = -Infinity;
      for (const r of rows) {
        if (!r.el.isConnected) continue;
        const t = r.el.getBoundingClientRect().top;
        if (t > maxTop) { maxTop = t; lastEl = r.el; }
      }
      if (lastEl) lastEl.scrollIntoView({ block: "start" });
      else stable++;
      await sleep(650);
    }
    return Array.from(collected.values());
  }

  async function scanAll() {
    const client = root.getElementById("client").value;
    if (!client) return log("Pick a client first.");
    const rows = await scrollCapture(null);
    log(`Done — ${rows.length} unique ad(s) found.`);
    if (!rows.length) return log("No ad thumbnails detected. Make sure you're on the Ads tab with creatives visible. If it still finds nothing, click “Send diagnostics to Claude”.");
    await captureAndSend(client, rows);
  }

  async function getViewAds() {
    const client = root.getElementById("client").value;
    if (!client) return log("Pick a client first.");
    const pr = await server("GET", "/pull-request?client=" + encodeURIComponent(client));
    const names = pr.ok && pr.data && pr.data.ad_names ? pr.data.ad_names : [];
    if (!names.length) return log('No "Pull creatives" request for ' + client + '. In the app, open a view and click Pull creatives, then come back here.');
    log(`Looking for ${names.length} ad(s) from the app's view…`);
    const want = new Set(names.map(norm));
    const rows = await scrollCapture(want);
    const matched = rows.filter((r) => want.has(norm(r.name)));
    const missing = names.filter((n) => !rows.some((r) => norm(r.name) === norm(n)));
    log(`Found ${matched.length}/${names.length}.` + (missing.length ? " Missing: " + missing.slice(0, 5).join(", ") + (missing.length > 5 ? " …" : "") : ""));
    if (!matched.length) return log("None of the view's ads were found in this account (they may be archived, or in a different account). Try “Scan all”.");
    await captureAndSend(client, matched);
  }

  function findSearchBox() {
    const sels = [
      'input[placeholder*="Search" i]', 'input[aria-label*="Search" i]',
      'input[type="search"]', '[role="searchbox"]', '[role="combobox"] input',
      'input[placeholder*="name" i]', 'input[aria-label*="filter" i]',
    ];
    for (const s of sels) {
      for (const el of document.querySelectorAll(s)) {
        if (el.offsetParent !== null && el.getBoundingClientRect().width > 80) return el;
      }
    }
    // fallback: first sizable visible text input near the top of the page
    const inputs = Array.from(document.querySelectorAll('input[type="text"], input:not([type])'))
      .filter((i) => i.offsetParent && i.getBoundingClientRect().top < 320 && i.getBoundingClientRect().width > 120);
    return inputs[0] || null;
  }
  function setSearch(box, val) {
    box.focus();
    const proto = box.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(box, val);
    box.dispatchEvent(new Event("input", { bubbles: true }));
    box.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true, key: " " }));
  }
  function describeBox(box) {
    if (!box) return "none";
    return (box.getAttribute("placeholder") || box.getAttribute("aria-label") || box.tagName + (box.type ? "[" + box.type + "]" : "")).slice(0, 60);
  }

  async function autoSearchFromApp() {
    try {
      const client = root.getElementById("client").value;
      if (!client) return log("Pick a client first.");
      const pr = await server("GET", "/pull-request?client=" + encodeURIComponent(client));
      const names = pr.ok && pr.data && pr.data.ad_names ? pr.data.ad_names : [];
      if (!names.length) return log('No "Pull creatives" request found for ' + client + '. In the app, open a view and click Pull creatives, then come back here.');
      const box = findSearchBox();
      if (!box) return log('Could not find the Ads search box on this page. Click "Send diagnostics to Claude" so I can target it precisely.');
      log(`Search box found: ${describeBox(box)}`);
      log(`Searching ${names.length} ad(s) by name…`);
      let sent = 0, missed = 0;
      for (let i = 0; i < names.length; i++) {
        const name = names[i];
        setProgress(i, names.length, `Searching ${i + 1}/${names.length}: ${name.slice(0, 28)}`);
        setSearch(box, name);
        const match = await waitForMatch(name);
        if (match) {
          const res = await sendBg({ type: "fetchImage", url: match.imgUrl });
          if (res && res.ok) {
            const r = await server("POST", "/creatives", { client, ads: [{ name, image_base64: res.dataUrl }] });
            if (r.ok) { sent++; log(`  ✓ ${name}`); } else log("  · helper error saving: " + name);
          } else log("  · image fetch failed: " + name);
        } else { missed++; log(`  – not found: ${name}`); }
      }
      setSearch(box, "");
      setProgress(names.length, names.length, `Done — ${sent} captured, ${missed} not found`);
      log(`Done — ${sent} captured, ${missed} not found. Reload the app to see thumbnails.`);
    } catch (e) {
      setProgress(null);
      log("Auto-search error: " + String(e).slice(0, 180));
    }
  }

  // ---- diagnostics: dump the DOM shape so selectors can be tuned blind ----- #
  function findRow(img) {
    let row = img.closest('[role="row"]') || img.closest("tr") || img.closest('[role="gridcell"]');
    if (!row) { let el = img; for (let i = 0; i < 8 && el.parentElement; i++) { el = el.parentElement; if (el.getBoundingClientRect().width > 380) { row = el; break; } } }
    return row;
  }
  function buildDiagnostics() {
    const imgs = Array.from(document.querySelectorAll("img"));
    let fbcdn = 0, smallFb = 0;
    const samples = [];
    for (const img of imgs) {
      const src = img.currentSrc || img.src || "";
      const isFb = /fbcdn|scontent/i.test(src);
      if (isFb) fbcdn++;
      const r = img.getBoundingClientRect();
      const small = r.width >= 14 && r.width <= 160 && r.height >= 14 && r.height <= 160;
      if (isFb && small) {
        smallFb++;
        if (samples.length < 6) {
          const row = findRow(img);
          const texts = [];
          if (row) row.querySelectorAll("a, span, div").forEach((e) => {
            if (!e.children.length) { const t = (e.textContent || "").trim(); if (t && t.length <= 180) texts.push(t); }
          });
          samples.push({
            imgW: Math.round(r.width), imgH: Math.round(r.height),
            srcHead: src.slice(0, 100), alt: img.alt || "",
            rowTag: row ? row.tagName : null, rowRole: row ? row.getAttribute("role") : null,
            pickedName: findAdName(img),
            textCandidates: texts.slice(0, 14),
            rowHtml: row ? row.outerHTML.slice(0, 1600) : null,
          });
        }
      }
    }
    return {
      url: location.href, isLogin: isLoginPage(), title: document.title,
      imgTotal: imgs.length, fbcdnImgs: fbcdn, smallFbcdnImgs: smallFb,
      roleRows: document.querySelectorAll('[role="row"]').length,
      gridcells: document.querySelectorAll('[role="gridcell"]').length,
      tables: document.querySelectorAll("table").length,
      searchBox: describeBox(findSearchBox()),
      inputs: Array.from(document.querySelectorAll("input")).slice(0, 14).map((i) => {
        const r = i.getBoundingClientRect();
        return { placeholder: i.placeholder || "", ariaLabel: i.getAttribute("aria-label") || "", type: i.type || "", w: Math.round(r.width), top: Math.round(r.top) };
      }),
      accountGuess: guessAccountName(),
      scrollAncestry: scrollAncestry(),
      scrollerPicked: (() => { const s = getScroller(); return s === document.scrollingElement || s === document.documentElement ? "window" : (s.tagName + "." + (s.className || "").toString().slice(0, 30)); })(),
      samples,
    };
  }
  async function sendDiagnostics() {
    const client = (root.getElementById("client") || {}).value || "";
    const report = buildDiagnostics();
    log(`Diagnostics: ${report.smallFbcdnImgs} small fbcdn img(s), ${report.roleRows} role=row, ${report.gridcells} gridcell, search box ${report.searchBoxFound ? "found" : "NOT found"}.`);
    const r = await server("POST", "/diagnostics", { client, report });
    if (r.ok) { log('✓ Sent to the helper. Tell Claude "diagnostics sent" and it will tune the selectors.'); return; }
    log("Helper unreachable — copying the dump to your clipboard instead; paste it to Claude.");
    try { await navigator.clipboard.writeText(JSON.stringify(report, null, 2)); log("✓ Copied to clipboard."); }
    catch (e) { console.log("UCS_DIAGNOSTICS", report); log("Clipboard blocked — open DevTools console and copy the UCS_DIAGNOSTICS object."); }
  }

  render();
})();
