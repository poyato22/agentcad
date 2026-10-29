// Live Onshape budget for the top-bar meter (Plan 5 A6). The core sends an `onshape_budget` event
// after every real Onshape request (the app's browsing and every agent's calls alike), carrying the
// same shape as GET /onshape/budget for that request's run. So the year count moves the moment
// anything spends a call, and each run's own count is kept by run id ("" = the app's own browsing
// with no run, since the core started). GET /onshape/budget is read on every (re)connect and for a
// newly selected run whose count we haven't seen yet.
import { effect, signal, type Signal } from "@preact/signals";
import { onCoreEvent, onCoreState, type StreamState } from "../api/events";
import { onshapeBudgetSchema, type OnshapeBudget } from "../api/directory";
import type { MisesEvent } from "../api/types";
import { debug } from "../lib/log";
import { actions, selectedRunId } from "./store";

export type BudgetLevel = "ok" | "warn" | "limit";

/** The latest year figures (any run's event updates them). */
export const onshapeBudget: Signal<OnshapeBudget | null> = signal<OnshapeBudget | null>(null);
/** Calls used per run id; "" is the app's own browsing (no run) since the core started. */
export const onshapeRunUsed: Signal<ReadonlyMap<string, number>> = signal<ReadonlyMap<string, number>>(new Map());
/** Bumped whenever the year count goes up, so the meter can show that it just moved. */
export const onshapeBudgetTick: Signal<number> = signal<number>(0);

/** Amber from `warn_at`, red at the yearly limit (a 0 limit is always at the limit). */
export function budgetLevel(b: Pick<OnshapeBudget, "used_year" | "limit_year" | "warn_at">): BudgetLevel {
  if (b.used_year >= b.limit_year) return "limit";
  if (b.used_year >= b.warn_at) return "warn";
  return "ok";
}

/** Folds one budget snapshot in. An older snapshot (a slow GET landing after a newer event) never
 *  moves the year count back, except across a yearly reset (a different `reset_at`). `runId` is
 *  whose `used_run` it carries (null: the app's own browsing). */
export function applyBudget(b: OnshapeBudget, runId: string | null = b.run_id): void {
  const cur = onshapeBudget.peek();
  const sameYear = cur !== null && cur.reset_at === b.reset_at;
  if (!sameYear || b.used_year >= cur.used_year) {
    if (sameYear && b.used_year > cur.used_year) onshapeBudgetTick.value = onshapeBudgetTick.peek() + 1;
    onshapeBudget.value = { ...b, run_id: null };
  } else if (cur.limit_year !== b.limit_year || cur.warn_at !== b.warn_at) {
    onshapeBudget.value = { ...cur, limit_year: b.limit_year, warn_at: b.warn_at };   // the cap changed
  }
  // per run: the latest answer wins (a new core starts the no-run count again from 0)
  const key = runId ?? "";
  const runs = onshapeRunUsed.peek();
  if (runs.get(key) !== b.used_run) onshapeRunUsed.value = new Map(runs).set(key, b.used_run);
}

/** An `onshape_budget` event, or null for anything else / a malformed one. */
export function parseBudgetEvent(ev: MisesEvent): { budget: OnshapeBudget; runId: string | null } | null {
  if (ev.type !== "onshape_budget") return null;
  const p = onshapeBudgetSchema.safeParse(ev.data);
  if (!p.success) return null;
  return { budget: p.data, runId: ev.run_id ?? p.data.run_id };
}

export interface BudgetDeps {
  subscribe?: (cb: (e: MisesEvent) => void) => () => void;
  subscribeState?: (cb: (s: StreamState) => void) => () => void;
  fetch?: (runId: string | null) => Promise<OnshapeBudget>;
}

/** Call once at boot (main.tsx). Returns an unsubscribe. */
export function startOnshapeBudget(deps: BudgetDeps = {}): () => void {
  const subscribe = deps.subscribe ?? onCoreEvent;
  const subscribeState = deps.subscribeState ?? onCoreState;
  const fetch = deps.fetch ?? ((runId: string | null) => actions.client().onshapeBudget(runId));
  const inflight = new Set<string>();

  function load(runId: string | null): void {
    const key = runId ?? "";
    if (inflight.has(key)) return;
    inflight.add(key);
    Promise.resolve().then(() => fetch(runId)).then(
      (b) => applyBudget(b, runId),
      (e: unknown) => debug("GET /onshape/budget failed", e),
    ).finally(() => inflight.delete(key));
  }

  const offEvents = subscribe((ev) => {
    const got = parseBudgetEvent(ev);
    if (got) applyBudget(got.budget, got.runId);
  });
  const offState = subscribeState((s) => {
    if (s !== "open") return;
    load(null);
    const sel = selectedRunId.peek();
    if (sel) load(sel);
  });
  const offSel = effect(() => {
    const sel = selectedRunId.value;
    if (sel && !onshapeRunUsed.peek().has(sel) && onshapeBudget.peek() !== null) load(sel);
  });
  return () => { offEvents(); offState(); offSel(); };
}

/** Back to boot state (tests). */
export function resetOnshapeBudget(): void {
  onshapeBudget.value = null;
  onshapeRunUsed.value = new Map();
  onshapeBudgetTick.value = 0;
}
