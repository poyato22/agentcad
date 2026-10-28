import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { stepSchema } from "../../api/schemas";
import type { Step } from "../../api/types";
import { buildLog } from "./logModel";
import { approval, input, message, pairs, resetIds, run, toolCall, toolFailure, toolResult, step } from "./testSteps";
import { activityText, buildThread, type ThreadItem } from "./threadModel";

// The real core's steps for one designer run (core/scripts/dump_ui_fixtures.py): a user message,
// part create, set_vars (diff), measure, an assistant message and an open fixture_merge call.
const REAL: Step[] = (JSON.parse(readFileSync(resolve(__dirname, "../../../test/fixtures/core-ui/steps.json"), "utf8")) as unknown[]).map((s) => stepSchema.parse(s));
const RID = REAL[0].run_id;

function thread(over: Parameters<typeof input>[0]): ThreadItem[] {
  const i = input({ filter: { scope: "run", kind: "everything" }, ...over });
  return buildThread(buildLog(i));
}

const brief = (t: ThreadItem[]) => t.map((x) => {
  switch (x.kind) {
    case "user": case "agent": return `${x.kind}: ${x.text}`;
    case "activity": return `${x.tone === "fail" ? "✕" : x.tone === "running" ? "…" : "·"} ${x.text}`;
    case "error": return `✕ ${x.code}: ${x.message}`;
    case "fold": return `▸ ${x.text}`;
    case "approval": return `card ${x.approval.id}`;
    case "question": return `question ${x.question.question_id}`;
    case "note": return `note ${x.text}`;
  }
});

describe("buildThread on the real core's steps", () => {
  it("reads_as_a_conversation_with_compact_activity_lines", () => {
    const t = thread({ runs: [run(RID)], stepList: REAL, selectedRunId: RID });
    expect(brief(t)).toEqual([
      "user: thicken the mount plate to 6 mm",
      "· created mount plate · 3.9 cm³",
      "· changed mount plate · thickness 5 → 6 · +0.8 cm³",
      "· measured mount plate · 4.6 cm³",
      "agent: Thickened the plate to 6 mm; asking to merge.",
      "… asking to merge mount plate",
    ]);
  });

  it("the_set_vars_line_carries_its_diff_and_raw_io_for_expansion", () => {
    const t = thread({ runs: [run(RID)], stepList: REAL, selectedRunId: RID });
    const set = t.find((x) => x.kind === "activity" && x.text.startsWith("changed")) as Extract<ThreadItem, { kind: "activity" }>;
    expect(set.call?.name).toBe("mirror_part_set_vars");
    expect(set.diff?.vars).toEqual([{ name: "thickness", prev: 5, now: 6 }]);
    expect(set.stepIds).toContain(7);
  });

  it("the_pending_approval_sits_inline_after_the_last_step", () => {
    const a = approval("ap1", RID);
    const t = thread({ runs: [run(RID)], stepList: REAL, selectedRunId: RID, approvals: [a] });
    expect(brief(t).at(-1)).toBe("card ap1");
  });
});

describe("buildThread rules", () => {
  it("failed_calls_and_errors_are_always_their_own_red_lines_with_the_hint", () => {
    resetIds();
    const stepList = [
      message("r1", "user", "make the wall thinner", 1),
      ...pairs("r1", 4, 2),
      toolCall("r1", "f1", "mirror_part_set_vars", { part_id: "mount_plate", vars: { wall: 0.4 } }, 10),
      toolFailure("r1", "f1", "mirror_part_set_vars", 10.5, "wall 0.4 mm is below the 0.8 mm minimum"),
      step("r1", "error", { stop_reason: "error", error: "transient", message: "provider timed out", hint: "try again", retryable: true }, 11),
    ];
    const t = thread({ runs: [run("r1")], stepList, selectedRunId: "r1" });
    expect(brief(t)).toEqual([
      "user: make the wall thinner",
      "· measured mount plate",
      "▸ 3 more steps · mirror_measure",
      "✕ changing mount plate failed · wall 0.4 mm is below the 0.8 mm minimum",
      "✕ transient: provider timed out",
    ]);
    const fail = t[3] as Extract<ThreadItem, { kind: "activity" }>;
    expect(fail.hint).toBe("try again");
    expect((t[4] as Extract<ThreadItem, { kind: "error" }>).hint).toBe("try again");
  });

  it("onshape_proposals_never_fold", () => {
    resetIds();
    const stepList = [message("r1", "user", "push it", 1), ...pairs("r1", 4, 2, "p", "onshape_propose_change")];
    const t = thread({ runs: [run("r1")], stepList, selectedRunId: "r1" });
    expect(brief(t).filter((x) => x === "· proposed change on Onshape branch")).toHaveLength(4);
    expect(t.some((x) => x.kind === "fold")).toBe(false);
  });

  it("live_text_is_an_agent_reply_being_written", () => {
    const t = thread({ runs: [run("r1")], stepList: [message("r1", "user", "hi", 1)], selectedRunId: "r1", liveText: new Map([["r1", "Checking the"]]) });
    const last = t.at(-1) as Extract<ThreadItem, { kind: "agent" }>;
    expect([last.text, last.live]).toEqual(["Checking the", true]);
  });

  it("an_open_question_is_inline", () => {
    const t = thread({ runs: [run("r1")], stepList: [message("r1", "user", "hi", 1)], selectedRunId: "r1",
      questions: [{ question_id: "q1", run_id: "r1", question: "merge now?", options: ["yes", "no"] }] });
    expect(brief(t).at(-1)).toBe("question q1");
  });

  it("keys_are_unique_and_stable", () => {
    const t = thread({ runs: [run(RID)], stepList: REAL, selectedRunId: RID, approvals: [approval("ap1", RID)] });
    const keys = t.map((x) => x.key);
    expect(new Set(keys).size).toBe(keys.length);
    expect(keys[0]).toBe("s:1");
  });
});

describe("activityText", () => {
  const ok = (name: string, inp: Record<string, unknown>, data: Record<string, unknown> = {}, summary = "ok") => {
    resetIds();
    const s = [toolCall("r", "c", name, inp, 1), toolResult("r", "c", name, 2, { data, summary })];
    const item = buildLog(input({ stepList: s, selectedRunId: "r", runs: [run("r")] }))[0];
    if (item.kind !== "call") throw new Error("not a call");
    return activityText(item, null);
  };
  it("names_what_happened_in_plain_words", () => {
    expect(ok("mirror_measure", { part_id: "mount_plate" }, { volume_mm3: 8000 })).toBe("measured mount plate · 8.0 cm³");
    expect(ok("onshape_propose_change", { part_id: "upright" })).toBe("proposed change on Onshape branch");
    expect(ok("mirror_part_list", {})).toBe("listed parts");
    expect(ok("agent_spawn", { agent: "verifier" })).toBe("started verifier");
    expect(ok("mirror_export", { part_id: "mount_plate" })).toBe("exported mount plate");
    expect(ok("some_new_tool", {}, {}, "did a thing\nsecond line")).toBe("some new tool · did a thing");
    expect(ok("mcp__mises__mirror_measure", { part_id: "bracket" })).toBe("measured bracket");
  });
});
