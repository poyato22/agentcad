// Pure log model for the agent console: steps (+ approvals, questions, live text, in-flight calls)
// → the rows the step log shows. Tool calls are paired with their result step by call_id, runs of
// ≥3 successful calls fold into "▸ N more tool calls", filters and the run/all scope apply here.
// Everything here is plain data; untrusted text stays a string and is rendered as text.
import type { AgentRun, Approval, Question, Step } from "../../api/types";
import { runLabel } from "../../lib/names";
import type { InflightCall } from "../../state/reduce";
import type { LogFilter } from "../../state/store";
import { isStudyCallName } from "./studycard/cardModel";
import { isTopoptCallName } from "../topopt/names";
import { isPartShowName } from "./cards/partcard/cardModel";

type CallItem = {
  kind: "call"; callId: string; name: string; input: Record<string, unknown>; result: Step | null;
  ok: boolean | null; summary: string | null; startedTs: number; endedTs: number | null;
  /** Hint from a failed result (gate MisesError shape), shown under the summary. */
  hint: string | null;
  /** Steps that happened inside this call and show on its row instead of as rows of their own
   *  (onshape_call ledger steps, other step kinds without a call_id). */
  attached: Step[];
};

type LogItemBody =
  | { kind: "message"; step: Step; role: "user" | "assistant"; text: string }
  | CallItem                                                                        // tool_call step + its result step
  | { kind: "fold"; calls: (CallItem & Labelled)[]; names: string[] }               // "▸ N more tool calls · a · b · c"
  | { kind: "diff"; step: Step; partId: string | null; vars: { name: string; prev: number | null; now: number }[];
      delta: { volume_mm3?: number; mass_g?: number; bbox_size?: number[] } }
  | { kind: "error"; step: Step; code: string; message: string; hint: string | null }
  | { kind: "file"; step: Step; callId: string | null; path: string; added: number; removed: number }  // ACP file edit
  | { kind: "decision"; step: Step } | { kind: "note"; text: string; ts: number; stepId: number | null }
  | { kind: "approval"; approval: Approval } | { kind: "question"; question: Question }
  | { kind: "live"; runId: string; text: string };                                   // streaming text_delta tail

type Labelled = {
  /** Set in the "all" scope: the run's label, shown before the row (keys then get a run prefix). */
  runLabel?: string;
  /** The run this row belongs to (set on every row built from a run). */
  runId?: string;
};

export type LogItem = LogItemBody & Labelled;

