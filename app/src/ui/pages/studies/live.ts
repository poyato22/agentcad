// Live study progress for every surface that shows a running study (the Studies page, the inline
// study cards in the thread, the immersive view): the latest `sim_progress {sim_id, phase, pct,
// detail}` per study, from the core's event stream. One subscription for the whole app.
//
// A card of a running sim_run call has no sim_id yet (it comes with the tool result), so a call is
// matched to its study by run, study type and part: studies are memory bound and the core runs
// one at a time, and an agent's calls in one run are sequential.
import { signal, type Signal } from "@preact/signals";
import { onCoreEvent } from "../../../api/events";
import { studyProgressSchema } from "../../../api/studies";
import type { MisesEvent } from "../../../api/types";

export interface LiveProgress {
  sim_id: string; run_id: string | null; part_id: string; type: string | null;
  stage: string; phase: string | null; pct: number | null; detail: string | null;
  /** Event time (s). */
  ts: number;
}

/** sim_id → its latest progress. Replaced (never mutated) on every event. */
export const liveProgress: Signal<ReadonlyMap<string, LiveProgress>> = signal(new Map());

const TERMINAL = new Set(["done", "failed", "cancelled"]);
export const isTerminal = (p: LiveProgress): boolean => TERMINAL.has(p.stage) || p.phase === "done";

/** At most this many studies are remembered (oldest dropped). */
const KEEP = 200;

export function applyProgressEvent(ev: MisesEvent): void {
  if (ev.type !== "sim_progress") return;
  const r = studyProgressSchema.safeParse(ev.data);
  if (!r.success) return;
  const d = r.data;
  const next = new Map(liveProgress.peek());
  next.delete(d.sim_id);
  next.set(d.sim_id, {
    sim_id: d.sim_id, run_id: ev.run_id ?? null, part_id: d.part_id, type: d.type ?? null, stage: d.stage,
    phase: d.phase ?? null, pct: typeof d.pct === "number" ? d.pct : null, detail: d.detail ?? null, ts: ev.ts,
  });
  while (next.size > KEEP) next.delete(next.keys().next().value as string);
  liveProgress.value = next;
}

let off: (() => void) | null = null;
/** Starts listening (idempotent); returns a stop function for tests. */
export function installLiveProgress(): () => void {
  off ??= onCoreEvent(applyProgressEvent);
  return () => { off?.(); off = null; };
}
installLiveProgress();

/** Tests: forget everything. */
export function resetLiveProgress(): void { liveProgress.value = new Map(); }

/** The newest study of this run, type and part that started at or after `sinceTs` (a running call's study). */
export function progressForCall(map: ReadonlyMap<string, LiveProgress>, q: { runId: string; type: string | null; partId: string | null; sinceTs: number }): LiveProgress | null {
  let best: LiveProgress | null = null;
  for (const p of map.values()) {
    if (p.run_id !== q.runId || p.ts + 1 < q.sinceTs) continue;
    if (q.type && p.type && p.type !== q.type) continue;
    if (q.partId && p.part_id !== q.partId) continue;
    if (!best || p.ts >= best.ts) best = p;
  }
  return best;
}
