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
    case "announce": return `announce ${x.announcement.kind}: ${x.announcement.title}`;
    case "queued": return `queued(${x.status}) ${x.text}`;
    case "studies": return `studies ×${x.calls.length}`;
  }
});

// The real U3c lead's steps (core scripts/dump_agents_fixtures.py): a spawn with its stored
// announcement, then a message queued while the lead worked and delivered at its turn's end.
const LEAD: Step[] = (JSON.parse(readFileSync(resolve(__dirname, "../../../../core/tests/fixtures/ui/plan7/lead_steps.json"), "utf8")) as unknown[]).map((s) => stepSchema.parse(s));
const LID = LEAD[0].run_id;

describe("buildThread on the real core's steps", () => {
  it("reads_as_a_conversation_with_compact_activity_lines", () => {
    const t = thread({ runs: [run(RID)], stepList: REAL, selectedRunId: RID });
    expect(brief(t)).toEqual([
      "user: thicken the mount plate to 6 mm",
      // m6: the card stands for mirror_part_create's raw row (still in the Full log)
      "announce part_created: Created part mount_plate",   // U3c's announcement, U3a's card
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
    expect(set.stepIds).toContain(8);
  });

  it("the_pending_approval_sits_inline_after_the_last_step", () => {
    const a = approval("ap1", RID);
    const t = thread({ runs: [run(RID)], stepList: REAL, selectedRunId: RID, approvals: [a] });
    expect(brief(t).at(-1)).toBe("card ap1");
  });
});

describe("buildThread rules", () => {
  it("claude_code_bookkeeping_decisions_are_not_bare_decided_lines_C10", () => {
    resetIds();
    const stepList = [
      step("r1", "decision", { claude_code: "session_started", session_id: "s1" }, 1),
      message("r1", "user", "crash now", 2),
      step("r1", "decision", { claude_code: "init" }, 3),
      step("r1", "decision", { agent_thinking: "…", signature: "x" }, 4),
      step("r1", "decision", { reason: "use 6 mm walls" }, 5),
    ];
    const t = thread({ runs: [run("r1")], stepList, selectedRunId: "r1" });
    expect(brief(t)).toEqual(["user: crash now", "note decided · use 6 mm walls"]);
  });

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

describe("Plan 7: the lead's stored announcements and message queue (real U3c steps)", () => {
  it("a stored agent_announce step is a card, never a bare 'decided' row — it survives a reload", () => {
    const t = brief(thread({ runs: [run(LID)], stepList: LEAD, selectedRunId: LID }));
    expect(t).toContain("announce agent_spawned: Spawned designer: Design motor mount");
    expect(t.some((x) => x?.startsWith("note decided"))).toBe(false);
  });

  it("a card replaces its own tool's raw row, but not another tool's (m6)", () => {
    resetIds();
    const ann = (kind: string, title: string, ts: number) => step("r1", "decision", { agent_announce: { run_id: "r1", kind, title, detail: "", ref: {} } }, ts);
    const stepList = [
      message("r1", "user", "add a base", 1),
      toolCall("r1", "m1", "mirror_measure", { part_id: "base" }, 2), toolResult("r1", "m1", "mirror_measure", 2.5, { summary: "measured base" }),
      toolCall("r1", "c1", "mcp__mises__bom_add", { name: "M3 screw" }, 3), toolResult("r1", "c1", "mcp__mises__bom_add", 3.5, { summary: "added M3 screw" }),
      ann("bom_added", "Added M3 screw to the parts list", 4),
      toolCall("r1", "s1", "agent_spawn", { agent: "designer" }, 5), toolResult("r1", "s1", "agent_spawn", 5.5, { summary: "spawned designer" }),
      ann("part_created", "Created part base", 6),                       // not agent_spawn's card: its row stays
    ];
    const t = thread({ runs: [run("r1")], stepList, selectedRunId: "r1" });
    const b = brief(t);
    expect(b.some((x) => x?.includes("M3 screw") && x.startsWith("·"))).toBe(false);
    expect(b).toContain("announce bom_added: Added M3 screw to the parts list");
    expect(b.filter((x) => x?.startsWith("·")).length).toBe(2);         // measure and spawn rows stay
    const card = t.find((x) => x.kind === "announce" && x.announcement.kind === "bom_added")!;
    expect(card.kind === "announce" && card.stepIds.length).toBeGreaterThan(1);   // the row's steps ride with the card
  });

  it("a queued message shows once, at the place it was typed, and reads delivered", () => {
    const t = brief(thread({ runs: [run(LID)], stepList: LEAD, selectedRunId: LID }));
    expect(t.filter((x) => x?.includes("and paint it red"))).toEqual(["queued(delivered) and paint it red"]);
    expect(t.indexOf("queued(delivered) and paint it red")).toBeLessThan(t.indexOf("agent: working on it"));
  });

  it("still queued until the delivery step lands; a dropped one says why", () => {
    const upTo = LEAD.findIndex((s) => "queued_message_delivered" in s.payload);
    const pending = brief(thread({ runs: [run(LID)], stepList: LEAD.slice(0, upTo), selectedRunId: LID }));
    expect(pending).toContain("queued(queued) and paint it red");
    const q = LEAD.find((s) => "queued_message" in s.payload) as Step;
    const id = (q.payload.queued_message as { message_id: string }).message_id;
    const dropped = step(LID, "decision", { queued_message_dropped: { message_id: id, reason: "the lead was cancelled" } }, LEAD[upTo].ts);
    const t = thread({ runs: [run(LID)], stepList: [...LEAD.slice(0, upTo), dropped], selectedRunId: LID });
    const item = t.find((x) => x.kind === "queued") as Extract<ThreadItem, { kind: "queued" }>;
    expect([item.status, item.reason]).toEqual(["dropped", "the lead was cancelled"]);
  });
});

describe("R18: one turn's studies group into one item", () => {
  let t0 = 10;
  const study = (id: string) => { t0 += 2; return [toolCall("r1", id, "sim_run", { type: "static", part_id: "arm" }, t0), toolResult("r1", id, "sim_run", t0 + 1)]; };
  it("two or more study calls in a row become a studies item; a single one stays a line", () => {
    const stepList = [message("r1", "user", "test it", 1), ...study("a"), ...study("b"), ...study("c"), message("r1", "assistant", "all pass", 90)];
    const t = brief(thread({ runs: [run("r1")], stepList, selectedRunId: "r1" }));
    expect(t).toEqual(["user: test it", "studies ×3", "agent: all pass"]);
    const one = brief(thread({ runs: [run("r1")], stepList: [message("r1", "user", "x", 1), ...study("a")], selectedRunId: "r1" }));
    expect(one[1]?.startsWith("·")).toBe(true);
  });
  it("an agent message between studies splits the group", () => {
    const first = [...study("a"), ...study("b")];
    const mid = message("r1", "assistant", "next", (t0 += 2));
    const stepList = [...first, mid, ...study("c"), ...study("d")];
    expect(brief(thread({ runs: [run("r1")], stepList, selectedRunId: "r1" }))).toEqual(["studies ×2", "agent: next", "studies ×2"]);
  });
});

describe("int-final: U3a's announcement cards beside U4a's part card and G4's cards", () => {
  const ann = (runId: string, kind: string, title: string, ref: Record<string, unknown> = {}) =>
    step(RID, "decision", { agent_announce: { run_id: runId, kind, title, detail: "", ref } }, 1);
  it("part_show on the acting run is U4a's part card only; the lead's copy of a sub-agent's is a card", () => {
    expect(brief(thread({ runs: [run(RID)], stepList: [ann(RID, "part_shown", "Showing a part for you to look at")], selectedRunId: RID }))).toEqual([]);
    const lead = brief(thread({ runs: [run(RID)], stepList: [ann("sub000000001", "part_shown", "Showing a part for you to look at")], selectedRunId: RID }));
    expect(lead).toEqual(["announce part_shown: Showing a part for you to look at"]);
    const made = brief(thread({ runs: [run(RID)], stepList: [ann(RID, "part_created", "Created part arm_left")], selectedRunId: RID }));
    expect(made).toEqual(["announce part_created: Created part arm_left"]);
  });
  it("G1's critique_round / view_presented become G4's cards, never a second announcement card", () => {
    const crit = ann(RID, "critique_round", "Critique round 1/3", { round: 1, max_rounds: 3, parts: ["arm"], score: 0.5, threshold: 0.8, done: false,
      issues_open: [], issues_fixed: [], before: { iso: "crit_a_iso" }, after: { iso: "crit_b_iso" } });
    const t = thread({ runs: [run(RID)], stepList: [crit], selectedRunId: RID });
    expect(t.map((x) => x.kind)).toEqual(["critique"]);
  });
});
