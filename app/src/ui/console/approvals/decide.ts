// Sending a decision for an approval card. One request in flight per approval (shared by every
// rendering of that card); never retried automatically. Maps the card's three choices onto the
// core's binary decide: Merge/Approve → approve:true; Keep branch and Reject → approve:false
// (the core doesn't delete branches on reject; "kept branch" is recorded in the UI only).
import { signal, type Signal } from "@preact/signals";
import { CoreError } from "../../../api/client";
import type { Approval } from "../../../api/types";
import { actions, approvals, retiredApprovals } from "../../../state/store";
import { isResolved, markResolved, resetOnce } from "../decisions/once";

export type Decision = "approve" | "keep_branch" | "reject";
export interface CardNotice { text: string; tone: "blue" | "red" }

/** Approval ids with a decide (or its stale refresh) in flight. */
export const deciding: Signal<ReadonlySet<string>> = signal<ReadonlySet<string>>(new Set());
/** Per-card notice ("proposal changed — review again", errors). */
export const notices: Signal<ReadonlyMap<string, CardNotice>> = signal<ReadonlyMap<string, CardNotice>>(new Map());
/** Rejections the user made with "Keep branch" (UI-only until the core records an outcome). */
export const keptBranch: Signal<ReadonlySet<string>> = signal<ReadonlySet<string>>(new Set());
/** Cards whose proposal was replaced by a different approval: they can no longer decide. */
export const retired: Signal<ReadonlySet<string>> = retiredApprovals; // lives in the store: counters read it too

const inflight = new Map<string, Promise<Approval>>();

export const STALE_NOTICE = "proposal changed — review again";

export function isExpired(a: Approval, nowS: number = Date.now() / 1000): boolean {
  return a.status === "expired" || (a.status === "pending" && a.expires_at <= nowS);
}

/** Can this card still be decided (pending, not expired, not replaced)? */
export function canDecide(a: Approval, nowS?: number): boolean {
  return a.status === "pending" && !isExpired(a, nowS) && !retired.peek().has(a.id) && !isResolved(`approval:${a.id}`);
}

export function decide(a: Approval, action: Decision): Promise<Approval> {
  const running = inflight.get(a.id);
  if (running) return running;
  // judge the latest copy the store has (decided elsewhere, expired, or a newer proposal), not
  // the caller's; a different hash means the human hasn't seen this proposal: never send it
  const latest = approvals.peek().get(a.id) ?? a;
  if (!canDecide(latest) || latest.payload_hash !== a.payload_hash) return Promise.resolve(latest);
  setIn(deciding, a.id, true);
  const p = send(a, action).finally(() => {
    inflight.delete(a.id);
    setIn(deciding, a.id, false);
  });
  inflight.set(a.id, p);
  return p;
}

async function send(a: Approval, action: Decision): Promise<Approval> {
  setNotice(a.id, null);
  let out: Approval;
  try {
    out = await actions.client().decide(a.id, { payload_hash: a.payload_hash, approve: action === "approve" });
  } catch (e) {
    if (e instanceof CoreError && e.code === "stale_version") return refresh(a);
    if (e instanceof CoreError && e.code === "network") return recheck(a, e);
    setNotice(a.id, { text: `decision not sent: ${errText(e)}`, tone: "red" });
    return a;
  }
  if (action === "keep_branch" && out.status === "rejected") setIn(keptBranch, a.id, true);
  // view-tab: decided once, read-only everywhere from now on (a replayed pending copy included)
  if (out.status !== "pending") markResolved(`approval:${a.id}`, out.status, out.decided_at ?? undefined);
  actions.upsertApproval(out);
  return out;
}

/** 409 stale_version: reload this run's pending approvals and replace the card in place. */
async function refresh(a: Approval): Promise<Approval> {
  let list: Approval[];
  try {
    list = await actions.client().pendingApprovals(a.run_id);
  } catch (e) {
    // the card keeps its old hash, so another click can only 409 again: it can never approve
    setNotice(a.id, { text: `proposal changed — reload failed: ${errText(e)}`, tone: "red" });
    return a;
  }
  for (const x of list) actions.upsertApproval(x);
  const fresh = list.find((x) => x.id === a.id);
  if (fresh) {
    setNotice(a.id, { text: STALE_NOTICE, tone: "blue" });
    return fresh;
  }
  setIn(retired, a.id, true);
  setNotice(a.id, { text: "proposal changed — this card is no longer pending", tone: "blue" });
  return a;
}

/**
 * No answer from the core: the decision may or may not have landed. Ask the core for the run's
 * pending approvals and show what it says, never retrying the decision.
 */
async function recheck(a: Approval, cause: unknown): Promise<Approval> {
  let list: Approval[];
  try {
    list = await actions.client().pendingApprovals(a.run_id);
  } catch {
    setNotice(a.id, { text: `decision may not have been sent: ${errText(cause)} · could not check the core`, tone: "red" });
    return a;
  }
  const fresh = list.find((x) => x.id === a.id);
  if (fresh) {
    actions.upsertApproval(fresh);
    setNotice(a.id, { text: `decision not sent: ${errText(cause)} · the core still shows it pending`, tone: "red" });
    return fresh;
  }
  // decided (perhaps by this very request) or expired on the core: this card can't decide again
  setIn(retired, a.id, true);
  setNotice(a.id, {
    text: "no answer from the core, and it no longer lists this as pending — it was decided or expired; see the log",
    tone: "blue",
  });
  return a;
}

/** Tests only. */
export function resetApprovalUi(): void {
  inflight.clear();
  resetOnce();
  deciding.value = new Set();
  notices.value = new Map();
  keptBranch.value = new Set();
  retired.value = new Set();
}

function setIn(s: Signal<ReadonlySet<string>>, id: string, on: boolean): void {
  if (s.peek().has(id) === on) return;
  const n = new Set(s.peek());
  if (on) n.add(id); else n.delete(id);
  s.value = n;
}

function setNotice(id: string, n: CardNotice | null): void {
  const cur = notices.peek();
  if (!n && !cur.has(id)) return;
  const m = new Map(cur);
  if (n) m.set(id, n); else m.delete(id);
  notices.value = m;
}

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));
