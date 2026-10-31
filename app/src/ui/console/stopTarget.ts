// What the composer's Stop stops (Plan 9 D2): the build's lead and its sub-agents, whichever of
// them is busy. With no live lead (an older core, a run started on its own) it is the run the
// console shows and its sub-agents. The send button turns into Stop while this list is not empty;
// Esc in the composer and /stop do the same.
import { computed, type ReadonlySignal } from "@preact/signals";
import type { AgentActivity, AgentRun } from "../../api/types";
import { debug } from "../../lib/log";
import { activityByRun, runBusy } from "../../state/activity";
import { effectiveRun } from "../../state/selection";
import { actions } from "../../state/store";
import { buildRuns } from "./derived";
import { shownPending } from "./queued";

/** The busy runs of the lead's tree (lead first), pure for tests. */
export function stopTargets(list: readonly AgentRun[], shown: AgentRun | null, activity: ReadonlyMap<string, AgentActivity>): AgentRun[] {
  const root = list.find((r) => r.role === "lead" && !r.archived) ?? shown;
  if (!root) return [];
  const tree: AgentRun[] = [root];
  const ids = new Set([root.id]);
  // sub-agents of sub-agents too (a list is small: repeat until nothing new joins)
  for (let grew = true; grew;) {
    grew = false;
    for (const r of list) {
      if (r.parent_id && ids.has(r.parent_id) && !ids.has(r.id)) { tree.push(r); ids.add(r.id); grew = true; }
    }
  }
  return tree.filter((r) => runBusy(r, activity.get(r.id)));
}

export const busyRuns: ReadonlySignal<readonly AgentRun[]> = computed(() =>
  stopTargets(buildRuns.value, effectiveRun.value ?? shownPending.value, activityByRun.value));

/** Stop every busy run of the tree (the core also stops a lead's children; asking twice is harmless). */
export async function stopBusy(): Promise<void> {
  const targets = busyRuns.peek();
  await Promise.all(targets.map(async (r) => {
    try { await actions.client().cancel(r.id); } catch (e) { debug("cancel failed", r.id, e); }
  }));
}
