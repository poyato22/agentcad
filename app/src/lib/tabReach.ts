// Keyboard reach in WebKit (I7). The app runs in WKWebView, which — with macOS's default
// "Keyboard navigation" setting off (Playwright WebKit behaves the same) — puts only text fields
// and elements with an explicit tabindex in the Tab order: every <button>, link, select, checkbox
// and slider is skipped, so Tab never reaches Home, the part list, View ▾, the console header or
// the Agents panel. Chromium tabs to all of them.
//
// The shared Button component sets tabIndex={0} itself. For every other native control this
// gives it the tabindex="0" WebKit needs, once, when it appears: an element that already has a
// tabindex (a roving -1, a deliberate 0) is left alone, so component-owned focus order wins.
// Chromium's order is unchanged (tabindex 0 is where those controls already sit).

/** Native controls WebKit skips on Tab unless they carry a tabindex. Text fields are reachable. */
export const TAB_REACH_SELECTOR = [
  "button", "a[href]", "select", "summary",
  'input[type="checkbox"]', 'input[type="radio"]', 'input[type="range"]', 'input[type="color"]', 'input[type="file"]:not([hidden])',
].join(", ");

/** Give each matching control without a tabindex an explicit tabindex="0". */
export function reachAll(root: ParentNode): void {
  const list: Element[] = [];
  if (root instanceof Element && root.matches(TAB_REACH_SELECTOR)) list.push(root);
  list.push(...root.querySelectorAll(TAB_REACH_SELECTOR));
  for (const el of list) if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "0");
}

/** Keep every control under `root` Tab-reachable as the UI renders. Returns a stop function. */
export function installTabReach(root: HTMLElement = document.body): () => void {
  reachAll(root);
  if (typeof MutationObserver === "undefined") return () => {};
  const mo = new MutationObserver((records) => {
    for (const r of records) {
      if (r.type === "childList") {
        for (const n of r.addedNodes) if (n instanceof Element) reachAll(n);
      } else if (r.type === "attributes" && r.target instanceof Element) {
        // a component dropped its tabindex (e.g. a roving item leaving the order): back to 0 —
        // unless it is a control that should not be reachable at all
        const el = r.target;
        if (!el.hasAttribute("tabindex") && el.matches(TAB_REACH_SELECTOR)) el.setAttribute("tabindex", "0");
      }
    }
  });
  mo.observe(root, { childList: true, subtree: true, attributes: true, attributeFilter: ["tabindex"] });
  return () => mo.disconnect();
}
