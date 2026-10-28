// Runs waiting for a parallel-agent slot (Plan 4: status "queued", event `run_queued`). The
// store's reducer marks a known run queued and refetches the list for an unknown one; until that
// list arrives the console reads the run once itself (onCoreEvent), so it shows at once. A run
// the console just created and the store hasn't listed is read the same way.
import { computed, signal, type ReadonlySignal } from "@preact/signals";
import { onCoreEvent } from "../../api/events";
import type { AgentRun } from "../../api/types";
import { debug } from "../../lib/log";
import { actions, runs, selectedRunId } from "../../state/store";

const extra = signal<ReadonlyMap<string, AgentRun>>(new Map());

/** Fetch one run the store doesn't have yet (queued, or just created). */
export function noteRun(id: string): void {
  if (runs.peek().has(id)) return;
  let p: Promise<AgentRun>;
  try { p = actions.client().getRun(id); } catch (e) { debug("getRun failed", id, e); return; }
  p.then((r) => {
    if (runs.peek().has(r.id)) return;
    extra.value = new Map([...extra.peek(), [r.id, r]]);
  }, (e: unknown) => debug("getRun failed", id, e));
}

let untap: (() => void) | null = null;

/** Start listening for `run_queued` (idempotent). */
export function watchQueued(): void {
  if (untap) return;
  untap = onCoreEvent((ev) => { if (ev.type === "run_queued" && ev.run_id) noteRun(ev.run_id); });
}

/** Runs the store doesn't list yet (the store's copy always wins). */
export const pendingRuns: ReadonlySignal<ReadonlyMap<string, AgentRun>> = computed(() => {
  const have = runs.value;
  const out = new Map<string, AgentRun>();
  for (const [id, r] of extra.value) if (!have.has(id)) out.set(id, r);
  return out;
});

/** The run the conversation shows: the store's, else a pending one with the selected id. */
export const shownPending: ReadonlySignal<AgentRun | null> = computed(() => {
  const id = selectedRunId.value;
  return id ? pendingRuns.value.get(id) ?? null : null;
});

/** Tests. */
export function resetQueued(): void {
  extra.value = new Map();
  untap?.();
  untap = null;
}
