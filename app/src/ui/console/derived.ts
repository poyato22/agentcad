// Console-local derived state (computed from the store; the store itself is not changed here).
// The log is rebuilt at most once per animation frame: one effect watches the store inputs and
// schedules a single rebuild into `logFrame`, which StepLog and Filters render from.
import { activeBuildId } from "../../state/builds";
import { computed, effect, signal, type ReadonlySignal } from "@preact/signals";
import type { AgentRun } from "../../api/types";
import { runLabel } from "../../lib/names";
import { effectiveRun, effectiveRunId, rosterOrder } from "../../state/selection";
import { actions, approvals, filter, inflight, liveText, questions, runs, steps } from "../../state/store";
import { buildLog, buildLogFrame, type BuildLogInput, type LogFrame } from "./logModel";
import { buildThread, type ThreadItem } from "./threadModel";
import { shownPending } from "./queued";
import { dockTree } from "./runTree";
import { treeStreams, type TreeStreams } from "./treeStreams";

/** Folds the user opened, by foldKey. */
export const expandedFolds = signal<ReadonlySet<string>>(new Set());

export function toggleFold(key: string): void {
  const next = new Set(expandedFolds.value);
  if (!next.delete(key)) next.add(key);
  expandedFolds.value = next;
}

export const runList: ReadonlySignal<readonly AgentRun[]> = computed(() => [...runs.value.values()]);

/** Roster order: running first, then newest first. */
export const rosterRuns: ReadonlySignal<readonly AgentRun[]> = computed(() => rosterOrder(runList.value));

/** The run switcher's list: the active build's runs (A3's build tabs). Runs with no build (an
 *  older core) stay listed; a run elsewhere that needs you is still offered by the switcher. */
export const buildRuns: ReadonlySignal<readonly AgentRun[]> = computed(() => {
  const id = activeBuildId.value;
  return id === null ? rosterRuns.value : rosterRuns.value.filter((r) => (r.project_id ?? null) === null || r.project_id === id);
});

/** The run the console shows (shared with the command line and the left pane; state/selection.ts). */
export { effectiveRunId };

/** Runs a card or question belongs to that GET /runs leaves out (the app's own "ui" run, whose
 *  print / slice cards sit in the console), fetched once each by id. */
export const offListRuns = signal<ReadonlyMap<string, AgentRun>>(new Map());
const lookedUp = new Set<string>();

/** Fetch an unknown run once so its cards get a real label (never retried within a session). */
export function lookUpRun(runId: string): void {
  if (lookedUp.has(runId) || runs.peek().has(runId)) return;
  lookedUp.add(runId);
  let client;
  try { client = actions.client(); } catch { lookedUp.delete(runId); return; }
  client.getRun(runId).then((r) => {
    offListRuns.value = new Map(offListRuns.value).set(runId, r);
  }, () => { /* stays "agent" */ });
}

/** Tests: forget the looked-up runs. */
export function resetOffListRuns(): void {
  lookedUp.clear();
  offListRuns.value = new Map();
}

export const offListLabels: ReadonlySignal<ReadonlyMap<string, string>> = computed(() =>
  new Map([...offListRuns.value.values()].map((r) => [r.id, runLabel(r, [r])])));

export const runLabels: ReadonlySignal<ReadonlyMap<string, string>> = computed(() => {
  const all = runList.value;
  return new Map([...offListLabels.value, ...all.map((r): [string, string] => [r.id, runLabel(r, all)])]);
});

/** Label for a run id ("designer · stiffen upright", "You (Mises)"); never an id or counter. */
export function labelOf(runId: string): string {
  const l = runLabels.value.get(runId);
  if (l !== undefined) return l;
  lookUpRun(runId);
  return "agent";
}

/** Look up the runs of cards / questions that are not in the run list. */
function lookUpCardRuns(): void {
  const known = runs.value;
  for (const a of approvals.value.values()) if (!known.has(a.run_id)) lookUpRun(a.run_id);
  for (const q of questions.value.values()) if (!known.has(q.run_id)) lookUpRun(q.run_id);
}

function readInput(): BuildLogInput {
  return {
    runs: runList.peek(), extraLabels: offListLabels.peek(), steps: steps.peek(), approvals: [...approvals.peek().values()], questions: [...questions.peek().values()],
    liveText: liveText.peek(), filter: filter.peek(), selectedRunId: effectiveRunId.peek(), expandedFolds: expandedFolds.peek(),
    inflight: inflight.peek(),
  };
}

const EMPTY: LogFrame = { items: [], counts: { everything: 0, decisions: 0, tool_calls: 0, files: 0, errors: 0 }, unfolded: 0 };

/** The log as of the last frame. `key` changes when the view (scope, run, filter) changes. */
export const logFrame = signal<LogFrame & { key: string }>({ ...EMPTY, key: "" });

