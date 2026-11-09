// Agent-built simulators (plan 2026-11-06 S3, S6): the simulator requests waiting on the user,
// for "Needs your decision". A request is decided once (decisions/once: one request per item;
// the core answers 409 already_decided to a second one, and that closes the card too). Never
// auto-approved: only the user's click sends a decision.
import { signal, type Signal } from "@preact/signals";
import { onCoreEvent } from "../../../api/events";
import { simRequestSchema, type SimBudgetDecision, type SimDecision, type SimRequest } from "../../../api/simRequests";
import { actions } from "../../../state/store";
import { alreadyDecided, markResolved, once } from "./once";

export const simRequests: Signal<ReadonlyMap<string, SimRequest>> = signal<ReadonlyMap<string, SimRequest>>(new Map());
export const simErrors: Signal<ReadonlyMap<string, string>> = signal<ReadonlyMap<string, string>>(new Map());

export const simKey = (r: SimRequest): string => (r.status === "paused_budget" ? `simbudget:${r.id}:${r.budget?.paused_spend?.usd ?? 0}` : `simreq:${r.id}`);

function put(r: SimRequest): void {
  const m = new Map(simRequests.peek());
  m.set(r.id, r);
  simRequests.value = m;
}

function fail(id: string, msg: string | null): void {
  const m = new Map(simErrors.peek());
  if (msg) m.set(id, msg); else m.delete(id);
  simErrors.value = m;
}

const why = (e: unknown): string => (e instanceof Error ? e.message : String(e));

/** Reads the open requests (pending, or paused at their budget). */
export async function loadSimRequests(): Promise<void> {
  try {
    const [p, b] = await Promise.all([actions.client().listSimRequests("pending"), actions.client().listSimRequests("paused_budget")]);
    const m = new Map(simRequests.peek());
    for (const r of [...p.requests, ...b.requests]) m.set(r.id, r);
    simRequests.value = m;
  } catch { /* the section shows what it has; the next event reloads */ }
}

const DONE: Readonly<Record<SimDecision, string>> = { accept: "Accepted by you", reject: "Rejected by you", changes: "Changes asked by you" };

export function decideSim(r: SimRequest, action: SimDecision, reason: string): Promise<boolean> {
  const key = simKey(r);
  return once(key, async () => {
    try {
      const out = await actions.client().decideSimRequest(r.id, action, reason);
      markResolved(key, DONE[action]);
      put(out.request);
      fail(r.id, null);
      return true;
    } catch (e) {
      const d = alreadyDecided(e);
      if (d) { markResolved(key, `Already decided: ${typeof d.status === "string" ? d.status.replace(/_/g, " ") : "decided"}`); return false; }
      fail(r.id, `Could not send the decision: ${why(e)}`);
      return false;
    }
  }).then((v) => v ?? false);
}

export function decideBudget(r: SimRequest, action: SimBudgetDecision): Promise<boolean> {
  const key = simKey(r);
  return once(key, async () => {
    try {
      const out = await actions.client().simRequestBudget(r.id, action);
      markResolved(key, action === "continue" ? "Continued by you" : "Stopped by you");
      put(out.request);
      fail(r.id, null);
      return true;
    } catch (e) {
      if (alreadyDecided(e)) { markResolved(key, "Already decided"); return false; }
      fail(r.id, `Could not send the decision: ${why(e)}`);
      return false;
    }
  }).then((v) => v ?? false);
}

/** Live: a filed or decided request updates the list (events carry the request). */
export function bindSimRequestEvents(): () => void {
  return onCoreEvent((ev) => {
    if (ev.type !== "sim_request_filed" && ev.type !== "sim_request_decided") return;
    const r = simRequestSchema.safeParse(ev.data);
    if (r.success) put(r.data);
  });
}

/** Tests. */
export function resetSimRequests(): void { simRequests.value = new Map(); simErrors.value = new Map(); }
