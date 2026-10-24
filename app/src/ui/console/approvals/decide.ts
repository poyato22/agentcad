// Sending a decision for an approval card. One request in flight per approval (shared by every
// rendering of that card); never retried automatically. Maps the card's three choices onto the
// core's binary decide: Merge/Approve → approve:true; Keep branch and Reject → approve:false
// (the core doesn't delete branches on reject; "kept branch" is recorded in the UI only).
import { signal, type Signal } from "@preact/signals";
import { CoreError } from "../../../api/client";
import type { Approval } from "../../../api/types";
import { actions } from "../../../state/store";

export type Decision = "approve" | "keep_branch" | "reject";
export interface CardNotice { text: string; tone: "blue" | "red" }

/** Approval ids with a decide (or its stale refresh) in flight. */
export const deciding: Signal<ReadonlySet<string>> = signal<ReadonlySet<string>>(new Set());
/** Per-card notice ("proposal changed — review again", errors). */
export const notices: Signal<ReadonlyMap<string, CardNotice>> = signal<ReadonlyMap<string, CardNotice>>(new Map());
/** Rejections the user made with "Keep branch" (UI-only until the core records an outcome). */
export const keptBranch: Signal<ReadonlySet<string>> = signal<ReadonlySet<string>>(new Set());
/** Cards whose proposal was replaced by a different approval: they can no longer decide. */
export const retired: Signal<ReadonlySet<string>> = signal<ReadonlySet<string>>(new Set());

const inflight = new Map<string, Promise<Approval>>();

export const STALE_NOTICE = "proposal changed — review again";

export function isExpired(a: Approval, nowS: number = Date.now() / 1000): boolean {
  return a.status === "expired" || (a.status === "pending" && a.expires_at <= nowS);
}

/** Can this card still be decided (pending, not expired, not replaced)? */
export function canDecide(a: Approval, nowS?: number): boolean {
  return a.status === "pending" && !isExpired(a, nowS) && !retired.peek().has(a.id);
}

export function decide(a: Approval, action: Decision): Promise<Approval> {
  const running = inflight.get(a.id);
  if (running) return running;
  if (!canDecide(a)) return Promise.resolve(a);
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
    setNotice(a.id, { text: `decision not sent: ${errText(e)}`, tone: "red" });
    return a;
  }
  if (action === "keep_branch" && out.status === "rejected") setIn(keptBranch, a.id, true);
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

/** Tests only. */
export function resetApprovalUi(): void {
  inflight.clear();
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
