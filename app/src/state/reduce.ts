// Pure reducer: (state, core Event) → (new state, effects to run). No IO, never throws.
// Steps are the source of truth: events only patch transient state (live text, in-flight calls,
// approvals, questions, run status) and request refetches. See "Real core API" in the plan.
import { approvalSchema, questionAskedSchema } from "../api/schemas";
import type { AgentRun, Approval, MisesEvent, Question, RunStatus, Step } from "../api/types";
import { debug } from "../lib/log";

export type Effect = { kind: "fetchSteps"; runId: string } | { kind: "fetchRuns" } | { kind: "fetchApprovals" }
  | { kind: "fetchParts" } | { kind: "fetchStatus" }
  /** Seed live activity from GET /runs/{id} for runs still working (on load and reconnect, C-3). */
  | { kind: "fetchActivity"; runIds: string[]; finished: string[]; asOf: number };

export interface InflightCall { run_id: string; name: string; input: Record<string, unknown> }

export interface AppState {
  runs: ReadonlyMap<string, AgentRun>;
  steps: ReadonlyMap<string, readonly Step[]>;
  liveText: ReadonlyMap<string, string>;
  inflight: ReadonlyMap<string, InflightCall>;   // by call_id
  approvals: ReadonlyMap<string, Approval>;
  questions: ReadonlyMap<string, Question>;
}

export function emptyState(): AppState {
  return { runs: new Map(), steps: new Map(), liveText: new Map(), inflight: new Map(), approvals: new Map(), questions: new Map() };
}

/** Tools whose successful result changes the part list / GLB. */
export const PART_MUTATING_TOOLS: ReadonlySet<string> = new Set(["mirror_part_create", "mirror_part_set_vars", "study_apply"]);

const RUN_STATUSES: ReadonlySet<string> = new Set<RunStatus>(["running", "done", "failed", "cancelled"]);

/** Event types the UI knows; anything else is ignored. */
export const KNOWN_EVENTS: ReadonlySet<string> = new Set([
  "run_started", "run_queued", "run_finished", "tool_call", "tool_result", "approval_requested", "question_asked",
  "question_answered", "text_delta", "thought_delta", "note", "plan", "usage", "diff", "file_written",
  "turn_retry", "compaction", "part_changed", "run_deleted", "agent_activity", "agent_thinking",
  // Plan 7 U3c announcements (and Plan 8 G1's critique / view cards in the same format)
  "agent_announce",
]);

type Out = { state: AppState; effects: Effect[] };

export function reduce(s: AppState, e: MisesEvent): Out {
  try {
    return reduceUnsafe(s, e);
  } catch (err) {
    debug("reduce failed for", e.type, err);
    return { state: s, effects: [] };
  }
}

