import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import type { Step } from "../../api/types";
import { buildLog, filterCounts, type LogItem } from "./logModel";
import { approval, input, message, pairs, resetIds, run, step, toolCall, toolFailure, toolResult } from "./testSteps";

const FIXTURE_STEPS = JSON.parse(readFileSync(resolve(__dirname, "../../../test/fixtures/core-ui/steps.json"), "utf8")) as Step[];
const FIXTURE_RUN = "b7c79a57cbb4";

const kinds = (items: LogItem[]) => items.map((i) => i.kind);
type Call = Extract<LogItem, { kind: "call" }>;

beforeEach(() => resetIds());

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
    expect(kinds(buildLog(input({ stepList: s, selectedRunId: "r1", expandedFolds: new Set(["y"]) })))).toEqual(["call", "call"]);
  });

  it("expanded_fold_shows_all", () => {
    const s = pairs("r1", 5, 100);
    const items = buildLog(input({ stepList: s, selectedRunId: "r1", expandedFolds: new Set(["k1"]) }));
    expect(kinds(items)).toEqual(["call", "call", "call", "call", "call"]);
  });

  it("diff_item_from_set_vars", () => {
    const items = buildLog(input({ stepList: FIXTURE_STEPS, selectedRunId: FIXTURE_RUN }));
    const diff = items.find((i) => i.kind === "diff");
    if (!diff || diff.kind !== "diff") throw new Error("no diff");
    expect(diff.step.id).toBe(7);
    expect(diff.partId).toBe("mount_plate");
    expect(diff.vars).toEqual([{ name: "thickness", prev: 5, now: 6 }]);
    expect(diff.delta).toEqual({ bbox_size: [0, 0, 1], mass_g: 0.9576410294662194, volume_mm3: 772.2911527953379 });
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
    expect(counts.errors).toBe(2);
    expect(counts.files).toBe(1);
    expect(counts.decisions).toBe(2); // decision step + pending approval
    const tc = buildLog({ ...base, filter: { scope: "run", kind: "tool_calls" } });
    expect(new Set(kinds(tc))).toEqual(new Set(["call", "fold", "diff"]));
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
