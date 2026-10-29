// Top-bar activity pill: a live count of working agents with an animated state dot
// ("2 working · 1 thinking"). Clicking it opens the agent manager (Task A2). Hidden when nothing
// is working or waiting.
import type { JSX } from "preact";
import { activitySummary } from "../../state/activity";
import { openAgentManager } from "../agents/manager";

export function ActivityPill(): JSX.Element | null {
  const s = activitySummary.value;
  if (!s.label) return null;
  const mode = s.working > 0 ? (s.thinking > 0 ? "thinking" : "working") : "waiting";
  return (
    <button type="button" class="activity-pill" data-mode={mode}
      title="Open the agent manager" aria-label={`${s.label}. Open the agent manager.`}
      onClick={() => openAgentManager("runs")}>
      <span class="activity-dot" aria-hidden="true" />
      <span class="activity-label">{s.label}</span>
    </button>
  );
}
