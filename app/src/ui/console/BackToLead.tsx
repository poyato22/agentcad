// Plan 10 E13: a sub-agent's thread opened in the Conversation (its card's "Open agent", the agent
// manager, a "needs you" count) always shows the way back: "← Back to lead" in the Conversation
// header, and Esc (focus in the dock, not typing) does the same. ← / ⌘← go back too (state/nav).
import type { JSX } from "preact";
import { useEffect, useLayoutEffect, useRef } from "preact/hooks";
import { selectBuildLead } from "../../state/builds";
import { runs, selectedRunId } from "../../state/store";
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

/** Whether the build has a lead to go back to. */
function hasLead(buildId: string | null): boolean {
  return buildId !== null && [...runs.value.values()].some((r) => r.project_id === buildId && r.role === "lead" && !r.archived);
}

export function BackToLead({ buildId }: { buildId: string | null }): JSX.Element | null {
  const lead = hasLead(buildId);
  // a layout effect: Esc works from the frame the button shows in
  useLayoutEffect(() => {
    if (!lead) return undefined;
    const inDock = (t: EventTarget | null) => !!(t as HTMLElement | null)?.closest?.("[data-dock]");
    // the opener may leave with the lead's thread, dropping the focus to the page: then Esc still
    // belongs to the dock when the dock was the last thing pressed
    let lastPressInDock = true;
    const onPress = (e: PointerEvent) => { lastPressInDock = inDock(e.target); };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented || typing(e.target) || modalOpen()) return;
      const lost = e.target === document.body || e.target === document.documentElement;
      if (!inDock(e.target) && !(lost && lastPressInDock)) return;
      e.preventDefault();
      backToLead(buildId);
    };
    document.addEventListener("pointerdown", onPress, true);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("pointerdown", onPress, true); document.removeEventListener("keydown", onKey); };
  }, [buildId, lead]);
  // the opener ("Open agent" on the lead's card) leaves with the lead's thread: focus lands here
  // instead of on nothing, so Esc (and Enter) go straight back
  const btn = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const a = document.activeElement;
    if (lead && (a === null || a === document.body)) btn.current?.focus();
  }, [lead]);
  if (!lead) return null;
  return (
    <button type="button" ref={btn} class="dock-back" data-back-to-lead title="Back to the lead's conversation (Esc)"
      onClick={() => backToLead(buildId)}>
      <span aria-hidden="true">←</span> Back to lead
    </button>
  );
}
