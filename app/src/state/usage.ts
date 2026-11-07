// Usage per turn and per build (GET /runs/{id}/usage, /projects/{id}/usage). Read for the run
// shown in the dock and the active build on (re)connect and on a switch, and again when any run
// finishes a turn (Claude Code reports its usage with each turn's result).
import { effect, signal, type Signal } from "@preact/signals";
import { onCoreEvent, onCoreState, type StreamState } from "../api/events";
import type { MisesEvent } from "../api/types";
import type { BuildUsage, RunUsage } from "../api/usage";
import { debug } from "../lib/log";
import { activeBuildId } from "./builds";
import { effectiveRunId } from "./selection";
import { actions } from "./store";

/** By the run id asked for (the dock's run). */
export const runUsage: Signal<ReadonlyMap<string, RunUsage>> = signal<ReadonlyMap<string, RunUsage>>(new Map());
/** By build id. */
export const buildUsage: Signal<ReadonlyMap<string, BuildUsage>> = signal<ReadonlyMap<string, BuildUsage>>(new Map());

export interface UsageDeps {
  subscribe?: (cb: (e: MisesEvent) => void) => () => void;
  subscribeState?: (cb: (s: StreamState) => void) => () => void;
  fetchRun?: (runId: string) => Promise<RunUsage>;
  fetchBuild?: (projectId: string) => Promise<BuildUsage>;
}

export function startUsage(deps: UsageDeps = {}): () => void {
  const fetchRun = deps.fetchRun ?? ((id: string) => actions.client().runUsage(id));
  const fetchBuild = deps.fetchBuild ?? ((id: string) => actions.client().buildUsage(id));
  const loadRun = (id: string | null) => {
    if (!id) return;
    Promise.resolve().then(() => fetchRun(id)).then(
      (u) => { runUsage.value = new Map(runUsage.peek()).set(id, u); },
      (e: unknown) => debug("GET /runs/{id}/usage failed", e));
  };
  const loadBuild = (id: string | null) => {
    if (!id) return;
    Promise.resolve().then(() => fetchBuild(id)).then(
      (u) => { buildUsage.value = new Map(buildUsage.peek()).set(id, u); },
      (e: unknown) => debug("GET /projects/{id}/usage failed", e));
  };
  const both = () => { loadRun(effectiveRunId.peek()); loadBuild(activeBuildId.peek()); };
  const offEvents = (deps.subscribe ?? onCoreEvent)((ev) => {
    if (ev.type === "run_finished") both();
  });
  const offState = (deps.subscribeState ?? onCoreState)((s) => { if (s === "open") both(); });
  const offRun = effect(() => loadRun(effectiveRunId.value));
  const offBuild = effect(() => loadBuild(activeBuildId.value));
  return () => { offEvents(); offState(); offRun(); offBuild(); };
}

export function resetUsage(): void {
  runUsage.value = new Map();
  buildUsage.value = new Map();
}
