// Pure log model for the agent console: steps (+ approvals, questions, live text, in-flight calls)
// → the rows the step log shows. Tool calls are paired with their result step by call_id, runs of
// ≥3 successful calls fold into "▸ N more tool calls", filters and the run/all scope apply here.
// Everything here is plain data; untrusted text stays a string and is rendered as text.
import type { AgentRun, Approval, Question, Step } from "../../api/types";
import { runLabel } from "../../lib/names";
import type { InflightCall } from "../../state/reduce";
import type { LogFilter } from "../../state/store";

type CallItem = {
  kind: "call"; callId: string; name: string; input: Record<string, unknown>; result: Step | null;
  ok: boolean | null; summary: string | null; startedTs: number; endedTs: number | null;
};

type LogItemBody =
  | { kind: "message"; step: Step; role: "user" | "assistant"; text: string }
  | CallItem                                                                        // tool_call step + its result step
  | { kind: "fold"; calls: (CallItem & Labelled)[]; names: string[] }               // "▸ N more tool calls · a · b · c"
  | { kind: "diff"; step: Step; partId: string | null; vars: { name: string; prev: number | null; now: number }[];
      delta: { volume_mm3?: number; mass_g?: number; bbox_size?: number[] } }
  | { kind: "error"; step: Step; code: string; message: string; hint: string | null }
  | { kind: "decision"; step: Step } | { kind: "note"; text: string; ts: number }
  | { kind: "approval"; approval: Approval } | { kind: "question"; question: Question }
  | { kind: "live"; runId: string; text: string };                                   // streaming text_delta tail

type Labelled = { /** Set in the "all" scope: the run's label, shown before the row. */ runLabel?: string };

export type LogItem = LogItemBody & Labelled;

export interface BuildLogInput {
  runs: readonly AgentRun[];
  steps: ReadonlyMap<string, readonly Step[]>;
  approvals: readonly Approval[];
  questions: readonly Question[];
  liveText: ReadonlyMap<string, string>;
  filter: LogFilter;
  selectedRunId: string | null;
  expandedFolds: ReadonlySet<string>;
  /** Calls whose tool_call event arrived before the step did (by call_id). */
  inflight?: ReadonlyMap<string, InflightCall>;
}

/** A fold's key (for expandedFolds): the call id of its first folded call. */
export function foldKey(item: Extract<LogItem, { kind: "fold" }>): string {
  return item.calls[0]?.callId ?? "";
}

/** Stable key per row (Preact keys, scroll anchoring). */
export function itemKey(item: LogItem): string {
  switch (item.kind) {
    case "call": return `c:${item.callId}`;
    case "fold": return `f:${foldKey(item)}`;
    case "approval": return `a:${item.approval.id}`;
    case "question": return `q:${item.question.question_id}`;
    case "live": return `l:${item.runId}`;
    case "note": return `n:${item.ts}`;
    default: return `s:${item.step.id}`;
  }
}

export function buildLog(input: BuildLogInput): LogItem[] {
  return fold(applyFilter(baseItems(input), input.filter.kind), input.expandedFolds);
}

export function filterCounts(input: BuildLogInput): Record<LogFilter["kind"], number> {
  return countsFrom(baseItems(input), input.expandedFolds);
}

export interface LogFrame {
  items: LogItem[];
  counts: Record<LogFilter["kind"], number>;
  /** Rows before folding: what the follow chip counts as "new". */
  unfolded: number;
}

/** Everything the console shows for one frame, from one pass over the steps. */
export function buildLogFrame(input: BuildLogInput): LogFrame {
  const base = baseItems(input);
  const shown = applyFilter(base, input.filter.kind);
  return { items: fold(shown, input.expandedFolds), counts: countsFrom(base, input.expandedFolds), unfolded: shown.length };
}

function countsFrom(base: Raw[], expanded: ReadonlySet<string>): Record<LogFilter["kind"], number> {
  const out = {} as Record<LogFilter["kind"], number>;
  for (const k of FILTER_KINDS) out[k] = fold(applyFilter(base, k), expanded).length;
  return out;
}

export const FILTER_KINDS: readonly LogFilter["kind"][] = ["everything", "decisions", "tool_calls", "files", "errors"];

