import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import type { Step } from "../../api/types";
import { buildLog, filterCounts, itemKey, lineDelta, SKETCH_PROCEDURE_START, userWords, type LogItem } from "./logModel";
import { approval, input, message, pairs, resetIds, run, step, toolCall, toolFailure, toolResult } from "./testSteps";

const FIXTURE_STEPS = JSON.parse(readFileSync(resolve(__dirname, "../../../test/fixtures/core-ui/steps.json"), "utf8")) as Step[];
const FIXTURE_RUN = FIXTURE_STEPS[0].run_id;

const kinds = (items: LogItem[]) => items.map((i) => i.kind);
type Call = Extract<LogItem, { kind: "call" }>;

beforeEach(() => resetIds());

describe("lineDelta", () => {
  it("counts_like_a_line_diff_moves_included", () => {
    expect(lineDelta("a\nb\nc\n", "a\nB\nc\n")).toEqual({ added: 1, removed: 1 });
    expect(lineDelta("a\nb\nc", "c\na\nb")).toEqual({ added: 1, removed: 1 }); // a moved line is -1 +1
    expect(lineDelta("x\nx\ny", "y\nx\nx")).toEqual({ added: 1, removed: 1 });
    expect(lineDelta("", "a\nb")).toEqual({ added: 2, removed: 0 });
    expect(lineDelta("a\nb", "")).toEqual({ added: 0, removed: 2 });
    expect(lineDelta("same\n", "same\n")).toEqual({ added: 0, removed: 0 });
  });

  it("huge_inputs_stay_bounded", () => {
    const big = Array.from({ length: 5000 }, (_, i) => `line ${i}`).join("\n");
    const t0 = performance.now();
    const d = lineDelta(big, big + "\nextra");
    expect(performance.now() - t0).toBeLessThan(500);
    expect(d).toEqual({ added: 1, removed: 0 });
  });
});

