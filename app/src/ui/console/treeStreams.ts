// The View and Simulations streams over the whole build (Plan 9 D4, coordinator ruling): every
// run of the dock's tree (the lead, its sub-agents, the shown run's tree) contributes its views and
// studies, merged by time. Each entry keeps its own run (its card names that agent). A card the
// core mirrors onto the lead (announce.announce stores an announcement on the acting run AND on
// its lead) is shown once, from the run that made it.
//
// Fix round 2 (N1, lag): views and studies depend only on a run's own steps and its in-flight
// calls — never on streamed text, approvals or questions (those always stay in the Conversation).
// So each run's entries are cached, keyed by the identity of its step array (the store replaces
// only the changed run's array) and its in-flight calls; a token, or a step on another run,
// rebuilds nothing here, and unchanged entries stay the very same objects (Preact skips them).
import type { AgentRun, Step } from "../../api/types";
import type { InflightCall } from "../../state/reduce";
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

/** What the aggregation reads: per-run steps and in-flight calls, nothing else. */
export interface TreeInput {
  runs: readonly AgentRun[];
  steps: ReadonlyMap<string, readonly Step[]>;
  inflight?: ReadonlyMap<string, InflightCall>;
}

/** The run that made a card (the actor of a mirrored announcement), else the thread's run. */
function actorOf(item: ThreadItem, runId: string): string {
  switch (item.kind) {
    case "view": return item.view.runId || runId;
    case "critique": return item.round.runId || runId;
    case "announce": return item.announcement.actor ?? runId;
    default: return runId;
  }
}

const NO_STEPS: readonly Step[] = [];
const EMPTY = new Map<string, string>();

interface RunCache {
  steps: readonly Step[];
  inflightKey: string;
  treeKey: string;
  agent: string;
  out: TreeStreams;
}

/** One run's views and studies (the expensive part: its own thread, from its own steps only). */
function runEntries(input: TreeInput, run: AgentRun, steps: readonly Step[], inflight: Map<string, InflightCall>, ids: ReadonlySet<string>): TreeStreams {
  const tsOf = new Map(steps.map((s) => [s.id, s.ts]));
  const one: BuildLogInput = {
    runs: input.runs, steps: new Map([[run.id, steps]]), approvals: [], questions: [], liveText: EMPTY,
    filter: { scope: "run", kind: "everything" }, selectedRunId: run.id, expandedFolds: new Set(), inflight,
  };
  const s = splitStreams(buildThread(buildLog(one)));
  const out: TreeStreams = { view: [], sim: [] };
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
  return out;
}

const sameList = <T>(a: readonly T[], b: readonly T[]) => a.length === b.length && a.every((x, i) => x === b[i]);

/** A cached aggregation: call it every frame; only runs whose steps or in-flight calls changed
 *  are rebuilt, and when nothing changed the previous result itself comes back. `stats.rebuilt`
 *  counts the runs rebuilt by the last call (tests). */
export function createTreeStreams(): ((input: TreeInput, tree: readonly AgentRun[]) => TreeStreams) & { stats: { rebuilt: number } } {
  const cache = new Map<string, RunCache>();
  let last: TreeStreams = { view: [], sim: [] };
  const stats = { rebuilt: 0 };
  const fn = (input: TreeInput, tree: readonly AgentRun[]): TreeStreams => {
    stats.rebuilt = 0;
    const ids = new Set(tree.map((r) => r.id));
    const treeKey = tree.map((r) => r.id).join(",");
    const byRun = new Map<string, Map<string, InflightCall>>();
    for (const [cid, c] of input.inflight ?? []) {
      if (!ids.has(c.run_id)) continue;
      let m = byRun.get(c.run_id);
      if (!m) { m = new Map(); byRun.set(c.run_id, m); }
      m.set(cid, c);
    }
    const parts: TreeStreams[] = [];
    for (const run of tree) {
      const steps = input.steps.get(run.id) ?? NO_STEPS;
      const inflight = byRun.get(run.id) ?? new Map<string, InflightCall>();
      const inflightKey = [...inflight.keys()].join(",");
      const hit = cache.get(run.id);
      if (hit && hit.steps === steps && hit.inflightKey === inflightKey && hit.treeKey === treeKey && hit.agent === run.agent) {
        parts.push(hit.out);
        continue;
      }
      stats.rebuilt++;
      const out = runEntries(input, run, steps, inflight, ids);
      cache.set(run.id, { steps, inflightKey, treeKey, agent: run.agent, out });
      parts.push(out);
    }
    for (const id of [...cache.keys()]) if (!ids.has(id)) cache.delete(id);
    const next: TreeStreams = { view: [], sim: [] };
    for (const kind of ["view", "sim"] as const) {
      for (const p of parts) next[kind].push(...p[kind]);
      next[kind].sort((a, b) => (a.order === b.order ? 0 : a.order - b.order));   // stable; Infinity last
    }
    // nothing moved: the same lists, so the dock's streams see no change at all
    if (sameList(next.view, last.view) && sameList(next.sim, last.sim)) return last;
    last = {
      view: sameList(next.view, last.view) ? last.view : next.view,
      sim: sameList(next.sim, last.sim) ? last.sim : next.sim,
    };
    return last;
  };
  return Object.assign(fn, { stats });
}

/** Uncached, for one-off use and tests. */
export function treeStreams(input: TreeInput, tree: readonly AgentRun[]): TreeStreams {
  return createTreeStreams()(input, tree);
}
