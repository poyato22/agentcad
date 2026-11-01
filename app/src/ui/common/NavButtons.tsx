// ← / → (Plan 10 E11): step back and forward through where the user went (state/nav). In the build's
// top bar beside ⌂, and in Home's header; ⌘← / ⌘→ and the mouse's side buttons do the same.
import type { JSX } from "preact";
import { canGoBack, canGoForward, goBack, goForward } from "../../state/nav";
import "./navButtons.css";

function Chevron({ dir }: { dir: "left" | "right" }): JSX.Element {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d={dir === "left" ? "M10 3.5 5.5 8l4.5 4.5" : "M6 3.5 10.5 8 6 12.5"} fill="none" stroke="currentColor"
        stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
    </svg>
  );
}

export function NavButtons(): JSX.Element {
  return (
    <span class="nav-btns" role="group" aria-label="History">
      <button type="button" class="nav-btn" aria-label="Back" title="Back (⌘←)" disabled={!canGoBack.value}
        onClick={() => goBack()}><Chevron dir="left" /></button>
      <button type="button" class="nav-btn" aria-label="Forward" title="Forward (⌘→)" disabled={!canGoForward.value}
        onClick={() => goForward()}><Chevron dir="right" /></button>
    </span>
  );
}
