// What the composer's Stop stops (Plan 9 D2): every busy run of the dock's tree — the build's lead
// and its sub-agents, and the run the console shows with its sub-agents when that run sits outside
// the lead's tree. The send button turns into Stop while this list is not empty; Esc in the
// composer and /stop do the same (Esc spares a run that waits on your approval or answer).
import { computed, type ReadonlySignal } from "@preact/signals";
import type { AgentActivity, AgentRun } from "../../api/types";
import { debug } from "../../lib/log";
import { activityByRun, runBusy } from "../../state/activity";
import { effectiveRun } from "../../state/selection";
import { actions } from "../../state/store";
import { buildRuns } from "./derived";
import { shownPending } from "./queued";
import { dockTree } from "./runTree";

/** The busy runs of the dock's tree (lead first), pure for tests. */
export function stopTargets(list: readonly AgentRun[], shown: AgentRun | null, activity: ReadonlyMap<string, AgentActivity>): AgentRun[] {
  return dockTree(list, shown).filter((r) => runBusy(r, activity.get(r.id)));
}

/** Is the run waiting on you (an approval or a question)? Esc never cancels such a run: you decide
 *  the card; the Stop button still can. */
export function waitingOnYou(a: AgentActivity | undefined): boolean {
  return a?.state === "waiting_approval" || a?.state === "waiting_user";
}

export const busyRuns: ReadonlySignal<readonly AgentRun[]> = computed(() =>
  stopTargets(buildRuns.value, effectiveRun.value ?? shownPending.value, activityByRun.value));

/** Stop every busy run of the tree, each explicitly (the core cancels only the run asked, not its
 *  children). `fromEsc`: leave runs that wait on you alone. */
export async function stopBusy(fromEsc = false): Promise<void> {
  const act = activityByRun.peek();
  const targets = busyRuns.peek().filter((r) => !fromEsc || !waitingOnYou(act.get(r.id)));
  await Promise.all(targets.map(async (r) => {
    try { await actions.client().cancel(r.id); } catch (e) { debug("cancel failed", r.id, e); }
  }));
}