// ---------------------------------------------------------------------------------------------

type Raw = { item: LogItem; ts: number; collapsed: boolean; runId: string };

function baseItems(input: BuildLogInput): Raw[] {
  const prevVars = diffPrevVars(input.steps);
  const perRun = scopeRuns(input).map((id) => runItems(id, input, prevVars));
  const merged = perRun.length === 1 ? perRun[0] : mergeByTs(perRun);
  if (input.filter.scope === "all") {
    const labels = new Map(input.runs.map((r) => [r.id, runLabel(r, input.runs)]));
    for (const r of merged) {
      const label = labels.get(r.runId);
      if (label) r.item.runLabel = label;
    }
  }
  return merged;
}

function scopeRuns(input: BuildLogInput): string[] {
  if (input.filter.scope === "run") return input.selectedRunId ? [input.selectedRunId] : [];
  const ids = new Set<string>();
  for (const r of input.runs) ids.add(r.id);
  for (const id of input.steps.keys()) ids.add(id);
  for (const a of input.approvals) ids.add(a.run_id);
  for (const q of input.questions) ids.add(q.run_id);
  return [...ids];
}

/** k-way merge of per-run lists by ts; stable (earlier run list wins ties). */
function mergeByTs(lists: Raw[][]): Raw[] {
  const out: Raw[] = [];
  const idx = lists.map(() => 0);
  for (;;) {
    let best = -1;
    for (let i = 0; i < lists.length; i++) {
      if (idx[i] >= lists[i].length) continue;
      if (best < 0 || lists[i][idx[i]].ts < lists[best][idx[best]].ts) best = i;
    }
    if (best < 0) return out;
    out.push(lists[best][idx[best]++]);
  }
}

function runItems(runId: string, input: BuildLogInput, prevVars: ReadonlyMap<number, Record<string, number>>): Raw[] {
  const steps = input.steps.get(runId) ?? [];
  const out: Raw[] = [];
  let clock = -Infinity;
  const push = (item: LogItem, ts: number, collapsed = false) => {
    clock = Math.max(clock, ts);  // keep each run's list non-decreasing for the merge
    out.push({ item, ts: clock, collapsed, runId });
  };

  const results = new Map<string, Step>();
  const calls = new Map<string, Step>();
  for (const s of steps) {
    const cid = s.payload.call_id;
    if (typeof cid !== "string") continue;
    if (s.kind === "tool_call") { if (!calls.has(cid)) calls.set(cid, s); }
    else if (!results.has(cid)) results.set(cid, s);
  }

  let lastMessage: string | null = null;
  for (const s of steps) {
    const p = s.payload;
    const cid = typeof p.call_id === "string" ? p.call_id : null;
    if (s.kind === "tool_call" && cid) {
      if (calls.get(cid) !== s) continue;
      const result = results.get(cid) ?? null;
      push(callItem(cid, str(p.name) ?? "", rec(p.input), s.ts, result), s.ts, s.collapsed);
      if (result) {
        const extra = resultExtra(result, rec(p.input), prevVars);
        if (extra) push(extra, s.ts);
      }
      lastMessage = null;
      continue;
    }
    if (cid && s.kind !== "message") {
      if (calls.has(cid)) continue;                 // shown with its call
      if (results.get(cid) !== s) continue;
      // orphan result: show as a call on its own
      push(callItem(cid, str(p.name) ?? "", {}, s.ts, s), s.ts, s.collapsed);
      const extra = resultExtra(s, {}, prevVars);
      if (extra) push(extra, s.ts);
      lastMessage = null;
      continue;
    }
    switch (s.kind) {
      case "message": {
        const text = str(p.text) ?? "";
        const role = p.role === "user" ? "user" : "assistant";
        if (!text.trim()) break;                    // tool-use-only turn: its calls are the rows
        push({ kind: "message", step: s, role, text }, s.ts);
        lastMessage = role === "assistant" ? text : null;
        continue;
      }
      case "error":
        push({ kind: "error", step: s, code: str(p.error) ?? str(p.code) ?? "error", message: str(p.message) ?? "", hint: str(p.hint) }, s.ts);
        break;
      case "decision": case "plan":
        push({ kind: "decision", step: s }, s.ts);
        break;
      case "compaction": {
        const n = typeof p.summarized_messages === "number" ? p.summarized_messages : null;
        push({ kind: "note", text: n === null ? "compacted the conversation" : `compacted ${n} messages`, ts: s.ts }, s.ts);
        break;
      }
      case "diff": {
        const d = diffItem(s, {}, prevVars);
        if (d) push(d, s.ts);
        break;
      }
      default: {
        const out = rec(p.output);
        const summary = str(out.summary) ?? str(p.summary);
        push({ kind: "note", text: summary ? `${s.kind} · ${summary}` : s.kind, ts: s.ts }, s.ts);
      }
    }
    lastMessage = null;
  }

  // tool_call events whose step has not been fetched yet
  for (const [cid, c] of input.inflight ?? []) {
    if (c.run_id !== runId || calls.has(cid) || results.has(cid)) continue;
    push(callItem(cid, c.name, c.input, clock === -Infinity ? 0 : clock, null), clock === -Infinity ? 0 : clock);
  }

  const live = input.liveText.get(runId);
  if (live && live !== lastMessage) push({ kind: "live", runId, text: live }, clock === -Infinity ? 0 : clock);

  const tail = clock === -Infinity ? 0 : clock;
  for (const a of input.approvals) if (a.run_id === runId) push({ kind: "approval", approval: a }, tail);
  for (const q of input.questions) if (q.run_id === runId) push({ kind: "question", question: q }, tail);
  return out;
}

