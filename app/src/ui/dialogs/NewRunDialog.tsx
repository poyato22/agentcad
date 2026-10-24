import type { JSX } from "preact";
import { actions, newRunOpen } from "../../state/store";

// STUB (Task 2). Task 9 replaces this file. Mounted once at the app root; open/close state is
// the store's `newRunOpen` signal (actions.openNewRun / actions.closeNewRun).
export function NewRunDialog(): JSX.Element | null {
  if (!newRunOpen.value) return null;
  return (
    <div data-stub="NewRunDialog" role="dialog" aria-modal="true" aria-label="New run"
      style={{ position: "fixed", inset: "0", display: "grid", placeItems: "center", background: "rgba(0,0,0,.6)" }}>
      <div data-card style={{ background: "var(--paper2)", padding: "12px", minWidth: "360px" }}>
        <h2 class="pane-title">New run</h2>
        <button type="button" class="btn" onClick={() => actions.closeNewRun()}>Close</button>
      </div>
    </div>
  );
}
