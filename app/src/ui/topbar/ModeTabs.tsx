import type { JSX } from "preact";
import { activePage, PAGES, setPage } from "../pages/router";

/** The top mode tabs. Every tab is clickable and navigates (⌘1–⌘5 do the same); the active tab is
 *  underlined in blue. */
export function ModeTabs(): JSX.Element {
  const current = activePage.value;
  return (
    <div class="tabs" role="tablist" aria-label="mode">
      {PAGES.map((p) => {
        const active = p.id === current;
        return (
          <button
            key={p.id}
            type="button"
            role="tab"
            class={active ? "tab tab-active active-tab" : "tab"}
            aria-selected={active}
            title={`${p.label} (⌘${p.key})`}
            tabIndex={active ? 0 : -1}
            onClick={() => setPage(p.id)}
          >{p.label}</button>
        );
      })}
    </div>
  );
}