function callItem(callId: string, name: string, input: Record<string, unknown>, startedTs: number, result: Step | null): CallItem {
  if (!result) return { kind: "call", callId, name, input, result: null, ok: null, summary: null, startedTs, endedTs: null };
  const p = result.payload;
  let ok: boolean;
  let summary: string | null;
  if (result.kind === "error") {
    ok = false;
    summary = str(p.message) ?? str(p.error) ?? null;
  } else {
    const o = rec(p.output);
    ok = o.ok !== false && p.ok !== false;
    summary = str(o.summary) ?? str(p.summary) ?? str(p.message) ?? null;
  }
  return { kind: "call", callId, name: name || (str(p.name) ?? ""), input, result, ok, summary, startedTs, endedTs: result.ts };
}

/** Extra row after a call: a diff box for diff results, a decision row for decision results. */
function resultExtra(result: Step, callInput: Record<string, unknown>, prevVars: ReadonlyMap<number, Record<string, number>>): LogItem | null {
  if (result.kind === "diff") return diffItem(result, callInput, prevVars);
  if (result.kind === "decision") return { kind: "decision", step: result };
  return null;
}

function diffItem(s: Step, callInput: Record<string, unknown>, prevVars: ReadonlyMap<number, Record<string, number>>): LogItem | null {
  const out = rec(s.payload.output);
  const data = rec(out.data);
  const partId = str(data.part_id) ?? str(callInput.part_id);
  const nowVars = { ...numRec(callInput.vars), ...numRec(data.vars) };
  const names = Object.keys(numRec(callInput.vars)).length ? Object.keys(numRec(callInput.vars)) : Object.keys(numRec(data.vars));
  const prev = prevVars.get(s.id) ?? {};
  const vars = names.filter((n) => typeof nowVars[n] === "number")
    .map((name) => ({ name, prev: typeof prev[name] === "number" ? prev[name] : null, now: nowVars[name] }));
  const d = rec(data.delta);
  const delta: { volume_mm3?: number; mass_g?: number; bbox_size?: number[] } = {};
  if (typeof d.volume_mm3 === "number") delta.volume_mm3 = d.volume_mm3;
  if (typeof d.mass_g === "number") delta.mass_g = d.mass_g;
  if (Array.isArray(d.bbox_size) && d.bbox_size.every((x) => typeof x === "number")) delta.bbox_size = d.bbox_size as number[];
  return { kind: "diff", step: s, partId, vars, delta };
}

/**
 * For every diff step: the part's variables just before it, across all runs, from successful
 * mirror_part_create / mirror_part_set_vars calls (input vars) and result data.vars, by time.
 */
