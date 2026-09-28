// Background service worker.
//
// The content script runs in the page (https://app.uplane.com) and therefore
// CANNOT fetch http://localhost (mixed content) or call chrome.tabs.* APIs.
// So it delegates three things to us:
//   - server : proxy a request to the local helper on 127.0.0.1:8787
//   - fetchImage : fetch a creative's image URL and return it as a data URL
//   - captureVisibleTab : screenshot the visible tab (for the crop fallback)

const SERVER_BASES = ["http://127.0.0.1:8787", "http://localhost:8787"];

async function callServer({ method = "GET", path = "/", body }) {
  let lastErr;
  for (const base of SERVER_BASES) {
    try {
      const resp = await fetch(base + path, {
        method,
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      const text = await resp.text();
      let data;
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        data = { raw: text };
      }
      return { ok: resp.ok, status: resp.status, data };
    } catch (e) {
      lastErr = e;
    }
  }
  return {
    ok: false,
    status: 0,
    error:
      "Can't reach the local helper. Is it running? (cd server && ./run.sh) — " +
      (lastErr && lastErr.message ? lastErr.message : "connection failed"),
  };
}

async function fetchImageAsDataUrl(url) {
  const resp = await fetch(url, { credentials: "omit" });
  if (!resp.ok) throw new Error("image fetch HTTP " + resp.status);
  const blob = await resp.blob();
  const contentType = blob.type || "image/png";
  const dataUrl = await new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(fr.result);
    fr.onerror = () => reject(fr.error);
    fr.readAsDataURL(blob);
  });
  return { dataUrl, contentType };
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    try {
      if (msg.type === "server") {
        sendResponse(await callServer(msg));
      } else if (msg.type === "fetchImage") {
        const r = await fetchImageAsDataUrl(msg.url);
        sendResponse({ ok: true, ...r });
      } else if (msg.type === "captureVisibleTab") {
        const dataUrl = await chrome.tabs.captureVisibleTab({ format: "png" });
        sendResponse({ ok: true, dataUrl });
      } else {
        sendResponse({ ok: false, error: "unknown message type: " + msg.type });
      }
    } catch (e) {
      sendResponse({ ok: false, error: e && e.message ? e.message : String(e) });
    }
  })();
  return true; // keep the message channel open for the async response
});
