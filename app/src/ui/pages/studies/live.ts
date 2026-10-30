// Live study progress for every surface that shows a running study (the Studies page, the inline
// study cards in the thread, the immersive view): the latest `sim_progress {sim_id, call_id,
// phase, pct, detail}` per study, from the core's event stream. One subscription for the app.
//
// A card of a running sim_run call has no sim_id yet (it comes with the tool result), so a call
// finds its study by the event's call_id. Without a call_id (an older core) it falls back to run
// + study type + part, and refuses to guess when more than one study matches (design_review runs
// several same-type studies of one part at once): the card then just says "starting".
import { signal, type Signal } from "@preact/signals";
import { onCoreEvent } from "../../../api/events";
import { studyProgressSchema } from "../../../api/studies";
import type { MisesEvent } from "../../../api/types";

export interface LiveProgress {
  sim_id: string; run_id: string | null; part_id: string; type: string | null;
  call_id: string | null; step_id: number | null;
  stage: string; phase: string | null; pct: number | null; detail: string | null;
  /** Event time (s). */
  ts: number;
}

/** sim_id → its latest progress. Replaced (never mutated) on every event. */
export const liveProgress: Signal<ReadonlyMap<string, LiveProgress>> = signal(new Map());

const TERMINAL = new Set(["done", "failed", "cancelled"]);
/** The core's last event of a study: phase (and stage) done / failed / cancelled. */
export const isTerminal = (p: LiveProgress): boolean => TERMINAL.has(p.stage) || (p.phase !== null && TERMINAL.has(p.phase));

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
    sim_id: d.sim_id, run_id: ev.run_id ?? null, part_id: d.part_id, type: d.type ?? null,
    call_id: d.call_id ?? null, step_id: d.step_id ?? null, stage: d.stage,
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

export interface CallQuery { runId: string; callId?: string | null; type: string | null; partId: string | null; sinceTs: number }

/** The study a running tool call runs: by call_id; else (no call_id on any candidate) the one
 *  study of this run, type and part since the call started, or null when that is ambiguous. */
export function progressForCall(map: ReadonlyMap<string, LiveProgress>, q: CallQuery): LiveProgress | null {
  const ofRun = [...map.values()].filter((p) => p.run_id === q.runId);
  if (q.callId) {
    let hit: LiveProgress | null = null;
    for (const p of ofRun) if (p.call_id === q.callId && (!hit || p.ts >= hit.ts)) hit = p;
    if (hit) return hit;
    // the core names its calls: a study of another call is never this one
    if (ofRun.some((p) => p.call_id)) return null;
  }
  const cands = ofRun.filter((p) => p.ts + 1 >= q.sinceTs && (!q.type || !p.type || p.type === q.type) && (!q.partId || p.part_id === q.partId));
  const ids = new Set(cands.map((p) => p.sim_id));
  return ids.size === 1 ? cands[0] : null;
}
