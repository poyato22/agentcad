import type { JSX } from "preact";

// STUB (Task 2). Task 4 replaces this file: breadcrumb · mode tabs · status lights · STOP ALL.
export function TopBar(): JSX.Element {
  return (
    <header class="topbar-stub" data-stub="TopBar" style={{ height: "100%", display: "flex", alignItems: "center", padding: "0 16px 0 72px", background: "var(--head)", borderBottom: "1px solid var(--line2)" }}>
      <span class="pane-title">Mises</span>
    </header>
  );
}