describe("buildLog", () => {
  it("pairs_call_with_result_by_call_id", () => {
    const items = buildLog(input({ stepList: FIXTURE_STEPS, selectedRunId: FIXTURE_RUN, runs: [run(FIXTURE_RUN)] }));
    const calls = items.filter((i): i is Call => i.kind === "call");
    expect(calls.map((c) => c.callId)).toEqual(["c1", "c2", "c3", "c4"]);
    const c1 = calls[0];
    expect(c1.name).toBe("mirror_part_create");
    expect(c1.input.part_id).toBe("mount_plate");
    expect(c1.result?.id).toBe(4);
    expect(c1.ok).toBe(true);
    expect(c1.summary).toContain("mount_plate: volume 3861.5 mm3");
    expect(c1.startedTs).toBe(FIXTURE_STEPS[2].ts);
    expect(c1.endedTs).toBe(FIXTURE_STEPS[3].ts);
    // c4 (fixture_merge) has no result yet
    expect(calls[3].result).toBeNull();
    expect(calls[3].ok).toBeNull();
    expect(calls[3].endedTs).toBeNull();
    // tool-use-only assistant messages (empty text) are not rows; the user message and the final text are
    const msgs = items.filter((i) => i.kind === "message").map((i) => (i.kind === "message" ? i.text : ""));
    expect(msgs).toEqual(["thicken the mount plate to 6 mm", "Thickened the plate to 6 mm; asking to merge."]);
  });

  it("folds_three_or_more_consecutive_calls", () => {
    const items = buildLog(input({ stepList: pairs("r1", 5, 100), selectedRunId: "r1" }));
    expect(kinds(items)).toEqual(["call", "fold"]);
    const fold = items[1];
    if (fold.kind !== "fold") throw new Error("no fold");
    expect(fold.calls).toHaveLength(4);
    expect(fold.calls.map((c) => c.callId)).toEqual(["k1", "k2", "k3", "k4"]);
    expect(fold.names).toEqual(["mirror_measure"]);
    // two consecutive calls never fold
    expect(kinds(buildLog(input({ stepList: pairs("r2", 2, 100), selectedRunId: "r2" })))).toEqual(["call", "call"]);
  });

  it("fold_names_are_distinct_in_order", () => {
    const s = [...pairs("r1", 1, 100, "a", "mirror_measure"), ...pairs("r1", 2, 110, "b", "check_min_wall"), ...pairs("r1", 1, 120, "c", "check_overhang")];
    const items = buildLog(input({ stepList: s, selectedRunId: "r1" }));
    const fold = items[1];
    if (fold.kind !== "fold") throw new Error("no fold");
    expect(fold.names).toEqual(["check_min_wall", "check_overhang"]);
  });

  it("never_folds_failed_call_or_diff", () => {
    const s: Step[] = [
      ...pairs("r1", 2, 100, "a"),
      toolCall("r1", "bad", "mirror_part_set_vars", { part_id: "p", vars: { t: 1 } }, 110),
      toolFailure("r1", "bad", "mirror_part_set_vars", 111),
      ...pairs("r1", 2, 120, "b"),
      toolCall("r1", "d", "mirror_part_set_vars", { part_id: "p", vars: { t: 2 } }, 130),
      toolResult("r1", "d", "mirror_part_set_vars", 131, { kind: "diff", data: { part_id: "p", vars: { t: 2 }, delta: { volume_mm3: 1, mass_g: 0.1, bbox_size: [0, 0, 1] } } }),
      ...pairs("r1", 2, 140, "c"),
    ];
    const items = buildLog(input({ stepList: s, selectedRunId: "r1" }));
    // a0 a1 | bad | b0 b1 d(call) | diff | c0 c1 → the b-run is b0 b1 d = 3 successful calls → b0 + fold(b1, d)
    expect(kinds(items)).toEqual(["call", "call", "call", "call", "fold", "diff", "call", "call"]);
    const bad = items[2] as Call;
    expect(bad.callId).toBe("bad");
    expect(bad.ok).toBe(false);
    expect(bad.summary).toBe("it broke");
  });

  it("respects_step_collapsed_flag", () => {
    const s: Step[] = [
      toolCall("r1", "x", "mirror_measure", {}, 100),
      toolResult("r1", "x", "mirror_measure", 101),
      toolCall("r1", "y", "mirror_measure", {}, 102, { collapsed: true }),
      toolResult("r1", "y", "mirror_measure", 103),
    ];
    const items = buildLog(input({ stepList: s, selectedRunId: "r1" }));
    expect(kinds(items)).toEqual(["call", "fold"]);
    const fold = items[1];
    if (fold.kind !== "fold") throw new Error("no fold");
    expect(fold.calls.map((c) => c.callId)).toEqual(["y"]);
    // expanded by its key (first folded call id) it shows
    expect(kinds(buildLog(input({ stepList: s, selectedRunId: "r1", expandedFolds: new Set(["r1/y"]) })))).toEqual(["call", "call"]);
  });

  it("expanded_fold_shows_all", () => {
    const s = pairs("r1", 5, 100);
    const items = buildLog(input({ stepList: s, selectedRunId: "r1", expandedFolds: new Set(["r1/k1"]) }));
    expect(kinds(items)).toEqual(["call", "call", "call", "call", "call"]);
  });

  it("diff_item_from_set_vars", () => {
    const items = buildLog(input({ stepList: FIXTURE_STEPS, selectedRunId: FIXTURE_RUN }));
    const diff = items.find((i) => i.kind === "diff");
    if (!diff || diff.kind !== "diff") throw new Error("no diff");
    expect(diff.step.id).toBe(8);   // after U3c's part_created announcement (step 5)
    expect(diff.partId).toBe("mount_plate");
    expect(diff.vars).toEqual([{ name: "thickness", prev: 5, now: 6 }]);
    // the mass delta is the tree mass (volume x the part's material), rounded like it (Plan 9 B2)
    expect(diff.delta).toEqual({ bbox_size: [0, 0, 1], mass_g: 0.9576, volume_mm3: 772.2911527953379 });
    // order: … call c2, diff, call c3 …
    const i = items.indexOf(diff);
    expect((items[i - 1] as Call).callId).toBe("c2");
  });

  it("diff_prev_is_null_without_an_earlier_value", () => {
    const s = [
      toolCall("r1", "d", "mirror_part_set_vars", { part_id: "bracket", vars: { wall: 3 } }, 130),
      toolResult("r1", "d", "mirror_part_set_vars", 131, { kind: "diff", data: { part_id: "bracket", vars: { wall: 3 }, delta: { mass_g: 1 } } }),
    ];
    const diff = buildLog(input({ stepList: s, selectedRunId: "r1" })).find((i) => i.kind === "diff");
    expect(diff && diff.kind === "diff" && diff.vars).toEqual([{ name: "wall", prev: null, now: 3 }]);
  });

  it("filter_counts_match_items", () => {
    const s: Step[] = [
      ...FIXTURE_STEPS,
      toolCall(FIXTURE_RUN, "e1", "mirror_export", { part_id: "mount_plate" }, 1792869421),
      toolResult(FIXTURE_RUN, "e1", "mirror_export", 1792869422, { handles: ["/tmp/mount_plate/model.glb"] }),
      toolCall(FIXTURE_RUN, "f1", "mirror_measure", {}, 1792869423),
      toolFailure(FIXTURE_RUN, "f1", "mirror_measure", 1792869424),
      step(FIXTURE_RUN, "decision", { skipped_tools: ["onshape_merge"], reason: "needs approval" }, 1792869425),
      step(FIXTURE_RUN, "error", { stop_reason: "error", error: "transient", message: "rate limited", hint: "wait", retryable: true }, 1792869426),
      ...pairs(FIXTURE_RUN, 3, 1792869430),
    ];
    const base = input({ stepList: s, selectedRunId: FIXTURE_RUN, runs: [run(FIXTURE_RUN)], approvals: [approval("a1", FIXTURE_RUN)] });
    const counts = filterCounts(base);
    for (const kind of ["everything", "decisions", "tool_calls", "files", "errors"] as const) {
      expect(counts[kind]).toBe(buildLog({ ...base, filter: { scope: "run", kind } }).length);
    }
    // the pending approval shows under every filter (it needs the human)
    expect(counts.errors).toBe(3);
    expect(counts.files).toBe(2);
    expect(counts.decisions).toBe(3); // U3c's announcement + decision step + pending approval
    const tc = buildLog({ ...base, filter: { scope: "run", kind: "tool_calls" } });
    expect(new Set(kinds(tc))).toEqual(new Set(["call", "fold", "diff", "approval"]));
  });

  it("pending_approvals_and_open_questions_show_under_every_filter", () => {
    const s: Step[] = [toolCall("r1", "c1", "mirror_measure", {}, 100), toolResult("r1", "c1", "mirror_measure", 101)];
    const q = { question_id: "q1", run_id: "r1", question: "which side?", options: ["left"] };
    const base = input({
      stepList: s, selectedRunId: "r1", runs: [run("r1")], questions: [q],
      approvals: [approval("live", "r1"), approval("done", "r1", { status: "approved" })],
    });
    for (const kind of ["everything", "decisions", "tool_calls", "files", "errors"] as const) {
      const items = buildLog({ ...base, filter: { scope: "run", kind } });
      const keys = items.map(itemKey);
      expect(keys, kind).toContain("a:live");
      expect(keys, kind).toContain("q:q1");
      // a decided card is history: it follows the filter (decisions / everything only)
      expect(keys.includes("a:done"), kind).toBe(kind === "everything" || kind === "decisions");
    }
  });

  it("errors_filter_includes_failed_tool_results", () => {
    const s: Step[] = [
      ...pairs("r1", 3, 100),
      toolCall("r1", "f", "mirror_measure", {}, 110),
      toolResult("r1", "f", "mirror_measure", 111, { ok: false, summary: "no such part" }),
      toolCall("r1", "g", "mirror_measure", {}, 112),
      toolFailure("r1", "g", "mirror_measure", 113, "boom"),
    ];
    const items = buildLog(input({ stepList: s, selectedRunId: "r1", filter: { scope: "run", kind: "errors" } }));
    expect(items.map((i) => (i.kind === "call" ? `${i.callId}:${i.summary}` : i.kind))).toEqual(["f:no such part", "g:boom"]);
  });

  it("all_scope_interleaves_by_ts_with_labels", () => {
    const runs = [run("r1", { task: "stiffen the upright" }), run("r2", { agent: "simulator", task: "fea on three envs" })];
    const s = [message("r1", "user", "one", 100), message("r2", "user", "two", 101), message("r1", "assistant", "three", 102)];
    const items = buildLog(input({ stepList: s, runs, filter: { scope: "all", kind: "everything" }, selectedRunId: "r1" }));
    expect(items.map((i) => (i.kind === "message" ? `${i.runLabel} ${i.text}` : i.kind))).toEqual([
      "designer · stiffen upright one", "simulator · fea on three envs two", "designer · stiffen upright three",
    ]);
    // run scope shows only the selected run, no label
    const one = buildLog(input({ stepList: s, runs, selectedRunId: "r2" }));
    expect(one.map((i) => (i.kind === "message" ? i.text : ""))).toEqual(["two"]);
    expect(one[0].runLabel).toBeUndefined();
  });

  it("approval_after_latest_run_step", () => {
    const runs = [run("r1"), run("r2", { agent: "simulator" })];
    const s = [message("r1", "user", "a", 100), message("r2", "user", "b", 101), message("r1", "assistant", "c", 102), message("r2", "assistant", "d", 103)];
    const approvals = [approval("ap", "r1")];
    const questions = [{ question_id: "q1", run_id: "r2", question: "which env?", options: ["desk"] }];
    const all = buildLog(input({ stepList: s, runs, approvals, questions, filter: { scope: "all", kind: "everything" } }));
    expect(all.map((i) => (i.kind === "message" ? i.text : i.kind))).toEqual(["a", "b", "c", "approval", "d", "question"]);
    const one = buildLog(input({ stepList: s, runs, approvals, questions, selectedRunId: "r1" }));
    expect(kinds(one)).toEqual(["message", "message", "approval"]);
  });

  it("live_text_tail_and_no_duplicate_after_message_lands", () => {
    const s = [message("r1", "user", "go", 100)];
    const live = new Map([["r1", "Thickening the pl"]]);
    const items = buildLog(input({ stepList: s, selectedRunId: "r1", liveText: live }));
    expect(items[items.length - 1]).toEqual({ kind: "live", runId: "r1", text: "Thickening the pl" });
    // the message step landed but live text was not cleared yet → one row, not two
    const landed = [...s, message("r1", "assistant", "Thickening the pl", 101)];
    const after = buildLog(input({ stepList: landed, selectedRunId: "r1", liveText: live }));
    expect(kinds(after)).toEqual(["message", "message"]);
  });

  it("errors_and_decisions_and_notes_from_real_payload_shapes", () => {
    const s = [
      step("r1", "error", { stop_reason: "error", error: "budget_exhausted", message: "out of tokens", hint: "raise the cap", retryable: false }, 100),
      step("r1", "error", { code: "acp_error", message: "agent crashed" }, 101),
      step("r1", "plan", { entries: [{ content: "add gusset" }] }, 102),
      step("r1", "compaction", { summarized_messages: 12, est_tokens_before: 90000 }, 103),
    ];
    const items = buildLog(input({ stepList: s, selectedRunId: "r1" }));
    expect(items[0]).toMatchObject({ kind: "error", code: "budget_exhausted", message: "out of tokens", hint: "raise the cap" });
    expect(items[1]).toMatchObject({ kind: "error", code: "acp_error", message: "agent crashed", hint: null });
    expect(items[2]).toMatchObject({ kind: "decision" });
    expect(items[3]).toMatchObject({ kind: "note", text: "compacted 12 messages", ts: 103 });
  });

  it("inflight_call_without_step_shows_as_running_call", () => {
    const s = [message("r1", "user", "go", 100)];
    const items = buildLog({ ...input({ stepList: s, selectedRunId: "r1" }), inflight: new Map([["z1", { run_id: "r1", name: "mirror_measure", input: { part_id: "p" } }]]) });
    expect(items[1]).toMatchObject({ kind: "call", callId: "z1", name: "mirror_measure", result: null, ok: null });
  });
});