type Schedule = (cb: () => void) => () => void;

function rafSchedule(cb: () => void): () => void {
  if (typeof requestAnimationFrame === "function") {
    const id = requestAnimationFrame(() => cb());
    return () => cancelAnimationFrame(id);
  }
  const id = setTimeout(cb, 16);
  return () => clearTimeout(id);
}

let users = 0;
let stop: (() => void) | null = null;

/** Start (ref-counted) the once-per-frame log rebuild. Returns a release function. */
export function retainLogFrames(schedule: Schedule = rafSchedule): () => void {
  if (users++ === 0) stop = start(schedule);
  let released = false;
  return () => {
    if (released) return;
    released = true;
    if (--users === 0) { stop?.(); stop = null; }
  };
}

function start(schedule: Schedule): () => void {
  let cancel: (() => void) | null = null;
  const flush = () => {
    cancel = null;
    const input = readInput();
    const key = `${input.filter.scope}|${input.filter.kind}|${input.filter.scope === "run" ? input.selectedRunId : "*"}`;
    logFrame.value = { ...buildLogFrame(input), key };
  };
  const dispose = effect(() => {
    // subscribe to every input; the rebuild itself runs in the next frame
    void runs.value; void steps.value; void approvals.value; void questions.value; void liveText.value;
    void inflight.value; void filter.value; void effectiveRunId.value; void expandedFolds.value;
    void offListRuns.value;
    lookUpCardRuns();
    if (!cancel) cancel = schedule(flush);
  });
  return () => { dispose(); cancel?.(); cancel = null; logFrame.value = { ...EMPTY, key: "" }; };
}

// ---- the conversation view: the shown run's thread, rebuilt at most once per frame. It always
// shows the whole run (the Details view's filter never hides anything here).

export interface ThreadFrame { runId: string | null; items: ThreadItem[] }

export const threadFrame = signal<ThreadFrame>({ runId: null, items: [] });

let threadUsers = 0;
let stopThread: (() => void) | null = null;

/** Start (ref-counted) the once-per-frame thread rebuild. Returns a release function. */
export function retainThreadFrames(schedule: Schedule = rafSchedule): () => void {
  if (threadUsers++ === 0) stopThread = startThread(schedule);
  let released = false;
  return () => {
    if (released) return;
    released = true;
    if (--threadUsers === 0) { stopThread?.(); stopThread = null; }
  };
}

// ---- Plan 9 D4: the View and Simulations streams over the build's agent tree (treeStreams),
// rebuilt at most once per frame like the thread.

export interface TreeFrame extends TreeStreams { buildId: string | null }

export const treeFrame = signal<TreeFrame>({ buildId: null, view: [], sim: [] });

let treeUsers = 0;
let stopTree: (() => void) | null = null;

/** Start (ref-counted) the once-per-frame tree rebuild. Returns a release function. */
export function retainTreeFrames(schedule: Schedule = rafSchedule): () => void {
  if (treeUsers++ === 0) stopTree = startTree(schedule);
  let released = false;
  return () => {
    if (released) return;
    released = true;
    if (--treeUsers === 0) { stopTree?.(); stopTree = null; }
  };
}

function startTree(schedule: Schedule): () => void {
  let cancel: (() => void) | null = null;
  const flush = () => {
    cancel = null;
    const tree = dockTree(buildRuns.peek(), effectiveRun.peek() ?? shownPending.peek());
    treeFrame.value = { buildId: activeBuildId.peek(), ...treeStreams(readInput(), tree) };
  };
  const dispose = effect(() => {
    void runs.value; void steps.value; void approvals.value; void questions.value; void liveText.value;
    void inflight.value; void effectiveRunId.value; void activeBuildId.value; void shownPending.value;
    if (!cancel) cancel = schedule(flush);
  });
  return () => { dispose(); cancel?.(); cancel = null; treeFrame.value = { buildId: null, view: [], sim: [] }; };
}

function startThread(schedule: Schedule): () => void {
  let cancel: (() => void) | null = null;
  const flush = () => {
    cancel = null;
    const input: BuildLogInput = { ...readInput(), filter: { scope: "run", kind: "everything" } };
    threadFrame.value = { runId: input.selectedRunId, items: buildThread(buildLog(input)) };
  };
  const dispose = effect(() => {
    void runs.value; void steps.value; void approvals.value; void questions.value; void liveText.value;
    void inflight.value; void effectiveRunId.value; void expandedFolds.value; void offListRuns.value;
    lookUpCardRuns();
    if (!cancel) cancel = schedule(flush);
  });
  return () => { dispose(); cancel?.(); cancel = null; threadFrame.value = { runId: null, items: [] }; };
}
