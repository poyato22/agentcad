// Live per-run agent activity, fed by the `agent_activity` event (Plan 5 contract). The top-bar
// activity pill and (Task A2) the agent manager read `activityByRun` / `activitySummary`. A run drops
// out of the map once it is done; an idle run (its turn ended, waiting for your next message) stays
// so the manager can say "idle", but only working / waiting states are counted.
import { computed, signal, type ReadonlySignal, type Signal } from "@preact/signals";
import { onCoreEvent } from "../api/events";
import { ACTIVITY_STATES, type ActivityState, type AgentActivity, type MisesEvent } from "../api/types";

export const activityByRun: Signal<ReadonlyMap<string, AgentActivity>> = signal<ReadonlyMap<string, AgentActivity>>(new Map());

/** States that count as "working" in the pill. */
const WORKING: ReadonlySet<ActivityState> = new Set<ActivityState>(["starting", "thinking", "writing", "tool"]);
const WAITING: ReadonlySet<ActivityState> = new Set<ActivityState>(["waiting_approval", "waiting_user"]);

export function parseActivity(ev: MisesEvent): AgentActivity | null {
  if (ev.type !== "agent_activity") return null;
  const d = ev.data;
  const runId = ev.run_id ?? (typeof d.run_id === "string" ? d.run_id : null);
  if (!runId) return null;
  if (typeof d.state !== "string" || !(ACTIVITY_STATES as readonly string[]).includes(d.state)) return null;
  return {
    run_id: runId,
    state: d.state as ActivityState,
    detail: typeof d.detail === "string" ? d.detail : "",
    since: typeof d.since === "number" ? d.since : Date.now() / 1000,
    ...(typeof d.tokens_out === "number" ? { tokens_out: d.tokens_out } : {}),
  };
}

export function recordActivity(ev: MisesEvent): void {
  if (ev.type === "sim_finished" || ev.type === "topopt_finished") { recordSimFinished(ev); return; }
  // a finished run is no longer active, even when no final idle/done activity came (a crash, or
  // a delete whose tracker is forgotten without one): drop it so no stale state lingers (A2)
  if (ev.type === "run_finished" && ev.run_id && activityByRun.value.has(ev.run_id)) {
    const m = new Map(activityByRun.value);
    m.delete(ev.run_id);
    activityByRun.value = m;
    return;
  }
  if (ev.type === "run_deleted") {
    const ids = [ev.run_id, ev.data.run_id, ...(Array.isArray(ev.data.run_ids) ? ev.data.run_ids : [])].filter((x): x is string => typeof x === "string");
    forgetActivity(ids);
    return;
  }
  const a = parseActivity(ev);
  if (!a) return;
  const m = new Map(activityByRun.value);
  // "done" means the run is no longer active; drop it rather than show a stale state. "idle" is
  // kept (the manager shows it; the thread status line and the pill ignore it).
  if (a.state === "done") m.delete(a.run_id);
  else m.set(a.run_id, a);
  activityByRun.value = m;
}

export interface ActivitySummary {
  working: number;
  thinking: number;
  waiting: number;
  /** A short human label, or null when nothing is working or waiting. */
  label: string | null;
}

function summarise(byRun: ReadonlyMap<string, AgentActivity>): ActivitySummary {
  let working = 0, thinking = 0, waiting = 0;
  for (const a of byRun.values()) {
    if (WORKING.has(a.state)) { working++; if (a.state === "thinking") thinking++; }
    else if (WAITING.has(a.state)) waiting++;
  }
  let label: string | null = null;
  if (working > 0) label = `${working} working${thinking > 0 ? ` · ${thinking} thinking` : ""}`;
  else if (waiting > 0) label = `${waiting} waiting`;
  return { working, thinking, waiting, label };
}

export const activitySummary: ReadonlySignal<ActivitySummary> = computed(() => summarise(activityByRun.value));

/** Subscribes to the event stream (default onCoreEvent). Call once at boot; returns an unsubscribe. */
export function startActivity(subscribe: (cb: (e: MisesEvent) => void) => () => void = onCoreEvent): () => void {
  return subscribe(recordActivity);
}

