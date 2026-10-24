import type { JSX } from "preact";

const TABS = ["Design", "Studies", "Sketch", "Drawing", "Print"] as const;

/** Only Design exists in v1; the others are shown disabled. */
export function ModeTabs(): JSX.Element {
  return (
    <div class="tabs" role="tablist" aria-label="mode">
      {TABS.map((t) => {
        const active = t === "Design";
        return (
          <button
            key={t}
            type="button"
            role="tab"
            class={active ? "tab tab-active active-tab" : "tab"}
            aria-selected={active}
            aria-disabled={active ? undefined : "true"}
            title={active ? undefined : "later"}
            tabIndex={active ? 0 : -1}
            onClick={(e) => { if (!active) e.preventDefault(); }}
          >{t}</button>
        );
      })}
    </div>
  );
}
