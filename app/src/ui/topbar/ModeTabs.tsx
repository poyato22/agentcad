import type { JSX } from "preact";
import { useRef } from "preact/hooks";
import { atHome } from "../../state/home";
import { activePage, setPage, TAB_PAGES } from "../pages/router";

/** The top mode tabs. Every tab is clickable and navigates (⌘1–⌘7 do the same); the active tab is
 *  underlined in blue. Keyboard: one tab stop (roving tabindex); ←/→ move to the previous/next tab
 *  (wrapping), Home/End to the first/last, and the focused tab opens. */
export function ModeTabs(): JSX.Element {
  const current = activePage.value;
  const list = useRef<HTMLDivElement>(null);

  function onKeyDown(e: KeyboardEvent): void {
    const i = TAB_PAGES.findIndex((p) => p.id === current);
    let next: number;
    if (e.key === "ArrowRight") next = (i + 1) % TAB_PAGES.length;
    else if (e.key === "ArrowLeft") next = (i - 1 + TAB_PAGES.length) % TAB_PAGES.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = TAB_PAGES.length - 1;
    else return;
    e.preventDefault();
    setPage(TAB_PAGES[next].id);
    list.current?.querySelectorAll<HTMLButtonElement>("[role=tab]")[next]?.focus();
  }

  return (
    <div ref={list} class="tabs" role="tablist" aria-label="mode" onKeyDown={onKeyDown}>
      {TAB_PAGES.map((p) => {
        // m1: on Home no page tab reads as the one shown (the roving tab stop stays on it)
        const current_ = p.id === current;
        const active = current_ && !atHome.value;
        return (
          <button
            key={p.id}
            type="button"
            role="tab"
            class={active ? "tab tab-active active-tab" : "tab"}
            aria-selected={active}
            title={`${p.label} (⌘${p.key})`}
            tabIndex={current_ ? 0 : -1}
            onClick={() => setPage(p.id)}
          >{p.label}</button>
        );
      })}
    </div>
  );
}