/** Deleted runs (C-6) leave the pill and the manager. */
export function forgetActivity(ids: readonly string[]): void {
  if (!ids.some((id) => activityByRun.peek().has(id))) return;
  const m = new Map(activityByRun.peek());
  for (const id of ids) m.delete(id);
  activityByRun.value = m;
}

/** A snapshot from GET /runs/{id} (`activity`), as the event would carry it; null when absent or
 *  malformed. */
export function parseSnapshot(runId: string, raw: unknown): AgentActivity | null {
  if (typeof raw !== "object" || raw === null) return null;
  return parseActivity({ type: "agent_activity", run_id: runId, data: raw as Record<string, unknown>, ts: 0 });
}

/**
 * Seeds activity from the core on load and on every reconnect (C-3): the pill and the manager
 * show what runs are doing even though their `agent_activity` events came before this socket.
 * `snapshots` holds each run the core still runs (by id → its `activity`, null when none yet);
 * `finished` are runs the core reports as no longer running. Runs not named keep their entry.
 */
export function seedActivity(snapshots: ReadonlyMap<string, unknown>, finished: readonly string[] = [], asOf = Infinity): void {
  const m = new Map(activityByRun.peek());
  let changed = false;
  // a finished run's working state is stale (its done came before this socket); an idle Claude
  // Code session between turns stays, as recordActivity keeps it
  for (const id of finished) {
    const cur = m.get(id);
    if (cur && cur.state !== "idle" && cur.since < asOf) { m.delete(id); changed = true; }
  }
  for (const [id, raw] of snapshots) {
    const a = parseSnapshot(id, raw);
    // no snapshot (nothing yet when the core answered): what came live since stays
    if (!a) continue;
    const cur = m.get(id);
    // a live event newer than the snapshot wins (the fetch may predate it)
    if (cur && cur.since > a.since) continue;
    if (a.state === "done") { if (m.delete(id)) changed = true; continue; }
    m.set(id, a);
    changed = true;
  }
  if (changed) activityByRun.value = m;
}

/** Clears activity (a new core session starts fresh). */
export function resetActivity(): void { activityByRun.value = new Map(); finishedSims.value = new Map(); }

// ---- Plan 9 (D2 / D4): what the agent dock needs to know

/** Studies the core reported finished (`sim_finished {sim_id, status}`, B3), by sim id → status.
 *  A study started with wait:false returns "running" at once; this is how its card learns it
 *  ended without the run's next step. Bounded (oldest dropped). */
export const finishedSims: Signal<ReadonlyMap<string, string>> = signal<ReadonlyMap<string, string>>(new Map());
const FINISHED_KEEP = 200;

export function recordSimFinished(ev: MisesEvent): void {
  if (ev.type !== "sim_finished" && ev.type !== "topopt_finished") return;
  // a generative design run (Plan 9 D14) finishes like a study, by its topopt id
  // H4: a whole-assembly study (and its part-stress hand-off) finishes by its job id
  const raw = ev.type === "sim_finished" ? ev.data.sim_id ?? (ev.data.kind === "assembly_study" ? ev.data.job_id : undefined) : ev.data.topopt_id;
  const id = typeof raw === "string" ? raw : null;
  if (!id) return;
  const m = new Map(finishedSims.peek());
  m.delete(id);
  m.set(id, typeof ev.data.status === "string" ? ev.data.status : "done");
  while (m.size > FINISHED_KEEP) m.delete(m.keys().next().value as string);
  finishedSims.value = m;
}

/** Is the run busy, so the composer offers Stop? A queued or running run, unless its activity says
 *  its turn ended (an idle Claude Code session between turns is "running" but not busy). Waiting on
 *  you counts: Stop is how you end a run you don't want to answer. */
export function runBusy(run: { status: string }, activity: AgentActivity | undefined): boolean {
  if (run.status !== "running" && run.status !== "queued") return false;
  return activity === undefined || (activity.state !== "idle" && activity.state !== "done");
}
