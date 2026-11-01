// Top-bar activity pill: a live count of working agents with an animated state dot
// ("2 working · 1 thinking"). Clicking it opens the agent manager (Task A2), and clicking it again
// closes it (Plan 10 E7: the top bar sits above the manager's scrim). Hidden when nothing is
// working or waiting.
import type { JSX } from "preact";
import { activitySummary } from "../../state/activity";
import { agentManagerOpen, closeAgentManager, openAgentManager } from "../agents/manager";
import { toggleOpen } from "../common/toggle";

export function ActivityPill(): JSX.Element | null {
  const s = activitySummary.value;
  const open = agentManagerOpen.value;
  if (!s.label) return null;
  const mode = s.working > 0 ? (s.thinking > 0 ? "thinking" : "working") : "waiting";
  const verb = open ? "Close" : "Open";
  return (
    <button type="button" class="activity-pill" data-mode={mode} aria-expanded={open}
      title={`${verb} the agent manager`} aria-label={`${s.label}. ${verb} the agent manager.`}
      onClick={() => toggleOpen(agentManagerOpen.peek(), () => openAgentManager("agents"), closeAgentManager)}>
      <span class="activity-dot" aria-hidden="true" />
      <span class="activity-label">{s.label}</span>
    </button>
  );
}
