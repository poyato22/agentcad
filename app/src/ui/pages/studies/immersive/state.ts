// Which study the immersive view shows, and the others it can step to (⌘[ / ⌘]): the studies of
// the same design review, the same run, or the Studies page's history. `why` is who ran it and
// their one-line reason (from the tool call), shown in the side strip as data.
import { signal, type Signal } from "@preact/signals";

export interface ImmersiveOrigin { agent: string | null; runId: string | null; reason: string | null }

export interface ImmersiveTarget {
  simId: string | null;
  /** A study still starting (no sim_id yet): its run, type and part, to follow its progress. */
  pending?: { runId: string; callId?: string | null; type: string | null; partId: string | null; sinceTs: number } | null;
  ids: readonly string[];
  origin?: ImmersiveOrigin | null;
}

export const immersive: Signal<ImmersiveTarget | null> = signal(null);

/** Opens the immersive view on a study (element focused before it is restored on close). */
let returnFocus: HTMLElement | null = null;

export function openImmersive(t: ImmersiveTarget): void {
  returnFocus = typeof document !== "undefined" && document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const ids = t.simId && !t.ids.includes(t.simId) ? [t.simId, ...t.ids] : t.ids;
  immersive.value = { ...t, ids };
}

export function closeImmersive(): void {
  immersive.value = null;
  const el = returnFocus;
  returnFocus = null;
  if (el && el.isConnected) el.focus();
}

/** ⌘] (+1) / ⌘[ (−1): the next / previous study of the same set (wraps around). */
export function stepImmersive(delta: 1 | -1): void {
  const t = immersive.peek();
  if (!t || t.ids.length < 2 || t.simId === null) return;
  const i = t.ids.indexOf(t.simId);
  const next = t.ids[(i + delta + t.ids.length) % t.ids.length];
  immersive.value = { ...t, simId: next, pending: null, origin: next === t.simId ? t.origin : null };
}

/** The pending study got its sim_id (its tool result landed). */
export function resolvePending(simId: string): void {
  const t = immersive.peek();
  if (!t || t.simId !== null) return;
  immersive.value = { ...t, simId, pending: null, ids: t.ids.includes(simId) ? t.ids : [simId, ...t.ids] };
}
