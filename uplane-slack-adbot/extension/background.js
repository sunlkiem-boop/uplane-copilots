/* Uplane Slack Ad-Bot Driver — service worker.
 *
 * The content script runs on an https page and cannot fetch the http localhost
 * helper directly (mixed content). So it relays every helper call through here.
 *
 *   content -> { type:"server", method, path, body } -> background -> fetch()
 */
const BASE = "http://127.0.0.1:8790";

// cdn.uplane.com blocks requests without a board Referer (hotlink protection),
// so our background image fetch 404s. Spoof the Referer to app.uplane.com for
// CDN requests so the fetch looks like it came from the board.
function installReferrerRule() {
  chrome.declarativeNetRequest.updateDynamicRules(
    {
      removeRuleIds: [1],
      addRules: [
        {
          id: 1,
          priority: 1,
          action: {
            type: "modifyHeaders",
            requestHeaders: [
              { header: "referer", operation: "set", value: "https://app.uplane.com/" },
              { header: "origin", operation: "set", value: "https://app.uplane.com" },
            ],
          },
          condition: { urlFilter: "||cdn.uplane.com/", resourceTypes: ["xmlhttprequest"] },
        },
      ],
    },
    () => void chrome.runtime.lastError
  );
}
chrome.runtime.onInstalled.addListener(installReferrerRule);
chrome.runtime.onStartup.addListener(installReferrerRule);
installReferrerRule();

// --------------------------------------------------------------------------- //
// Auto-open the board tab.
// The driver (content script) only exists inside an app.uplane.com/concepts page,
// so with no tab open nothing can run. THIS worker runs without any tab, so it
// wakes on an alarm, asks the helper whether a job is waiting, and opens the
// board itself at the job's folder URL. => you never have to keep a tab open.
// --------------------------------------------------------------------------- //
const BOARD_MATCH = "https://app.uplane.com/concepts/*";
// Cap on simultaneous board tabs. Each one is a real browser tab running a real
// generation, and Uplane throttles concurrent renders, so more tabs than clients
// buys nothing and risks the account.
const MAX_BOARD_TABS = 3;
const folderId = (u) => {
  const m = String(u || "").match(/concepts\/([0-9a-fA-F-]{16,})/);
  return m ? m[1] : null;
};

async function ensureBoardTab() {
  try {
    const r = await fetch(BASE + "/pending", { cache: "no-store" });
    if (!r.ok) return;
    const d = await r.json();
    if (!d || !d.waiting || !d.folder_url) return;
    const tabs = await chrome.tabs.query({ url: BOARD_MATCH });
    if (tabs && tabs.length) {
      // A board tab exists — but is its DRIVER alive? Reloading the extension
      // kills the content script in already-open tabs without re-injecting, so
      // the tab looks fine while nothing runs. If no driver has checked in for a
      // LONG time, reload the tab to re-inject the driver.
      //
      // Threshold is 150s, NOT 45s: a driver actively rendering a job now sends a
      // /job-progress heartbeat every ~10s, so a truly-alive driver never crosses
      // even 45s. 45s used to fire mid-render (a render takes 1-2 min and the old
      // driver made no calls during it), reloading the tab and KILLING the job in
      // progress — the run made zero completions until this was fixed. 150s only
      // trips when the driver has genuinely stopped heartbeating.
      if (typeof d.driver_idle_s === "number" && d.driver_idle_s > 150) {
        try { await chrome.tabs.reload(tabs[0].id); } catch (e) {}
        return;                     // give the revived driver a cycle first
      }
    }
    // ONE TAB PER CLIENT. Two clients queued behind a single tab used to take
    // turns through it, so a 40-job batch blocked the other client for hours.
    // Each board with waiting work gets its own tab, and /next-job hands a tab
    // the jobs for the board it is already on, so they don't fight.
    const folders = (d.folders && d.folders.length ? d.folders : [d.folder_url]).slice(0, MAX_BOARD_TABS);
    const open = new Set((tabs || []).map((t) => folderId(t.url)).filter(Boolean));
    for (const url of folders) {
      const id = folderId(url);
      if (id && open.has(id)) continue;                 // already has a tab
      if (open.size >= MAX_BOARD_TABS) break;           // don't spawn tabs forever
      // open in the background so it doesn't steal focus
      await chrome.tabs.create({ url, active: false });
      if (id) open.add(id);
    }
  } catch (e) {
    // helper not running / not reachable — nothing to do
  }
}