function reduceUnsafe(s: AppState, e: MisesEvent): Out {
  const d = e.data;
  const rid = e.run_id;
  const steps = (): Effect[] => (rid ? [{ kind: "fetchSteps", runId: rid }] : []);
  switch (e.type) {
    case "run_started": {
      if (!rid) return { state: s, effects: [] };
      const known = s.runs.get(rid);
      if (!known) return { state: s, effects: [{ kind: "fetchRuns" }, ...steps(), { kind: "fetchStatus" }] };
      const resumed: AgentRun = { ...known, status: "running" };
      const runs = known.status === "running" ? s.runs : setIn(s.runs, rid, resumed);
      return { state: { ...s, runs }, effects: [...steps(), { kind: "fetchStatus" }] };
    }
    case "run_queued": {
      // waiting for a parallel-agent slot (data: run_started's + position, limit, reason); its
      // run_started follows when a slot frees
      if (!rid) return { state: s, effects: [] };
      const known = s.runs.get(rid);
      if (!known) return { state: s, effects: [{ kind: "fetchRuns" }, { kind: "fetchStatus" }] };
      const runs = known.status === "queued" ? s.runs : setIn(s.runs, rid, { ...known, status: "queued" as const });
      return { state: { ...s, runs }, effects: [{ kind: "fetchStatus" }] };
    }
    case "run_finished": {
      if (!rid) return { state: s, effects: [] };
      let next: AppState = s;
      const known = s.runs.get(rid);
      const status = typeof d.status === "string" && RUN_STATUSES.has(d.status) ? (d.status as RunStatus) : null;
      if (known && status && known.status !== status) next = { ...next, runs: setIn(s.runs, rid, { ...known, status }) };
      next = clearRunTransients(next, rid);
      return { state: next, effects: [{ kind: "fetchRuns" }, ...steps(), { kind: "fetchApprovals" }, { kind: "fetchStatus" }] };
    }
    case "tool_call": {
      if (!rid || typeof d.call_id !== "string") return { state: s, effects: steps() };
      // a Claude Code run's own echo of a mises tool call: the Gate reports that call itself
      // (its own call_id, the plain tool name), so this would be a second in-flight row
      if (d.source === "claude-code" && typeof d.name === "string" && d.name.startsWith("mcp__mises__")) return { state: s, effects: steps() };
      const call: InflightCall = { run_id: rid, name: typeof d.name === "string" ? d.name : "", input: isRecord(d.input) ? d.input : {} };
      return { state: { ...s, inflight: setIn(s.inflight, d.call_id, call) }, effects: steps() };
    }
    case "tool_result": {
      const effects = steps();
      if (d.ok === true && typeof d.name === "string") {
        if (PART_MUTATING_TOOLS.has(d.name)) effects.push({ kind: "fetchParts" });
        if (d.name.startsWith("onshape_")) effects.push({ kind: "fetchStatus" });
      }
      if (typeof d.call_id !== "string" || !s.inflight.has(d.call_id)) return { state: s, effects };
      return { state: { ...s, inflight: deleteIn(s.inflight, d.call_id) }, effects };
    }
    case "approval_requested": {
      const a = approvalSchema.safeParse(d);
      if (!a.success) return { state: s, effects: [{ kind: "fetchApprovals" }, { kind: "fetchStatus" }] };
      // fetchApprovals too: a fetch already in flight may predate this approval and would drop it;
      // the runner's dirty re-run guarantees one more fetch that starts after now.
      return { state: { ...s, approvals: setIn(s.approvals, a.data.id, a.data) }, effects: [{ kind: "fetchStatus" }, { kind: "fetchApprovals" }] };
    }
    case "question_asked": {
      const q = questionAskedSchema.safeParse(d);
      if (!rid || !q.success) return { state: s, effects: [] };
      const question: Question = { question_id: q.data.question_id, run_id: rid, question: q.data.question, options: q.data.options,
        ...(q.data.questions.length ? { items: q.data.questions } : {}) };
      return { state: { ...s, questions: setIn(s.questions, question.question_id, question) }, effects: [] };
    }
    case "question_answered": {
      if (typeof d.question_id !== "string" || !s.questions.has(d.question_id)) return { state: s, effects: [] };
      return { state: { ...s, questions: deleteIn(s.questions, d.question_id) }, effects: [] };
    }
    case "text_delta": {
      if (!rid || typeof d.text !== "string" || d.text === "") return { state: s, effects: [] };
      return { state: { ...s, liveText: setIn(s.liveText, rid, (s.liveText.get(rid) ?? "") + d.text) }, effects: [] };
    }
    // Plan 7 (U3c): agent_announce and the lead's message_* events are also stored DECISION
    // steps the thread renders (an announcement card, a queued message and its delivery)
    case "note": case "compaction": case "plan": case "diff": case "file_written":
    case "agent_announce": case "message_queued": case "message_delivered": case "message_dropped":
      return { state: s, effects: steps() };
    case "agent_announce":
      // U3c / Plan 8: the announcement is also stored as a decision step; the thread draws its card
      return { state: s, effects: steps() };
    case "part_changed":
      // a user or agent edit (C6): new version, vars, metrics and GLB
      return { state: s, effects: [{ kind: "fetchParts" }] };
    case "run_deleted":
      // deleted from another window or by the core's Clean up (C-6); a no-op for unknown runs
      return { state: forgetRuns(s, deletedRunIds(e)), effects: [] };
    case "thought_delta": case "usage": case "turn_retry":
      return { state: s, effects: [] };
    case "agent_activity": case "agent_thinking":
      // their own subscribers keep them (state/activity.ts, state/thinking.ts)
      return { state: s, effects: [] };
    default:
      debug("ignoring unknown event type", e.type);
      return { state: s, effects: [] };
  }
}

/** Merge step pages by id (ascending, no duplicates). Returns `existing` itself if nothing is new. */
export function mergeSteps(existing: readonly Step[], incoming: readonly Step[]): readonly Step[] {
  if (incoming.length === 0) return existing;
  const lastId = existing.length ? existing[existing.length - 1].id : -Infinity;
  const seen = new Set(existing.map((x) => x.id));
  const fresh = incoming.filter((x) => !seen.has(x.id));
  if (fresh.length === 0) return existing;
  const dedup = new Map(fresh.map((x) => [x.id, x]));
  const sorted = [...dedup.values()].sort((a, b) => a.id - b.id);
  if (sorted[0].id > lastId) return [...existing, ...sorted];
  return [...existing, ...sorted].sort((a, b) => a.id - b.id);
}

/**
 * Apply fetched steps for a run. Also: an assistant `message` step landing clears that run's
 * live text (the streamed text is now a real row), and a result step for a call clears a stale
 * in-flight entry (in case its tool_result event was dropped).
 */
export function applySteps(s: AppState, runId: string, incoming: readonly Step[]): AppState {
  const known = s.steps.get(runId);
  // record "fetched, zero steps" so fetchRuns doesn't keep re-queuing this run
  if (!known && incoming.length === 0) return { ...s, steps: setIn(s.steps, runId, []) };
  const prev = known ?? [];
  const merged = mergeSteps(prev, incoming);
  if (merged === prev) return s;
  const prevIds = new Set(prev.map((x) => x.id));
  const fresh = merged.filter((x) => !prevIds.has(x.id));
  let next: AppState = { ...s, steps: setIn(s.steps, runId, merged) };
  if (fresh.some((x) => x.kind === "message" && x.payload.role === "assistant") && next.liveText.has(runId)) {
    next = { ...next, liveText: deleteIn(next.liveText, runId) };
  }
  const done = fresh.filter((x) => x.kind !== "tool_call" && typeof x.payload.call_id === "string")
    .map((x) => x.payload.call_id as string).filter((id) => next.inflight.has(id));
  if (done.length) {
    const inflight = new Map(next.inflight);
    for (const id of done) inflight.delete(id);
    next = { ...next, inflight };
  }
  return next;
}

