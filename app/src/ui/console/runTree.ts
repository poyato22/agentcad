// The build's agent tree as the dock sees it (Plan 9 D2 / D4): the live lead with its sub-agents
// (at any depth), plus the run the console shows and its own sub-agents when that run sits outside
// the lead's tree (a run started on its own). The View and Simulations streams aggregate it, and
// the composer's Stop stops its busy runs. Pure.
import type { AgentRun } from "../../api/types";

/** `root` and every run below it, root first. */
export function subtree(list: readonly AgentRun[], root: AgentRun): AgentRun[] {
  const out: AgentRun[] = [root];
  const ids = new Set([root.id]);
  // sub-agents of sub-agents too (a list is small: repeat until nothing new joins)
  for (let grew = true; grew;) {
    grew = false;
    for (const r of list) {
      if (r.parent_id && ids.has(r.parent_id) && !ids.has(r.id)) { out.push(r); ids.add(r.id); grew = true; }
    }
  }
  return out;
}

/** The live lead's tree, then the shown run's tree (de-duplicated). */
export function dockTree(list: readonly AgentRun[], shown: AgentRun | null): AgentRun[] {
  const lead = list.find((r) => r.role === "lead" && !r.archived) ?? null;
  const out: AgentRun[] = [];
  const seen = new Set<string>();
  for (const root of [lead, shown]) {
    if (!root || seen.has(root.id)) continue;
    for (const r of subtree(list.some((x) => x.id === root.id) ? list : [root, ...list], root)) {
      if (!seen.has(r.id)) { seen.add(r.id); out.push(r); }
    }
  }
  return out;
}
