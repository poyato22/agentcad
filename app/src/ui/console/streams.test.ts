import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { eventSchema, stepSchema } from "../../api/schemas";
import type { Step } from "../../api/types";
import { buildLog } from "./logModel";
import { approval, input, message, resetIds, step, toolCall, toolResult } from "./testSteps";
import { eventStream, simOrder, simRunning, splitStreams, streamOf, toolStream } from "./streams";
import { buildThread, type ThreadItem } from "./threadModel";

// Real-core fixtures: one designer run (dump_ui_fixtures.py), an agent's studies and design
// review (dump_study_fixtures.py), and G1's presented view / critique round (g1_cards.json).
const FX = resolve(__dirname, "../../../../core/tests/fixtures/ui");
const APP_FX = resolve(__dirname, "../../../test/fixtures/core-ui");
const parse = (path: string): Step[] => (JSON.parse(readFileSync(path, "utf8")) as unknown[]).map((s) => stepSchema.parse(s));
const TEXT = parse(resolve(APP_FX, "steps.json"));
const RID = TEXT[0].run_id;
const STUDY = parse(resolve(FX, "study_agent_steps.json")).map((s) => ({ ...s, run_id: RID, id: s.id + 10_000 }));
const G1 = JSON.parse(readFileSync(resolve(APP_FX, "g1_cards.json"), "utf8")) as Record<string, Record<string, unknown>>;
const announce = (card: Record<string, unknown>, id: number, ts: number, extra: Partial<Step> = {}): Step =>
  step(RID, "decision", { agent_announce: card }, ts, { id, ...extra });

function thread(list: Step[]): ThreadItem[] {
  return buildThread(buildLog(input({ filter: { scope: "run", kind: "everything" }, selectedRunId: RID, stepList: list })));
}

