import { describe, expect, it } from "vitest";
import { applyRuns, emptyState, mergeApprovals, mergeSteps, reduce, applySteps, type AppState } from "./reduce";
import type { AgentRun, Approval, MisesEvent, Step } from "../api/types";

const RID = "b7c79a57cbb4";

function run(over: Partial<AgentRun> = {}): AgentRun {
  return {
    id: RID, parent_id: null, agent: "designer", provider: "fake", model: "fake-model",
    task: "thicken the mount plate to 6 mm", status: "running", branch: null, onshape_calls: 0,
    input_tokens: 0, output_tokens: 0, usd: 0, created_at: 1792869413, updated_at: 1792869413, ...over,
  };
}

function approval(over: Partial<Approval> = {}): Approval {
  return {
    id: "a332e73aa8f2", run_id: RID, action: "fixture_merge", payload: { part_id: "mount_plate" },
    payload_hash: "f".repeat(64), microversion: null, deltas: {}, status: "pending", expires_at: 1792871220,
    decided_by: null, decided_at: null, edited_payload: null, ...over,
  };
}

function step(id: number, kind: Step["kind"], payload: Record<string, unknown> = {}, runId = RID): Step {
  return { id, run_id: runId, ts: 1792869413 + id, kind, payload, evidence_hash: "e".repeat(64), prev_hash: null, collapsed: false };
}

function ev(type: string, data: Record<string, unknown> = {}, runId: string | null = RID): MisesEvent {
  return { type, run_id: runId, data, ts: 1792869420 };
}

function withRun(): AppState {
  return { ...emptyState(), runs: new Map([[RID, run()]]) };
}

