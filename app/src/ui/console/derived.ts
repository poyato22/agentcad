// Console-local derived state (computed from the store; the store itself is not changed here).
// The log is rebuilt at most once per animation frame: one effect watches the store inputs and
// schedules a single rebuild into `logFrame`, which StepLog and Filters render from.
import { computed, effect, signal, type ReadonlySignal } from "@preact/signals";
import type { AgentRun } from "../../api/types";
import { runLabel } from "../../lib/names";
import { approvals, filter, inflight, liveText, questions, runs, selectedRunId, steps } from "../../state/store";
import { buildLogFrame, type BuildLogInput, type LogFrame } from "./logModel";

/** Folds the user opened, by foldKey. */
export const expandedFolds = signal<ReadonlySet<string>>(new Set());

export function toggleFold(key: string): void {
  const next = new Set(expandedFolds.value);
  if (!next.delete(key)) next.add(key);
  expandedFolds.value = next;
}

export const runList: ReadonlySignal<readonly AgentRun[]> = computed(() => [...runs.value.values()]);

/** Roster order: running first, then newest first. */
export const rosterRuns: ReadonlySignal<readonly AgentRun[]> = computed(() =>
  [...runList.value].sort((a, b) => Number(b.status === "running") - Number(a.status === "running") || b.created_at - a.created_at));

/** The run the console shows: the selected one, else the newest running run, else the newest run. */
export const effectiveRunId: ReadonlySignal<string | null> = computed(() => {
  const sel = selectedRunId.value;
  if (sel && runs.value.has(sel)) return sel;
  return rosterRuns.value[0]?.id ?? sel ?? null;
});

export const runLabels: ReadonlySignal<ReadonlyMap<string, string>> = computed(() => {
  const all = runList.value;
  return new Map(all.map((r) => [r.id, runLabel(r, all)]));
});

/** Label for a run id ("designer · stiffen upright"); never an id or counter. */
export function labelOf(runId: string): string {
  return runLabels.value.get(runId) ?? "agent";
}

function readInput(): BuildLogInput {
  return {
    runs: runList.peek(), steps: steps.peek(), approvals: [...approvals.peek().values()], questions: [...questions.peek().values()],
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
    if (!cancel) cancel = schedule(flush);
  });
  return () => { dispose(); cancel?.(); cancel = null; logFrame.value = { ...EMPTY, key: "" }; };
}