describe("dock stream classification (Plan 9 D4)", () => {
  it("splits the real core's run, studies and views into the three streams by type", () => {
    resetIds(50_000);
    const views = [announce(G1.view_presented, 60_001, 2e9), announce(G1.critique_round, 60_002, 2e9 + 1)];
    const s = splitStreams(thread([...TEXT, ...STUDY, ...views]));
    expect(s.view.map((i) => i.kind)).toEqual(["view", "critique"]);
    // every sim_run and the design review, one card each (R18's grid is split per study)
    expect(s.sim.length).toBe(STUDY.filter((x) => x.kind === "tool_call").length);
    expect(s.sim.every((i) => i.kind === "activity" && /sim_run|design_review/.test(i.call?.name ?? ""))).toBe(true);
    // the conversation keeps the words, the part_created card and the other activity lines
    expect(s.text.some((i) => i.kind === "user")).toBe(true);
    expect(s.text.some((i) => i.kind === "agent")).toBe(true);
    expect(s.text.some((i) => i.kind === "announce" && i.announcement.kind === "part_created")).toBe(true);
    expect(s.text.some((i) => i.kind === "activity" && /sim_run|design_review/.test(i.call?.name ?? ""))).toBe(false);
    expect(s.text.some((i) => i.kind === "view" || i.kind === "critique")).toBe(false);
  });

  it("uses B3's stream field when a step carries one", () => {
    resetIds();
    const list = [
      toolCall(RID, "r1", "mirror_measure", { part_id: "p" }, 1, { stream: "view" }), toolResult(RID, "r1", "mirror_measure", 1.5),
      toolCall(RID, "s1", "sim_start_custom", {}, 2), toolResult(RID, "s1", "sim_start_custom", 2.5),
      step(RID, "decision", { reason: "rendered the lid", note: true }, 3, { stream: "view" }),
      announce(G1.view_presented, 9001, 4, { stream: "text" }),
    ];
    const items = thread(list);
    const by = (k: string) => items.find((i) => i.key === k) as ThreadItem;
    expect(streamOf(items.find((i) => i.kind === "activity" && i.call?.name === "mirror_measure") as ThreadItem)).toBe("view");
    expect(streamOf(items.find((i) => i.kind === "activity" && i.call?.name === "sim_start_custom") as ThreadItem)).toBe("sim");
    expect(streamOf(items.find((i) => i.kind === "note") as ThreadItem)).toBe("view");
    expect(streamOf(by("s:9001"))).toBe("text");
  });

  it("never moves the dialogue or what needs you out of the conversation, whatever a step says", () => {
    resetIds();
    const list = [
      { ...message(RID, "user", "make it lighter", 1), stream: "sim" as const },
      { ...message(RID, "assistant", "on it", 2), stream: "view" as const },
    ];
    const items = buildThread(buildLog(input({ filter: { scope: "run", kind: "everything" }, selectedRunId: RID, stepList: list, approvals: [approval("ap1", RID)] })));
    expect(items.map(streamOf)).toEqual(["text", "text", "text"]);
  });

  it("falls back by tool name and event type", () => {
    for (const n of ["sim_run", "sim_static_run", "sim_wait", "sim_result_get", "mcp__mises__sim_run", "design_review", "study_compare"]) expect(toolStream(n), n).toBe("sim");
    for (const n of ["present_view", "critique_model", "part_show", "render_part", "render", "mcp__mises__present_view", "assembly_animate"]) expect(toolStream(n), n).toBe("view");
    for (const n of ["mirror_part_create", "mirror_measure", "agent_spawn", "ui_ask_user", "rendering_notes"]) expect(toolStream(n), n).toBe("text");
    expect(eventStream({ type: "sim_progress" })).toBe("sim");
    expect(eventStream({ type: "sim_finished" })).toBe("sim");
    expect(eventStream({ type: "render_done" })).toBe("view");
    expect(eventStream({ type: "text_delta" })).toBe("text");
    expect(eventStream({ type: "text_delta", stream: "view" })).toBe("view");
  });

  it("the schemas read stream as optional and drop an unknown value", () => {
    const s = TEXT[0];
    expect(stepSchema.parse({ ...s, stream: "sim" }).stream).toBe("sim");
    expect(stepSchema.parse({ ...s, stream: "hologram" }).stream).toBeUndefined();
    expect("stream" in stepSchema.parse(s) && stepSchema.parse(s).stream !== undefined).toBe(false);
    expect(eventSchema.parse({ type: "sim_finished", run_id: null, data: {}, ts: 1, stream: "sim" }).stream).toBe("sim");
  });
});

describe("Simulations order", () => {
  it("puts running studies on top, newest first in each group, and a finished sim_id ends running", () => {
    resetIds();
    const list = [
      toolCall(RID, "a", "sim_run", { type: "static", part_id: "p" }, 1), toolResult(RID, "a", "sim_run", 1.5, { data: { sim_id: "s_a" } }),
      // B3: wait:false returns at once, the study goes on in the background
      toolCall(RID, "b", "sim_run", { type: "modal", part_id: "p" }, 2), toolResult(RID, "b", "sim_run", 2.5, { data: { sim_id: "s_b", status: "running", stream: "sim" } }),
      toolCall(RID, "c", "sim_run", { type: "static", part_id: "q" }, 3), toolResult(RID, "c", "sim_run", 3.5, { data: { sim_id: "s_c" } }),
      toolCall(RID, "d", "sim_run", { type: "thermal", part_id: "q" }, 4),   // in flight
    ];
    const sims = splitStreams(thread(list)).sim;
    const ids = (xs: ThreadItem[]) => xs.map((i) => (i.kind === "activity" ? i.call?.callId : i.key));
    expect(ids(simOrder(sims))).toEqual(["d", "b", "c", "a"]);
    expect(simRunning(sims.find((i) => i.kind === "activity" && i.call?.callId === "b") as ThreadItem)).toBe(true);
    expect(ids(simOrder(sims, new Map([["s_b", "done"]])))).toEqual(["d", "c", "b", "a"]);
  });
});
