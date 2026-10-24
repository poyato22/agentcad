// Test helpers: real-shaped steps, runs and approvals (shapes from test/fixtures/core-ui).
import type { AgentRun, Approval, Step, StepKind } from "../../api/types";
import { DEFAULT_FILTER, type LogFilter } from "../../state/store";
import type { BuildLogInput } from "./logModel";

let nextId = 1000;

export function resetIds(start = 1000): void { nextId = start; }

export function step(runId: string, kind: StepKind, payload: Record<string, unknown>, ts: number, extra: Partial<Step> = {}): Step {
  const id = extra.id ?? nextId++;
  return { id, run_id: runId, ts, kind, payload, evidence_hash: `h${id}`, prev_hash: null, collapsed: false, ...extra };
}

export function toolCall(runId: string, callId: string, name: string, input: Record<string, unknown>, ts: number, extra: Partial<Step> = {}): Step {
  return step(runId, "tool_call", { call_id: callId, name, input }, ts, extra);
}

export function toolResult(runId: string, callId: string, name: string, ts: number, opts: { ok?: boolean; summary?: string; kind?: StepKind; data?: Record<string, unknown>; handles?: string[] } = {}): Step {
  const kind = opts.kind ?? "tool_result";
  return step(runId, kind, {
    call_id: callId, name,
    output: { ok: opts.ok ?? true, summary: opts.summary ?? `${name} ok`, data: opts.data ?? {}, handles: opts.handles ?? [], warnings: [], step_kind: kind },
  }, ts);
}

/** Gate failure step (kind error, with call_id). */
export function toolFailure(runId: string, callId: string, name: string, ts: number, message = "it broke"): Step {
  return step(runId, "error", { call_id: callId, name, error: "tool_failed", message, hint: "try again", retryable: false }, ts);
}

export function message(runId: string, role: "user" | "assistant", text: string, ts: number): Step {
  return step(runId, "message", role === "assistant" ? { role, text, stop_reason: "end_turn" } : { role, text }, ts);
}

export function run(id: string, over: Partial<AgentRun> = {}): AgentRun {
  return {
    id, parent_id: null, agent: "designer", provider: "anthropic", model: "claude-sonnet", task: "stiffen the upright",
    status: "running", branch: null, onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0,
    created_at: 1792869400, updated_at: 1792869400, ...over,
  };
}

export function approval(id: string, runId: string, over: Partial<Approval> = {}): Approval {
  return {
    id, run_id: runId, action: "fixture_merge", payload: { part_id: "mount_plate" }, payload_hash: "f340f4e1", microversion: null,
    deltas: {}, status: "pending", expires_at: 4102444800, decided_by: null, decided_at: null, edited_payload: null, ...over,
  };
}

export function input(over: Partial<BuildLogInput> & { stepList?: Step[] } = {}): BuildLogInput {
  const { stepList, ...rest } = over;
  const steps = new Map<string, Step[]>();
  for (const s of stepList ?? []) {
    const l = steps.get(s.run_id) ?? [];
    l.push(s);
    steps.set(s.run_id, l);
  }
  for (const l of steps.values()) l.sort((a, b) => a.id - b.id);
  const filter: LogFilter = DEFAULT_FILTER;
  return {
    runs: [], steps, approvals: [], questions: [], liveText: new Map(), filter, selectedRunId: null, expandedFolds: new Set(),
    ...rest,
  };
}

/** n successful call+result pairs. */
export function pairs(runId: string, n: number, t0: number, prefix = "k", name = "mirror_measure"): Step[] {
  const out: Step[] = [];
  for (let i = 0; i < n; i++) {
    out.push(toolCall(runId, `${prefix}${i}`, name, { part_id: "mount_plate" }, t0 + i));
    out.push(toolResult(runId, `${prefix}${i}`, name, t0 + i + 0.5));
  }
  return out;
}
