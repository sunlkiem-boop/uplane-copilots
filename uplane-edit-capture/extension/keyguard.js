// Runs at document_start, BEFORE any page script — that ordering is the whole point.
// The editor binds single-key tool shortcuts (T = text, C = comment) on window
// capture; a listener registered by the page first would see every key typed into
// our panel and switch tools while the user types ("client" = two tool switches).
// Being first lets stopImmediatePropagation() cut the page out entirely, while the
// browser's default action (the actual text insertion) still runs.
//
// For tabs ADOPTED after an extension reload the page's listeners are already ahead
// of us — there, capture-core.js's defaultPrevented-restore fallback covers typing,
// though tool side-effects can leak until the tab is reloaded.
(() => {
  if (window.__uplaneKeyGuard) return;
  window.__uplaneKeyGuard = true;
  const guard = (e) => {
    const path = e.composedPath ? e.composedPath() : [];
    for (const el of path) {
      if (el && el.id === "uplane-edit-capture") { e.stopImmediatePropagation(); return; }
    }
  };
  for (const k of ["keydown", "keyup", "keypress"]) {
    window.addEventListener(k, guard, true);
  }
})();
