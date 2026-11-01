// Plan 10 E13: a sub-agent's thread opened in the Conversation (its card's "Open agent", the agent
// manager, a "needs you" count) always shows the way back: "← Back to lead" in the Conversation
// header, and Esc (focus in the dock, not typing) does the same. ← / ⌘← go back too (state/nav).
import type { JSX } from "preact";
import { useEffect } from "preact/hooks";
import { selectBuildLead } from "../../state/builds";
import { selectedRunId } from "../../state/store";
import { modalOpen } from "../pages/router";
import "./backToLead.css";

/** Show the build's lead thread again (none: the dock's default pick). */
export function backToLead(buildId: string | null): void {
  const before = selectedRunId.peek();
  if (buildId) selectBuildLead(buildId);
  if (selectedRunId.peek() === before) selectedRunId.value = null;
}

function typing(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable === true);
}

export function BackToLead({ buildId }: { buildId: string | null }): JSX.Element {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented || typing(e.target) || modalOpen()) return;
      const t = e.target as HTMLElement | null;
      if (!t?.closest?.("[data-dock]")) return;
      e.preventDefault();
      backToLead(buildId);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [buildId]);
  return (
    <button type="button" class="dock-back" data-back-to-lead title="Back to the lead's conversation (Esc)"
      onClick={() => backToLead(buildId)}>
      <span aria-hidden="true">←</span> Back to lead
    </button>
  );
}