chrome.alarms.create("adbot-board-watch", { periodInMinutes: 0.5 });   // 30s: alarms wake the worker
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === "adbot-board-watch") ensureBoardTab();
});

// When the extension (re)starts, EVERY content script it had injected is now
// dead — Chrome kills them on reload without re-injecting. So proactively reload
// any open board tab to bring the driver straight back, instead of waiting for
// the idle-timeout heuristic. This is what makes "oops I reloaded the extension"
// self-heal: the board tab reloads itself and in-flight jobs re-attach.
async function reviveBoardTabs() {
  try {
    const tabs = await chrome.tabs.query({ url: BOARD_MATCH });
    for (const t of tabs) { try { await chrome.tabs.reload(t.id); } catch (e) {} }
  } catch (e) {}
}
const onBoot = () => { reviveBoardTabs(); ensureBoardTab(); };
chrome.runtime.onStartup.addListener(onBoot);
chrome.runtime.onInstalled.addListener(onBoot);

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  // Fetch a concept image. Content-script fetch is CORS-blocked, so the
  // background worker does it (host_permissions bypass CORS).
  //
  // Concepts live on TWO hosts and each needs a DIFFERENT fetch — Uplane is
  // mid-migration, so a single board carries images on both at once:
  //   * utfs.io / *.ufs.sh (UploadThing, public): returns Access-Control-Allow-
  //     Origin:* which FORBIDS credentials — a credentialed fetch is rejected
  //     ("...must not be the wildcard '*' when credentials mode is 'include'").
  //   * cdn.uplane.com (hotlink-protected): needs BOTH the spoofed Referer (the
  //     declarativeNetRequest rule above) AND the session cookies.
  // So try the public way (omit) first, then the credentialed way. Host-agnostic
  // on purpose: it survives Uplane switching CDNs again without a code change.
  if (msg && msg.type === "fetchImage") {
    (async () => {
      // One shared deadline across BOTH attempts — the content script only
      // waits 12s per call, so the worker must always answer inside that.
      const deadline = Date.now() + 9000;
      let lastStatus = 0;
      for (const credentials of ["omit", "include"]) {
        const remaining = deadline - Date.now();
        if (remaining <= 200) break;
        const ctrl = new AbortController();
        const to = setTimeout(() => ctrl.abort(), remaining);
        try {
          const r = await fetch(msg.url, { credentials, signal: ctrl.signal });
          if (!r.ok) { lastStatus = r.status; continue; }   // try the other mode
          const blob = await r.blob();
          const dataUrl = await new Promise((res) => {
            const fr = new FileReader();
            fr.onload = () => res(fr.result);
            fr.onerror = () => res(null);
            fr.readAsDataURL(blob);
          });
          if (dataUrl) { sendResponse({ ok: true, dataUrl }); return; }
        } catch (e) {
          // CORS rejection or abort — fall through to the next credentials mode
        } finally {
          clearTimeout(to);
        }
      }
      sendResponse({ ok: false, status: lastStatus,
                     error: "image fetch failed (both credential modes) for " + msg.url });
    })();
    return true; // async
  }
  if (msg && msg.type === "server") {
    const opts = { method: msg.method || "GET" };
    if (msg.body !== undefined) {
      opts.headers = { "Content-Type": "application/json" };
      opts.body = JSON.stringify(msg.body);
    }
    fetch(BASE + msg.path, opts)
      .then(async (r) => {
        const text = await r.text();
        let json = null;
        try { json = JSON.parse(text); } catch (_) {}
        sendResponse({ ok: r.ok, status: r.status, json, text });
      })
      .catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true; // async
  }
});