describe("reduce", () => {
  it("tool_call_adds_inflight_and_fetchSteps", () => {
    const { state, effects } = reduce(withRun(), ev("tool_call", { call_id: "c2", name: "mirror_part_set_vars", input: { part_id: "mount_plate" } }));
    expect(state.inflight.get("c2")).toEqual({ run_id: RID, name: "mirror_part_set_vars", input: { part_id: "mount_plate" } });
    expect(effects).toContainEqual({ kind: "fetchSteps", runId: RID });
  });

  it("claude_codes_own_echo_of_a_mises_tool_call_adds_no_second_inflight_row", () => {
    // a Claude Code run: the Gate reports the mises call (c-gate); Claude's stream echoes the same
    // call under its own id and the mcp__mises__ name. Only the Gate's shows as in flight.
    let s = reduce(withRun(), ev("tool_call", { call_id: "toolu_1", name: "mcp__mises__mirror_part_list", source: "claude-code" })).state;
    expect(s.inflight.size).toBe(0);
    s = reduce(s, ev("tool_call", { call_id: "mcp_1", name: "mirror_part_list", input: {} })).state;
    expect([...s.inflight.keys()]).toEqual(["mcp_1"]);
    // its own subagent tool (not a mises tool) still shows while it runs
    s = reduce(s, ev("tool_call", { call_id: "toolu_2", name: "Task", source: "claude-code" })).state;
    expect(s.inflight.get("toolu_2")?.name).toBe("Task");
    s = reduce(s, ev("tool_result", { call_id: "toolu_2", ok: true, source: "claude-code" })).state;
    expect(s.inflight.has("toolu_2")).toBe(false);
  });

  it("tool_result_clears_inflight", () => {
    const s1 = reduce(withRun(), ev("tool_call", { call_id: "c3", name: "mirror_measure", input: {} })).state;
    const { state, effects } = reduce(s1, ev("tool_result", { call_id: "c3", name: "mirror_measure", ok: true, summary: "x" }));
    expect(state.inflight.has("c3")).toBe(false);
    expect(effects).toContainEqual({ kind: "fetchSteps", runId: RID });
    expect(effects).not.toContainEqual({ kind: "fetchParts" });
  });

  it("text_delta_appends_liveText", () => {
    let s = withRun();
    s = reduce(s, ev("text_delta", { text: "Thickened " })).state;
    s = reduce(s, ev("text_delta", { text: "the plate" })).state;
    expect(s.liveText.get(RID)).toBe("Thickened the plate");
  });

  it("liveText_cleared_when_assistant_message_step_lands", () => {
    let s = reduce(withRun(), ev("text_delta", { text: "hello" })).state;
    s = applySteps(s, RID, [step(11, "message", { role: "assistant", text: "hello" })]);
    expect(s.liveText.has(RID)).toBe(false);
  });

  it("approval_requested_upserts_and_fetchStatus", () => {
    const a = approval();
    const { state, effects } = reduce(withRun(), ev("approval_requested", a as unknown as Record<string, unknown>));
    expect(state.approvals.get(a.id)).toEqual(a);
    expect(effects).toContainEqual({ kind: "fetchStatus" });
    // guarantees a fetch that starts after the approval exists (an older in-flight one may be stale)
    expect(effects).toContainEqual({ kind: "fetchApprovals" });
  });

  it("run_finished_clears_that_runs_questions", () => {
    let s = withRun();
    s = reduce(s, ev("question_asked", { question_id: "q1", question: "Which side?", options: [] })).state;
    s = reduce(s, ev("question_asked", { question_id: "q2", question: "Other run?", options: [] }, "other")).state;
    s = reduce(s, ev("run_finished", { status: "cancelled", stop_reason: "cancelled", error: null, final_text: null })).state;
    expect([...s.questions.keys()]).toEqual(["q2"]);
  });

  it("applyRuns_terminal_status_clears_questions", () => {
    let s = reduce(withRun(), ev("question_asked", { question_id: "q1", question: "Which side?", options: [] })).state;
    s = applyRuns(s, [run({ status: "running" })]);
    expect(s.questions.has("q1")).toBe(true);
    s = applyRuns(s, [run({ status: "cancelled" })]);
    expect(s.questions.has("q1")).toBe(false);
  });

  it("empty_step_page_records_an_empty_list", () => {
    const s = applySteps(withRun(), RID, []);
    expect(s.steps.get(RID)).toEqual([]);
    // a second empty page changes nothing
    expect(applySteps(s, RID, [])).toBe(s);
  });

  it("malformed_approval_requested_refetches_instead", () => {
    const { state, effects } = reduce(withRun(), ev("approval_requested", { id: "x" }));
    expect(state.approvals.size).toBe(0);
    expect(effects).toContainEqual({ kind: "fetchApprovals" });
  });

  it("question_asked_adds_question", () => {
    const { state } = reduce(withRun(), ev("question_asked", { question_id: "q1", question: "Which side?", options: ["left", "right"] }));
    expect(state.questions.get("q1")).toEqual({ question_id: "q1", run_id: RID, question: "Which side?", options: ["left", "right"] });
  });

  it("question_answered_removes_it", () => {
    const s1 = reduce(withRun(), ev("question_asked", { question_id: "q1", question: "Which side?", options: [] })).state;
    const { state } = reduce(s1, ev("question_answered", { question_id: "q1" }));
    expect(state.questions.has("q1")).toBe(false);
  });

  it("run_started_unknown_run_triggers_fetchRuns", () => {
    const { effects } = reduce(emptyState(), ev("run_started", { agent: "designer", provider: "fake", model: "m", parent_id: null }, "newrun"));
    expect(effects).toContainEqual({ kind: "fetchRuns" });
    expect(effects).toContainEqual({ kind: "fetchSteps", runId: "newrun" });
  });

  it("run_started_resumed_sets_running", () => {
    const s = { ...emptyState(), runs: new Map([[RID, run({ status: "done" })]]) };
    const { state } = reduce(s, ev("run_started", { resumed: true }));
    expect(state.runs.get(RID)?.status).toBe("running");
  });

  it("run_finished_sets_status_and_fetchApprovals", () => {
    let s = withRun();
    s = reduce(s, ev("approval_requested", approval() as unknown as Record<string, unknown>)).state;
    s = reduce(s, ev("tool_call", { call_id: "c4", name: "fixture_merge", input: {} })).state;
    s = reduce(s, ev("text_delta", { text: "partial" })).state;
    const { state, effects } = reduce(s, ev("run_finished", { status: "cancelled", stop_reason: "cancelled", error: null, final_text: null }));
    expect(state.runs.get(RID)?.status).toBe("cancelled");
    expect(state.inflight.size).toBe(0);
    expect(state.liveText.has(RID)).toBe(false);
    expect(effects).toContainEqual({ kind: "fetchApprovals" });
    expect(effects).toContainEqual({ kind: "fetchSteps", runId: RID });
    // the refetch returns no pending approvals for the cancelled run → its card vanishes
    const after = mergeApprovals(state.approvals, []);
    expect(after.size).toBe(0);
  });

  it("mergeApprovals_keeps_decided_cards", () => {
    const decided = approval({ id: "d1", status: "approved", decided_by: "local-user", decided_at: 5 });
    const stalePending = approval({ id: "p1" });
    const fresh = approval({ id: "p2" });
    const m = mergeApprovals(new Map([[decided.id, decided], [stalePending.id, stalePending]]), [fresh]);
    expect([...m.keys()].sort()).toEqual(["d1", "p2"]);
  });

  it("mirror_tool_result_triggers_fetchParts", () => {
    for (const name of ["mirror_part_create", "mirror_part_set_vars"]) {
      const { effects } = reduce(withRun(), ev("tool_result", { call_id: "c", name, ok: true, summary: "s" }));
      expect(effects).toContainEqual({ kind: "fetchParts" });
    }
    const failed = reduce(withRun(), ev("tool_result", { call_id: "c", name: "mirror_part_set_vars", ok: false, error: "tool_failed", message: "m", hint: null, retryable: false }));
    expect(failed.effects).not.toContainEqual({ kind: "fetchParts" });
  });

  it("unknown_event_type_is_ignored", () => {
    const s = withRun();
    let out: ReturnType<typeof reduce> | undefined;
    expect(() => { out = reduce(s, ev("brand_new_event", { whatever: 1 })); }).not.toThrow();
    expect(out?.state).toBe(s);
    expect(out?.effects).toEqual([]);
  });

  it("malformed_known_event_does_not_throw", () => {
    const s = withRun();
    expect(() => reduce(s, ev("tool_call", { name: 5 }))).not.toThrow();
    expect(() => reduce(s, ev("text_delta", {}))).not.toThrow();
    expect(() => reduce(s, ev("question_asked", {}))).not.toThrow();
    expect(() => reduce(s, ev("run_finished", { status: "weird" }))).not.toThrow();
    expect(reduce(s, ev("text_delta", {}, null)).state).toBe(s);
  });

  it("note_and_compaction_refetch_steps", () => {
    for (const t of ["note", "compaction"]) {
      expect(reduce(withRun(), ev(t, { text: "x" })).effects).toContainEqual({ kind: "fetchSteps", runId: RID });
    }
  });
});

describe("mergeSteps", () => {
  it("merges_by_id_ascending_without_duplicates", () => {
    const a = [step(1, "message"), step(2, "tool_call"), step(3, "tool_result")];
    const b = [step(3, "tool_result"), step(4, "diff"), step(2, "tool_call")];
    expect(mergeSteps(a, b).map((s) => s.id)).toEqual([1, 2, 3, 4]);
  });
  it("returns_same_array_when_nothing_new", () => {
    const a = [step(1, "message")];
    expect(mergeSteps(a, [step(1, "message")])).toBe(a);
  });
  it("result_step_clears_stale_inflight", () => {
    let s = reduce(withRun(), ev("tool_call", { call_id: "c9", name: "mirror_measure", input: {} })).state;
    s = applySteps(s, RID, [step(5, "measure", { call_id: "c9", name: "mirror_measure", output: {} })]);
    expect(s.inflight.has("c9")).toBe(false);
  });
});