function diffPrevVars(all: ReadonlyMap<string, readonly Step[]>): Map<number, Record<string, number>> {
  type Snap = { ts: number; id: number; part: string; vars: Record<string, number>; diff: boolean };
  const snaps: Snap[] = [];
  for (const steps of all.values()) {
    const callInputs = new Map<string, Record<string, unknown>>();
    for (const s of steps) {
      const cid = s.payload.call_id;
      if (typeof cid !== "string") continue;
      if (s.kind === "tool_call") { callInputs.set(cid, rec(s.payload.input)); continue; }
      const out = rec(s.payload.output);
      if (s.kind === "error" || out.ok === false) continue;
      const inp = callInputs.get(cid) ?? {};
      const data = rec(out.data);
      const part = str(data.part_id) ?? str(inp.part_id);
      const vars = { ...numRec(inp.vars), ...numRec(data.vars) };
      if (part && (Object.keys(vars).length || s.kind === "diff")) snaps.push({ ts: s.ts, id: s.id, part, vars, diff: s.kind === "diff" });
    }
  }
  snaps.sort((a, b) => a.ts - b.ts || a.id - b.id);
  const state = new Map<string, Record<string, number>>();
  const out = new Map<number, Record<string, number>>();
  for (const sn of snaps) {
    const before = state.get(sn.part) ?? {};
    if (sn.diff) out.set(sn.id, before);
    state.set(sn.part, { ...before, ...sn.vars });
  }
  return out;
}

function applyFilter(rows: Raw[], kind: LogFilter["kind"]): Raw[] {
  if (kind === "everything") return rows;
  return rows.filter((r) => matches(r.item, kind));
}

function matches(i: LogItem, kind: LogFilter["kind"]): boolean {
  switch (kind) {
    case "everything": return true;
    case "decisions": return i.kind === "decision" || i.kind === "approval" || i.kind === "question";
    case "tool_calls": return i.kind === "call" || i.kind === "fold" || i.kind === "diff";
    case "files": return isFileItem(i);
    case "errors": return i.kind === "error" || (i.kind === "call" && i.ok === false);
  }
}

function isFileItem(i: LogItem): boolean {
  if (i.kind === "call") {
    if (!i.result) return false;
    const out = rec(i.result.payload.output);
    if (Array.isArray(out.handles) && out.handles.length > 0) return true;
    return typeof i.result.payload.path === "string";
  }
  if (i.kind === "diff") return typeof i.step.payload.path === "string";
  return false;
}

/**
 * ≥3 consecutive successful calls (nothing else in between): the first stays, the rest fold.
 * A call whose step has collapsed = true always starts folded. Failed / running calls, diffs,
 * errors, approvals and questions never fold. An expanded fold (by foldKey) shows its calls.
 */
function fold(rows: Raw[], expanded: ReadonlySet<string>): LogItem[] {
  const out: LogItem[] = [];
  const foldable = (r: Raw | undefined) => r !== undefined && r.item.kind === "call" && r.item.ok === true;
  let i = 0;
  while (i < rows.length) {
    if (!foldable(rows[i])) { out.push(rows[i].item); i++; continue; }
    let j = i;
    while (foldable(rows[j])) j++;
    const group = rows.slice(i, j);
    const folded = group.map((r, k) => (group.length >= 3 ? k > 0 : r.collapsed));
    let k = 0;
    while (k < group.length) {
      if (!folded[k]) { out.push(group[k].item); k++; continue; }
      let m = k;
      while (m < group.length && folded[m]) m++;
      const calls = group.slice(k, m).map((r) => r.item as CallItem & Labelled);
      if (expanded.has(calls[0].callId)) out.push(...calls);
      else {
        const f: LogItem = { kind: "fold", calls, names: [...new Set(calls.map((c) => c.name))] };
        if (calls[0].runLabel) f.runLabel = calls[0].runLabel;
        out.push(f);
      }
      k = m;
    }
    i = j;
  }
  return out;
}

function str(v: unknown): string | null { return typeof v === "string" ? v : null; }

function rec(v: unknown): Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function numRec(v: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [k, x] of Object.entries(rec(v))) if (typeof x === "number" && Number.isFinite(x)) out[k] = x;
  return out;
}
