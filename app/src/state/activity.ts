// Live per-run agent activity, fed by the `agent_activity` event (Plan 5 contract). The top-bar
// activity pill and (Task A2) the agent manager read `activityByRun` / `activitySummary`. A run drops
// out of the map once it goes idle or done, so "working" counts only runs actually doing something.
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
  // a finished run is no longer active, even when no final idle/done activity came (a crash, or
  // a delete whose tracker is forgotten without one): drop it so no stale state lingers (A2)
  if (ev.type === "run_finished" && ev.run_id && activityByRun.value.has(ev.run_id)) {
    const m = new Map(activityByRun.value);
    m.delete(ev.run_id);
    activityByRun.value = m;
    return;
  }
  const a = parseActivity(ev);
  if (!a) return;
  const m = new Map(activityByRun.value);
  // "idle"/"done" mean the run is no longer active; drop it rather than show a stale state.
  if (a.state === "idle" || a.state === "done") m.delete(a.run_id);
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

/** Clears activity (a new core session starts fresh). */
export function resetActivity(): void { activityByRun.value = new Map(); }
