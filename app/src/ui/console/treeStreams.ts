// The View and Simulations streams over the whole build (Plan 9 D4, coordinator ruling): every
// run of the dock's tree (the lead, its sub-agents, the shown run's tree) contributes its views and
// studies, merged by time. Each entry keeps its own run (its card names that agent). A card the
// core mirrors onto the lead (announce.announce stores an announcement on the acting run AND on
// its lead) is shown once, from the run that made it. Pure.
import type { AgentRun } from "../../api/types";
import { buildLog, type BuildLogInput } from "./logModel";
import { splitStreams } from "./streams";
import { buildThread, type ThreadItem } from "./threadModel";

export interface DockEntry {
  /** Unique across runs: "<run id>/<thread key>". */
  key: string;
  item: ThreadItem;
  runId: string;
  agent: string;
  /** When it happened (s); Infinity for a call still waiting for its first step. */
  order: number;
}

export interface TreeStreams { view: DockEntry[]; sim: DockEntry[] }

/** The run that made a card (the actor of a mirrored announcement), else the thread's run. */
function actorOf(item: ThreadItem, runId: string): string {
  switch (item.kind) {
    case "view": return item.view.runId || runId;
    case "critique": return item.round.runId || runId;
    case "announce": return item.announcement.actor ?? runId;
    default: return runId;
  }
}

export function treeStreams(input: BuildLogInput, tree: readonly AgentRun[]): TreeStreams {
  const ids = new Set(tree.map((r) => r.id));
  const out: TreeStreams = { view: [], sim: [] };
  for (const run of tree) {
    const steps = input.steps.get(run.id) ?? [];
    const tsOf = new Map(steps.map((s) => [s.id, s.ts]));
    const items = buildThread(buildLog({ ...input, filter: { scope: "run", kind: "everything" }, selectedRunId: run.id }));
    const s = splitStreams(items);
    for (const kind of ["view", "sim"] as const) {
      for (const item of s[kind]) {
        const actor = actorOf(item, run.id);
        if (actor !== run.id && ids.has(actor)) continue;   // shown from the run that made it
        const at = "stepIds" in item ? Math.max(-Infinity, ...item.stepIds.map((id) => tsOf.get(id) ?? -Infinity)) : -Infinity;
        const own = "ts" in item && typeof item.ts === "number" ? item.ts : -Infinity;
        const order = Math.max(at, own);
        out[kind].push({ key: `${run.id}/${item.key}`, item, runId: run.id, agent: run.agent, order: order === -Infinity ? Infinity : order });
      }
    }
  }
  for (const kind of ["view", "sim"] as const) out[kind].sort((a, b) => (a.order === b.order ? 0 : a.order - b.order));   // stable; Infinity last
  return out;
}
