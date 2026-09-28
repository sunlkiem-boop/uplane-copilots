// Service worker: screenshots the visible tab when the content script asks.
// Used whenever the creative is not a readable canvas — in the Uplane editor it is a
// composition of separate DOM elements, so this is the normal path, not the fallback.
//
// captureVisibleTab accepts ONLY <all_urls> or activeTab; a site-specific host
// permission is refused ("Either the '<all_urls>' or 'activeTab' permission is
// required"), verified A/B against a real loaded extension. activeTab needs a gesture
// on the extension action, which the automatic before-frame does not have. So the
// manifest grants <all_urls> and the URL check below is what actually limits scope.
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === "capture") {
    // <all_urls> is required by captureVisibleTab (a site-specific host permission is
    // refused outright), so the narrowing lives here instead: only ever screenshot an
    // Uplane editor tab, and only the tab that actually asked.
    const from = sender && sender.tab && sender.tab.url ? sender.tab.url : "";
    if (!/^https:\/\/app\.uplane\.com\/.*[?&]editor=[^&]+/.test(from)) {
      sendResponse({ dataURL: null, err: "refused: not an Uplane editor tab" });
      return true;
    }
    try {
      chrome.tabs.captureVisibleTab(
        sender.tab.windowId,
        { format: "png" },
        async (dataURL) => {
          const err = chrome.runtime.lastError ? chrome.runtime.lastError.message : null;
          if (dataURL && !err) return sendResponse({ dataURL, err: null });
          // Chrome's own message ("Either the '<all_urls>' or 'activeTab' permission is
          // required") does not say WHICH of the two possible causes it is, and both
          // have different fixes. Ask the runtime what it was actually granted.
          let why = "";
          try {
            const granted = await chrome.permissions.contains({ origins: ["<all_urls>"] });
            why = granted
              ? " — all-sites access IS granted to this build, so Chrome is withholding it:"
                + " open chrome://extensions, Uplane Edit Capture -> Details -> Site access,"
                + " and set it to 'On all sites'"
              : " — this loaded build was NOT granted all-sites access, which means Chrome"
                + " has not re-read the manifest: open chrome://extensions and click the"
                + " reload arrow on Uplane Edit Capture, then reload the editor tab";
          } catch (e) { /* permissions API unavailable: fall through with Chrome's text */ }
          sendResponse({ dataURL: null, err: (err || "no image returned") + why });
        });
      return true;
    } catch (e) {
      sendResponse({ dataURL: null, err: String(e) });
    }
  }
});

// Reloading an extension does NOT inject content scripts into tabs that are already
// open — Chrome only injects at page load. So after every reload the editor tab keeps
// running the PREVIOUS build (or nothing at all, if that build threw), and the fix
// looks like it did not work. That cost a full round-trip to diagnose on 2026-08-24.
//
// So re-inject into any open editor tab whenever this worker starts. The scripts guard
// against double-injection themselves, so a tab that already has them is untouched.
async function adoptOpenTabs(reason) {
  try {
    const tabs = await chrome.tabs.query({ url: "https://app.uplane.com/*" });
    for (const t of tabs) {
      if (!t.id) continue;
      try {
        await chrome.scripting.executeScript({
          target: { tabId: t.id },
          files: ["capture-core.js", "content.js"],
        });
      } catch (e) { /* restricted tab, or the page went away */ }
    }
    if (tabs.length) console.log(`[uplane-edit-capture] adopted ${tabs.length} open tab(s) on ${reason}`);
  } catch (e) { /* scripting unavailable */ }
}

chrome.runtime.onInstalled.addListener(() => adoptOpenTabs("install/reload"));
chrome.runtime.onStartup.addListener(() => adoptOpenTabs("browser startup"));
adoptOpenTabs("worker start");
