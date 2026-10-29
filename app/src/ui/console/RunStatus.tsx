// The live status line under a run's thread, from `agent_activity`: a pulsing dot while thinking,
// the tool label with elapsed seconds while working, a streaming caret while writing, and the blue
// needs-you treatment while it waits for you. "Show thinking" reveals the model's thinking text
// (`agent_thinking`, display-only) in faint type. Nothing shows once the run is idle or done.
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import type { AgentActivity } from "../../api/types";
import { activityByRun } from "../../state/activity";
import { thinkingByRun } from "../../state/thinking";
import { activityLabel, isWaiting } from "../agents/activityLabel";

/** Whole seconds since `since` (unix seconds), never negative. */
export function elapsedSeconds(since: number, nowMs: number = Date.now()): number {
  return Math.max(0, Math.floor(nowMs / 1000 - since));
}

/** The status line's text for one activity snapshot. */
export function statusText(a: AgentActivity, nowMs: number = Date.now()): string {
  switch (a.state) {
    case "tool": return `${a.detail || "working"} · ${elapsedSeconds(a.since, nowMs)} s`;
    case "thinking": return a.detail ? `thinking · ${a.detail}` : "thinking";
    case "writing": return "writing";
    case "waiting_approval": return "waiting for your approval — decide the card above";
    case "waiting_user": return "waiting for your answer — see the question above";
    default: return a.detail ? `${activityLabel(a.state)} · ${a.detail}` : activityLabel(a.state);
  }
}

export function RunStatus({ runId }: { runId: string }): JSX.Element | null {
  const a = activityByRun.value.get(runId) ?? null;
  const thinking = thinkingByRun.value.get(runId) ?? "";
  const [open, setOpen] = useState(false);
  const [, tick] = useState(0);

  // the tool line counts seconds: re-render once a second while a tool runs
  useEffect(() => {
    if (a?.state !== "tool") return;
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [a?.state, a?.since]);

  if (!a) return null;
  const waiting = isWaiting(a.state);
  return (
    <div class={`cv-status${waiting ? " needs-you" : ""}`} data-run-status={a.state} role="status" aria-live="polite">
      <div class="cv-status-line">
        <span class="cv-status-mark" data-state={a.state} aria-hidden="true">
          {a.state === "writing" ? "▍" : waiting ? "▲" : "●"}
        </span>
        <span class="text" data-status-text>{statusText(a)}</span>
        {thinking !== "" && (
          <button type="button" class="cv-thinking-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? "Hide thinking" : "Show thinking"}
          </button>
        )}
      </div>
      {open && thinking !== "" && <pre class="cv-thinking faint text" data-thinking>{thinking}</pre>}
    </div>
  );
}
