import type { JSX } from "preact";

// STUB (Task 2). Task 9 replaces this file: "❯" prompt, routing, slash commands, history.
export function CommandLine(): JSX.Element {
  return (
    <div data-stub="CommandLine" style={{ display: "flex", alignItems: "center", gap: "8px", padding: "7px 8px", borderTop: "1px solid var(--line2)", background: "var(--head)" }}>
      <span class="faint">❯</span>
      <input aria-label="Command line" placeholder="message an agent, @all, or / for commands…" disabled style={{ flex: "1" }} />
    </div>
  );
}
