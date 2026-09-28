/* Background worker: the only context that can reach http://127.0.0.1 from an
   https:// page, and the one that fetches creative images cross-origin. Mirrors
   the Uplane Compliance Copilot bridge. */

const SERVER_BASES = ["http://127.0.0.1:8788", "http://localhost:8788"];

async function callServer({ method = "GET", path = "/", body = null }) {
  let lastErr = "no server";
  for (const base of SERVER_BASES) {
    try {
      const res = await fetch(base + path, {
        method,
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      const text = await res.text();
      let data;
      try { data = JSON.parse(text); } catch (e) { data = text; }
      return { ok: res.ok, status: res.status, data };
    } catch (e) {
      lastErr = String(e);
    }
  }
  return { ok: false, status: 0, error: "Can't reach the Creative Studio helper on 127.0.0.1:8788. Is run.sh running? (" + lastErr + ")" };
}

async function fetchImageAsDataUrl(url) {
  try {
    const r = await fetch(url, { credentials: "include" });
    if (!r.ok) return { ok: false, error: "HTTP " + r.status };
    const blob = await r.blob();
    const dataUrl = await new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => resolve(fr.result);
      fr.onerror = () => reject(fr.error);
      fr.readAsDataURL(blob);
    });
    return { ok: true, dataUrl, contentType: blob.type };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    if (msg.type === "server") sendResponse(await callServer(msg));
    else if (msg.type === "fetchImage") sendResponse(await fetchImageAsDataUrl(msg.url));
    else if (msg.type === "captureVisibleTab") {
      try {
        const dataUrl = await chrome.tabs.captureVisibleTab({ format: "png" });
        sendResponse({ ok: true, dataUrl });
      } catch (e) {
        sendResponse({ ok: false, error: String(e) });
      }
    } else sendResponse({ ok: false, error: "unknown message" });
  })();
  return true; // keep the channel open for the async response
});