// Fix round 1: real core step sequences.
describe("buildLog on real core sequences", () => {
  it("failed_tool_output_error_step_keeps_reason", () => {
    // core cad/tools.py _fail → gate._finish: kind = output.step_kind = "error", payload {call_id, name, output}
    const failedOutput = step("r1", "error", {
      call_id: "c1", name: "mirror_part_create",
      output: { ok: false, summary: "build failed: Box() missing 1 required positional argument", data: { stdout_tail: "Traceback …" }, handles: [], warnings: [], step_kind: "error" },
    }, 101);
    // core gate.py MisesError: kind "error", payload {call_id, name, error, message, hint, retryable}
    const misesError = step("r1", "error", { call_id: "c2", name: "mirror_part_set_vars", error: "out_of_scope", message: "part_id='bracket' is outside this run's scope", hint: "ask the user to widen the scope", retryable: false }, 103);
    const s = [
      toolCall("r1", "c1", "mirror_part_create", { part_id: "mount_plate" }, 100), failedOutput,
      toolCall("r1", "c2", "mirror_part_set_vars", { part_id: "bracket" }, 102), misesError,
    ];
    const items = buildLog(input({ stepList: s, selectedRunId: "r1" }));
    expect(items).toHaveLength(2);
    expect(items[0]).toMatchObject({ kind: "call", ok: false, summary: "build failed: Box() missing 1 required positional argument", hint: null });
    expect(items[1]).toMatchObject({ kind: "call", ok: false, summary: "part_id='bracket' is outside this run's scope", hint: "ask the user to widen the scope" });
    const errs = buildLog(input({ stepList: s, selectedRunId: "r1", filter: { scope: "run", kind: "errors" } }));
    expect(errs).toHaveLength(2);
  });

  // core acp_host/session.py: tool_call → diff step(s) {call_id, path, old_text, new_text, source} → tool_result {ok, status, output, text}
  const acpCall = (cid: string, ts: number) => step("r1", "tool_call", { call_id: cid, name: "Edit part.py", kind: "edit", status: "pending", input: { file_path: "/w/part.py" }, source: "acp" }, ts);
  const acpDiff = (cid: string, path: string, oldText: string, newText: string, ts: number) =>
    step("r1", "diff", { call_id: cid, path, old_text: oldText, new_text: newText, source: "acp" }, ts);
  const acpResult = (cid: string, status: "completed" | "failed", ts: number, text = "") =>
    step("r1", "tool_result", { call_id: cid, name: "Edit part.py", status, ok: status === "completed", output: null, text, source: "acp" }, ts);

  it("acp_call_pairs_with_its_tool_result_and_diffs_are_file_rows", () => {
    const s = [
      acpCall("t1", 100),
      acpDiff("t1", "/w/part.py", "a\nb\n", "a\nc\nd\n", 101),
      acpDiff("t1", "/w/README.md", "", "x\n", 102),
      acpResult("t1", "completed", 103, "edited 2 files"),
    ];
    const items = buildLog(input({ stepList: s, selectedRunId: "r1" }));
    expect(kinds(items)).toEqual(["call", "file", "file"]);
    expect(items[0]).toMatchObject({ kind: "call", ok: true, summary: "edited 2 files" });
    expect((items[0] as Call).result?.kind).toBe("tool_result");
    expect(items[1]).toMatchObject({ kind: "file", callId: "t1", path: "/w/part.py", added: 2, removed: 1 });
    expect(items[2]).toMatchObject({ kind: "file", path: "/w/README.md", added: 1, removed: 0 });
    const files = buildLog(input({ stepList: s, selectedRunId: "r1", filter: { scope: "run", kind: "files" } }));
    expect(kinds(files)).toEqual(["file", "file"]);
  });

  it("failed_acp_tool_result_is_a_failed_call_under_errors", () => {
    const s = [acpCall("t2", 100), acpDiff("t2", "/w/part.py", "a\n", "b\n", 101), acpResult("t2", "failed", 102)];
    const items = buildLog(input({ stepList: s, selectedRunId: "r1" }));
    expect(items[0]).toMatchObject({ kind: "call", ok: false, summary: "failed" });
    const errs = buildLog(input({ stepList: s, selectedRunId: "r1", filter: { scope: "run", kind: "errors" } }));
    expect(errs.map((i) => (i.kind === "call" ? i.callId : i.kind))).toEqual(["t2"]);
  });

  it("a_call_that_never_finished_does_not_absorb_later_steps", () => {
    const s: Step[] = [
      toolCall("r1", "a", "onshape_read", {}, 100),                       // never gets a result
      message("r1", "user", "try again", 101),                            // a new turn
      toolCall("r1", "b", "onshape_read", {}, 102), toolResult("r1", "b", "onshape_read", 103),
      step("r1", "onshape_call", { method: "GET", path: "/api/v10/documents/d1", status: 200 }, 104),
    ];
    const items = buildLog(input({ stepList: s, selectedRunId: "r1" }));
    const calls = items.filter((i): i is Call => i.kind === "call");
    expect(calls.find((c) => c.callId === "a")?.attached).toEqual([]);
    expect(calls.find((c) => c.callId === "b")?.attached.map((x) => x.kind)).toEqual(["onshape_call"]);
  });

  it("onshape_calls_attach_to_their_call_and_do_not_break_folding", () => {
    // core onshape/client.py: onshape_call {method, path, status, counted, ledger_id}, no call_id, written while the tool runs
    const s: Step[] = [];
    for (let i = 0; i < 5; i++) {
      s.push(toolCall("r1", `o${i}`, "onshape_get_features", { did: "d1" }, 100 + i));
      s.push(step("r1", "onshape_call", { method: "GET", path: "/api/v10/partstudios/d/d1/w/w1/e/e1/features", status: 200, counted: true, ledger_id: 10 + i }, 100 + i + 0.1));
      s.push(step("r1", "onshape_call", { method: "GET", path: "/api/v10/documents/d1", status: 200, counted: true, ledger_id: 20 + i }, 100 + i + 0.2));
      s.push(toolResult("r1", `o${i}`, "onshape_get_features", 100 + i + 0.5));
    }
    const items = buildLog(input({ stepList: s, selectedRunId: "r1" }));
    expect(kinds(items)).toEqual(["call", "fold"]);
    expect((items[0] as Call).attached.map((x) => x.kind)).toEqual(["onshape_call", "onshape_call"]);
    const fold = items[1];
    if (fold.kind !== "fold") throw new Error("no fold");
    expect(fold.calls).toHaveLength(4);
    expect(fold.calls.every((c) => c.attached.length === 2)).toBe(true);
  });

  it("unknown_kinds_never_split_a_fold_run", () => {
    const s: Step[] = [
      ...pairs("r1", 1, 100, "a"),
      step("r1", "sim_run", { output: { summary: "max stress 12 MPa" } }, 101.8),   // no call_id, after a result
      ...pairs("r1", 2, 102, "b"),
    ];
    const items = buildLog(input({ stepList: s, selectedRunId: "r1" }));
    expect(kinds(items)).toEqual(["call", "fold"]);
    expect((items[0] as Call).attached.map((x) => x.kind)).toEqual(["sim_run"]);
    // with no call to attach to it is a quiet note
    const alone = buildLog(input({ stepList: [message("r1", "user", "hi", 1), step("r1", "verify", { summary: "ok" }, 2, { id: 7777 })], selectedRunId: "r1" }));
    expect(alone[1]).toMatchObject({ kind: "note", text: "verify · ok", stepId: 7777 });
  });

  it("folds_carry_run_id_and_never_span_runs_in_all_scope", () => {
    const runs = [run("r1"), run("r2", { agent: "checker" })];
    // same call ids in both runs, interleaved in time so both runs' calls are adjacent when merged
    const s = [...pairs("r1", 4, 100), ...pairs("r2", 4, 200)];
    const all = buildLog(input({ stepList: s, runs, filter: { scope: "all", kind: "everything" } }));
    const folds = all.filter((i): i is Extract<LogItem, { kind: "fold" }> => i.kind === "fold");
    expect(folds).toHaveLength(2);
    expect(folds.map((f) => f.runId)).toEqual(["r1", "r2"]);
    for (const f of folds) expect(new Set(f.calls.map((c) => c.runId)).size).toBe(1);
    const keys = all.map(itemKey);
    expect(new Set(keys).size).toBe(keys.length);
    expect(keys.filter((k) => k.includes("f:"))).toEqual(["r1/f:k1", "r2/f:k1"]);
  });

  it("keys_use_step_ids_and_run_prefix_in_all_scope", () => {
    const runs = [run("r1"), run("r2", { agent: "checker" })];
    const s = [
      toolCall("r1", "c1", "mirror_measure", {}, 100), toolResult("r1", "c1", "mirror_measure", 101),
      { ...toolCall("r1", "c1", "mirror_measure", {}, 102), run_id: "r2" }, { ...toolResult("r1", "c1", "mirror_measure", 103), run_id: "r2" },
      step("r1", "compaction", { summarized_messages: 3 }, 104, { id: 5555 }),
    ];
    const all = buildLog(input({ stepList: s, runs, filter: { scope: "all", kind: "everything" } }));
    const keys = all.map(itemKey);
    expect(new Set(keys).size).toBe(keys.length);
    expect(keys).toEqual(["r1/c:c1", "r2/c:c1", "r1/n:5555"]);
    const one = buildLog(input({ stepList: s, runs, selectedRunId: "r1" }));
    expect(one.map(itemKey)).toEqual(["c:c1", "n:5555"]);
  });
});

describe("userWords", () => {
  const SKETCH_STEP = (JSON.parse(readFileSync(resolve(__dirname, "../../../test/fixtures/core-ui/sketch_run_steps.json"), "utf8")) as Step[])[0];

  it("a sketch run's first message shows the user's words, not the procedure Mises added (real core step)", () => {
    const p = SKETCH_STEP.payload as Record<string, unknown>;
    expect(String(p.text)).toContain(SKETCH_PROCEDURE_START);           // pinned to the real core
    expect(userWords(p)).toBe("make this bracket\n\n(sketch attached)");
    const items = buildLog(input({ runs: [run(SKETCH_STEP.run_id)], stepList: [SKETCH_STEP], selectedRunId: SKETCH_STEP.run_id }));
    const msg = items.find((i) => i.kind === "message") as Extract<LogItem, { kind: "message" }>;
    expect(msg.text).toBe("make this bracket\n\n(sketch attached)");
  });

  it("other messages are unchanged", () => {
    expect(userWords({ role: "user", text: "make a plate" })).toBe("make a plate");
    expect(userWords({ role: "user", text: "x", context: { part: 1 } })).toBe("x");
  });
});