/** Upsert runs from GET /runs. Runs that stopped running lose their in-flight calls and live text. */
export function applyRuns(s: AppState, list: readonly AgentRun[]): AppState {
  if (list.length === 0) return s;
  const runs = new Map(s.runs);
  let next: AppState = s;
  for (const r of list) {
    runs.set(r.id, r);
    if (r.status !== "running") next = clearRunTransients(next, r.id);
  }
  return { ...next, runs };
}

/**
 * Replace the pending set with the server's list (GET /approvals?status=pending) while keeping
 * cards the UI already saw decided (approved/rejected/…), so a decided card can show its outcome.
 * A pending card the server no longer lists (run cancelled, expired) disappears.
 */
export function mergeApprovals(current: ReadonlyMap<string, Approval>, pending: readonly Approval[]): ReadonlyMap<string, Approval> {
  const out = new Map<string, Approval>();
  for (const [id, a] of current) if (a.status !== "pending") out.set(id, a);
  for (const a of pending) if (!out.has(a.id) || a.status === "pending") out.set(a.id, a);
  return out;
}

/**
 * A run that stopped running loses its live text, in-flight calls and open questions (the core
 * drops a pending question on cancel without emitting question_answered).
 */
function clearRunTransients(s: AppState, runId: string): AppState {
  let next = s;
  if (next.liveText.has(runId)) next = { ...next, liveText: deleteIn(next.liveText, runId) };
  const qs = [...next.questions.values()].filter((q) => q.run_id === runId).map((q) => q.question_id);
  if (qs.length) {
    const questions = new Map(next.questions);
    for (const id of qs) questions.delete(id);
    next = { ...next, questions };
  }
  const calls = [...next.inflight].filter(([, c]) => c.run_id === runId).map(([id]) => id);
  if (calls.length) {
    const inflight = new Map(next.inflight);
    for (const id of calls) inflight.delete(id);
    next = { ...next, inflight };
  }
  return next;
}

/**
 * Drop runs the core no longer has (deleted, cleaned up): the run, its steps, live text,
 * in-flight calls, questions and approval cards. Returns `s` itself when none of them is known.
 */
export function forgetRuns(s: AppState, ids: Iterable<string>): AppState {
  const gone = new Set([...ids].filter((id) => s.runs.has(id) || s.steps.has(id)));
  if (gone.size === 0) return s;
  let next = s;
  for (const id of gone) next = clearRunTransients(next, id);
  const runs = new Map(next.runs);
  const steps = new Map(next.steps);
  for (const id of gone) { runs.delete(id); steps.delete(id); }
  const approvals = new Map([...next.approvals].filter(([, a]) => !gone.has(a.run_id)));
  return { ...next, runs, steps, approvals: approvals.size === next.approvals.size ? next.approvals : approvals };
}

/**
 * The core's open questions per run (GET /runs/{id} on connect, C-2): its questions are added,
 * and a question we had before the fetch began that it no longer lists is dropped (answered
 * elsewhere). One that arrived (question_asked) while the fetch was in flight is kept: the
 * answer may predate it.
 */
export function applyPendingQuestions(s: AppState, byRun: ReadonlyMap<string, readonly Question[]>, knownBefore: ReadonlySet<string>): AppState {
  const listed = new Set([...byRun.values()].flatMap((l) => l.map((q) => q.question_id)));
  const questions = new Map([...s.questions].filter(([id, q]) => !(byRun.has(q.run_id) && knownBefore.has(id) && !listed.has(id))));
  // a question we had before the fetch and no longer have was answered meanwhile: not re-added
  for (const list of byRun.values()) for (const q of list) if (!questions.has(q.question_id) && !knownBefore.has(q.question_id)) questions.set(q.question_id, q);
  return { ...s, questions };
}

/** The run ids a `run_deleted` event names (`run_id`, or `data.run_id` / `data.run_ids`). */
export function deletedRunIds(e: MisesEvent): string[] {
  const out = new Set<string>();
  if (e.run_id) out.add(e.run_id);
  if (typeof e.data.run_id === "string") out.add(e.data.run_id);
  if (Array.isArray(e.data.run_ids)) for (const x of e.data.run_ids) if (typeof x === "string") out.add(x);
  return [...out];
}

function setIn<K, V>(m: ReadonlyMap<K, V>, k: K, v: V): ReadonlyMap<K, V> {
  const n = new Map(m);
  n.set(k, v);
  return n;
}

function deleteIn<K, V>(m: ReadonlyMap<K, V>, k: K): ReadonlyMap<K, V> {
  const n = new Map(m);
  n.delete(k);
  return n;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
