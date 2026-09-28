// Extension entrypoint. capture-core.js loads first (see manifest) and exposes
// window.__uplaneEditCapture.
//
// GATING — why this lives here and not in the manifest:
// Chrome content-script `matches` patterns IGNORE the query string, so
// "https://app.uplane.com/concepts/*?editor=*" is not expressible there. The
// manifest therefore matches the whole origin and the real gate is this file.
// Before this, init() ran on every Uplane page: the capture UI mounted on the
// concepts list and a baseline screenshot fired 1.2s after every load.
//
// Uplane is a single-page app, so ?editor= appears and disappears without a
// document load. Polling the URL is the only reliable signal — pushState and
// replaceState do not emit an event, and popstate misses programmatic routing.
(function () {
  if (window.__uplaneEditCaptureGate) return;   // see capture-core.js
  window.__uplaneEditCaptureGate = true;
  const OPTS = { endpoint: "http://127.0.0.1:8791/edit-session", by: "sun" };

  // the editor is identified by an `editor` query param carrying an id:
  //   app.uplane.com/concepts/<concept-id>?editor=<editor-id>
  const inEditor = () => /[?&]editor=[^&]+/.test(location.search);

  let started = false;
  let reported = false;
  let lastHref = "";

  function sync() {
    try {
      if (location.href === lastHref) return;
      lastHref = location.href;
      const want = inEditor();
      if (want && !started) {
        started = true;
        window.__uplaneEditCapture && window.__uplaneEditCapture.init(OPTS);
      } else if (!want && started) {
        // leaving the editor tears everything down, so nothing lingers on the
        // concepts list or the share page
        started = false;
        window.__uplaneEditCapture && window.__uplaneEditCapture.teardown();
      }
    } catch (e) {
      // Never break the host page — but never go silent either. This catch hid a
      // ReferenceError for three test runs while the pill simply failed to appear.
      if (!reported) {
        reported = true;
        console.warn("[uplane-edit-capture] failed to start:",
                     (e && e.stack) || String(e));
      }
    }
  }

  sync();
  setInterval(sync, 700);
  addEventListener("popstate", sync);
  addEventListener("hashchange", sync);
})();