export interface BuildLogInput {
  runs: readonly AgentRun[];
  /** Labels of runs not in `runs` (the app's own "ui" run, which GET /runs leaves out). */
  extraLabels?: ReadonlyMap<string, string>;
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

/** Expansion key of a fold that starts at this call: "<run id>/<call id>". */
export function callFoldKey(call: { callId: string; runId?: string }): string {
  return `${call.runId ?? ""}/${call.callId}`;
}

/** A fold's key (for expandedFolds): its first folded call's callFoldKey. */
export function foldKey(item: Extract<LogItem, { kind: "fold" }>): string {
  return item.calls[0] ? callFoldKey(item.calls[0]) : "";
}

/** Stable key per row (Preact keys, scroll anchoring). In the "all" scope every key gets a run
 *  prefix (call ids like "c1" repeat across runs). */
export function itemKey(item: LogItem): string {
  let k: string;
  switch (item.kind) {
    case "call": k = `c:${item.callId}`; break;
    case "fold": k = `f:${item.calls[0]?.callId ?? ""}`; break;
    case "approval": k = `a:${item.approval.id}`; break;
    case "question": k = `q:${item.question.question_id}`; break;
    case "live": k = `l:${item.runId}`; break;
    case "note": k = item.stepId !== null ? `n:${item.stepId}` : `n:${item.ts}`; break;
    default: k = `s:${item.step.id}`;
  }
  return item.runLabel !== undefined && item.runId ? `${item.runId}/${k}` : k;
}

/** Where a step shows in the log: the row (index, key), or the fold that must open first. */
export type StepRowLocation = { index: number; key: string; unfold: string | null };

/**
 * The row that shows step `stepId` (one run's items, run scope). A step of a tool call (its
 * tool_call, result, measure or diff step) maps to the call's row; other steps to their own row
 * (message, error, decision, note), and steps attached to a call (onshape_call) to that call.
 * A call hidden in a fold returns the fold's index and key to open.
 */
export function findStepRow(items: readonly LogItem[], stepId: number, runSteps: readonly Step[]): StepRowLocation | null {
  const step = runSteps.find((s) => s.id === stepId);
  const cid = step && typeof step.payload.call_id === "string" ? step.payload.call_id : null;
  const at = (index: number, unfold: string | null = null): StepRowLocation => ({ index, key: itemKey(items[index]), unfold });
  if (cid) {
    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      if (it.kind === "call" && it.callId === cid) return at(i);
      if (it.kind === "fold" && it.calls.some((c) => c.callId === cid)) return at(i, foldKey(it));
    }
  }
  for (let i = 0; i < items.length; i++) {
    const it = items[i];
    if ("step" in it && it.step.id === stepId) return at(i);
    if (it.kind === "note" && it.stepId === stepId) return at(i);
    if (it.kind === "call" && (it.result?.id === stepId || it.attached.some((s) => s.id === stepId))) return at(i);
    if (it.kind === "fold" && it.calls.some((c) => c.result?.id === stepId || c.attached.some((s) => s.id === stepId))) return at(i, foldKey(it));
  }
  return null;
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

/** First line of the core's sketch procedure (sketch.py SKETCH_PROCEDURE), appended to a sketch
 *  run's first message after the user's words. The contract test pins it to the real core. */
export const SKETCH_PROCEDURE_START = "The sketch is in the <mises-context> block";

/** What the user typed: a sketch run's first message also carries the staged procedure Mises
 *  added for the model; the thread shows the user's words and a short note instead. */
export function userWords(p: Record<string, unknown>): string {
  const text = typeof p.text === "string" ? p.text : "";
  const ctx = p.context;
  if (typeof ctx !== "object" || ctx === null || !("sketch" in ctx)) return text;
  const at = text.indexOf(`\n\n${SKETCH_PROCEDURE_START}`);
  const words = at >= 0 ? text.slice(0, at) : text;
  return `${words}\n\n(sketch attached)`;
}

export const FILTER_KINDS: readonly LogFilter["kind"][] = ["everything", "decisions", "tool_calls", "files", "errors"];

// ---------------------------------------------------------------------------------------------

type Raw = { item: LogItem; ts: number; collapsed: boolean; runId: string };

function baseItems(input: BuildLogInput): Raw[] {
  const prevVars = diffPrevVars(input.steps);
  const perRun = scopeRuns(input).map((id) => runItems(id, input, prevVars));
  const merged = perRun.length === 1 ? perRun[0] : mergeByTs(perRun);
  for (const r of merged) r.item.runId = r.runId;
  if (input.filter.scope === "all") {
    const labels = new Map(input.runs.map((r) => [r.id, runLabel(r, input.runs)]));
    for (const r of merged) r.item.runLabel = labels.get(r.runId) ?? input.extraLabels?.get(r.runId) ?? "agent";
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

  // A call's result is its terminal step (tool_result / error / gate result kinds). ACP file-edit
  // diff steps (path/old_text/new_text) that arrive in between are rows of their own.
  const results = new Map<string, Step>();
  const calls = new Map<string, Step>();
  for (const s of steps) {
    const cid = s.payload.call_id;
    if (typeof cid !== "string") continue;
    if (s.kind === "tool_call") { if (!calls.has(cid)) calls.set(cid, s); }
    else if (!isAcpDiff(s) && !results.has(cid)) results.set(cid, s);
  }

  // Calls started and not finished at this point of the walk; onshape_call steps attach to the newest.
  const open: CallItem[] = [];
  let lastStarted: CallItem | null = null;
  const close = (cid: string) => {
    const i = open.findIndex((c) => c.callId === cid);
    if (i >= 0) open.splice(i, 1);
  };
  /** A step that is not a row: attach it to the enclosing call, else to the call shown just
   *  before it, else show a quiet note. Either way it never splits a run of calls. */
  const attachOrNote = (s: Step, text: string) => {
    const last = out[out.length - 1]?.item;
    // only the most recently started call can still be running; an older unfinished one (its
    // result never came) must not absorb steps of later calls
    const newest = open[open.length - 1];
    const host = (newest && newest === lastStarted ? newest : null) ?? (last && last.kind === "call" ? last : null);
    if (host) host.attached.push(s);
    else push({ kind: "note", text, ts: s.ts, stepId: s.id }, s.ts);
  };

  let lastMessage: string | null = null;
  for (const s of steps) {
    const p = s.payload;
    const cid = typeof p.call_id === "string" ? p.call_id : null;
    if (s.kind === "tool_call" && cid) {
      if (calls.get(cid) !== s) continue;
      const result = results.get(cid) ?? null;
      const item = callItem(cid, str(p.name) ?? "", rec(p.input), s.ts, result);
      push(item, s.ts, s.collapsed);
      lastStarted = item;
      if (result) {
        const extra = resultExtra(result, rec(p.input), prevVars);
        if (extra) push(extra, s.ts);
      }
      if (!result || result.id > s.id) open.push(item);   // open until the walk passes its result
      lastMessage = null;
      continue;
    }
    if (cid && isAcpDiff(s)) {
      push(fileItem(s, cid), s.ts);
      lastMessage = null;
      continue;
    }
    if (cid && s.kind !== "message") {
      if (calls.has(cid)) { if (results.get(cid) === s) close(cid); continue; }   // shown with its call
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
        const role = p.role === "user" ? "user" : "assistant";
        const text = role === "user" ? userWords(p) : str(p.text) ?? "";
        open.length = 0;                            // a new message: earlier calls are over
        if (!text.trim()) continue;                 // tool-use-only turn: its calls are the rows
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
        push({ kind: "note", text: n === null ? "compacted the conversation" : `compacted ${n} messages`, ts: s.ts, stepId: s.id }, s.ts);
        break;
      }
      case "diff": {
        if (typeof p.path === "string") { push(fileItem(s, null), s.ts); break; }
        const d = diffItem(s, {}, prevVars);
        if (d) push(d, s.ts);
        break;
      }
      default: {
        // onshape_call ({method, path, status, …}, no call_id) and any other kind
        const o = rec(p.output);
        const summary = str(o.summary) ?? str(p.summary);
        attachOrNote(s, summary ? `${s.kind} · ${summary}` : s.kind);
        continue;
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
  const base = { kind: "call" as const, callId, input, startedTs, attached: [] as Step[] };
  if (!result) return { ...base, name, result: null, ok: null, summary: null, hint: null, endedTs: null };
  const p = result.payload;
  const o = rec(p.output);
  let ok: boolean;
  let summary: string | null;
  if (result.kind === "error") {
    // gate MisesError {error, message, hint} or a failed ToolOutput {output: {ok: false, summary, data}}
    ok = false;
    summary = nonEmpty(p.message) ?? nonEmpty(o.summary) ?? nonEmpty(p.error);
  } else {
    // gate ToolOutput {output: {ok, summary}} or ACP {ok, status, output: <raw>, text}
    ok = o.ok !== false && p.ok !== false && p.status !== "failed";
    summary = nonEmpty(o.summary) ?? nonEmpty(p.summary) ?? nonEmpty(p.message) ?? nonEmpty(p.text) ?? nonEmpty(p.status);
  }
  return { ...base, name: name || (str(p.name) ?? ""), result, ok, summary, hint: nonEmpty(p.hint), endedTs: result.ts };
}

/** ACP file edit (acp_host/session.py _tool_content): a diff step with path/old_text/new_text, no ToolOutput. */
function isAcpDiff(s: Step): boolean {
  return s.kind === "diff" && typeof s.payload.path === "string" && s.payload.output === undefined;
}

function fileItem(s: Step, callId: string | null): LogItem {
  const { added, removed } = lineDelta(str(s.payload.old_text) ?? "", str(s.payload.new_text) ?? "");
  return { kind: "file", step: s, callId, path: str(s.payload.path) ?? "", added, removed };
}

/** Above this many cells the LCS table is skipped and lines are matched as a multiset. */
const LCS_MAX_CELLS = 4_000_000;

/** +/- line counts as a line diff reports them: lines outside the longest common subsequence
 *  (common head and tail trimmed first; a very large middle falls back to a multiset match). */
export function lineDelta(oldText: string, newText: string): { added: number; removed: number } {
  const lines = (t: string) => (t === "" ? [] : t.replace(/\n$/, "").split("\n"));
  const a = lines(oldText);
  const b = lines(newText);
  let head = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) head++;
  let tail = 0;
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++;
  const x = a.slice(head, a.length - tail);
  const y = b.slice(head, b.length - tail);
  if (x.length === 0 || y.length === 0) return { added: y.length, removed: x.length };
  let common: number;
  if (x.length * y.length <= LCS_MAX_CELLS) {
    let prev = new Uint32Array(y.length + 1);
    let cur = new Uint32Array(y.length + 1);
    for (let i = 1; i <= x.length; i++) {
      for (let j = 1; j <= y.length; j++) cur[j] = x[i - 1] === y[j - 1] ? prev[j - 1] + 1 : Math.max(prev[j], cur[j - 1]);
      [prev, cur] = [cur, prev];
    }
    common = prev[y.length];
  } else {
    const pool = new Map<string, number>();
    for (const l of x) pool.set(l, (pool.get(l) ?? 0) + 1);
    common = 0;
    for (const l of y) {
      const n = pool.get(l) ?? 0;
      if (n > 0) { pool.set(l, n - 1); common++; }
    }
  }
  return { added: y.length - common, removed: x.length - common };
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
  // what needs the human is never filtered away: pending approvals and open questions
  if (i.kind === "question") return true;
  if (i.kind === "approval" && i.approval.status === "pending") return true;
  switch (kind) {
    case "everything": return true;
    case "decisions": return i.kind === "decision" || i.kind === "approval"; // decided cards (open ones are above)
    case "tool_calls": return i.kind === "call" || i.kind === "fold" || i.kind === "diff" || i.kind === "file";
    case "files": return isFileItem(i);
    case "errors": return i.kind === "error" || (i.kind === "call" && i.ok === false);
  }
}

function isFileItem(i: LogItem): boolean {
  if (i.kind === "file") return true;
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
  // Onshape proposals change the real document: each stays a row of its own, never folded
  const foldable = (r: Raw | undefined) => r !== undefined && r.item.kind === "call" && r.item.ok === true
    && !r.item.name.replace(/^mcp__mises__/, "").startsWith("onshape_propose")
    && !isStudyCallName(r.item.name) // study / review cards stay in sight (Task A8)
    && !isTopoptCallName(r.item.name) // generative design cards too (Plan 9 D14)
    && !isPartShowName(r.item.name); // part cards stay in sight (Plan 7 R4)
  let i = 0;
  while (i < rows.length) {
    if (!foldable(rows[i])) { out.push(rows[i].item); i++; continue; }
    let j = i;
    while (foldable(rows[j]) && rows[j].runId === rows[i].runId) j++; // a fold never spans runs ("all" scope)
    const group = rows.slice(i, j);
    const folded = group.map((r, k) => (group.length >= 3 ? k > 0 : r.collapsed));
    let k = 0;
    while (k < group.length) {
      if (!folded[k]) { out.push(group[k].item); k++; continue; }
      let m = k;
      while (m < group.length && folded[m]) m++;
      const calls = group.slice(k, m).map((r) => r.item as CallItem & Labelled);
      if (expanded.has(callFoldKey(calls[0]))) out.push(...calls);
      else {
        const f: LogItem = { kind: "fold", calls, names: [...new Set(calls.map((c) => c.name))] };
        f.runId = calls[0].runId;
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

function nonEmpty(v: unknown): string | null { return typeof v === "string" && v.trim() !== "" ? v : null; }

function rec(v: unknown): Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function numRec(v: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [k, x] of Object.entries(rec(v))) if (typeof x === "number" && Number.isFinite(x)) out[k] = x;
  return out;
}
