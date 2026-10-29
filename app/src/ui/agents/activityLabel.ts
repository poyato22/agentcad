// Plain words for an activity state, shared by the agent manager, the thread status line and the
// run switcher.
import type { ActivityState } from "../../api/types";

const LABELS: Record<ActivityState, string> = {
  queued: "waiting for a free slot",
  starting: "starting",
  thinking: "thinking",
  writing: "writing",
  tool: "working",
  waiting_approval: "waiting for your approval",
  waiting_user: "waiting for your answer",
  idle: "idle",
  done: "done",
};

export function activityLabel(state: ActivityState): string { return LABELS[state]; }

/** States where the run needs the human (rendered with the blue needs-you treatment). */
export function isWaiting(state: ActivityState | null | undefined): boolean {
  return state === "waiting_approval" || state === "waiting_user";
}
