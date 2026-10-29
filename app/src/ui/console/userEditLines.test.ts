import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import type { AgentRun, MisesEvent } from "../../api/types";
import { activeBuildId, resetBuilds } from "../../state/builds";
import { buildUserEdits, describeVarsEdit, knownVersions, noteMyEdit, onPartChanged, resetPartEdits, userEdits, type UserEditLine } from "../../state/partEdits";
import { parts, resetStore } from "../../state/store";
import type { ThreadItem } from "./threadModel";
import { editsForRun, weave } from "./userEditLines";

const EVENTS = readFileSync(resolve(import.meta.dirname, "../../../test/fixtures/core-ui/builds_events.jsonl"), "utf8")
  .trim().split("\n").map((l) => JSON.parse(l) as MisesEvent);

const run = (created: number, project: string | null = "a"): AgentRun => ({
  id: "r1", parent_id: null, agent: "designer", provider: "fake", model: "m", task: "t", status: "running", branch: null,
  onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0, created_at: created, updated_at: created, project_id: project,
});
const line = (ts: number, text: string, buildId: string | null = "a"): UserEditLine => ({ key: `k${ts}`, ts, partId: "p", buildId, text });
const msg = (ts: number, text: string): ThreadItem => ({ kind: "agent", key: `m${ts}`, text, ts, live: false, stepIds: [] });

beforeEach(() => { resetStore(); resetBuilds(); resetPartEdits(); });

describe("your edits in the thread", () => {
  it("weaves each edit in by time, after what came before it", () => {
    const rows = weave([msg(10, "made it"), { kind: "fold", key: "f", text: "2 more", foldKey: "x", count: 2 }, msg(30, "measured")],
      [line(20, "changed wall 3 → 4 mm on plate"), line(5, "early"), line(40, "late")]);
    expect(rows.map((r) => (r.kind === "you" ? `you:${r.line.text}` : r.item.key))).toEqual(["you:early", "m10", "f", "you:changed wall 3 → 4 mm on plate", "m30", "you:late"]);
  });

  it("shows only edits since the run started, in the run's build", () => {
    const edits = [line(5, "before"), line(20, "mine"), line(25, "other build", "b")];
    expect(editsForRun(edits, run(10)).map((l) => l.text)).toEqual(["mine"]);
    expect(editsForRun(edits, null)).toEqual([]);
  });

  it("an edit made here is written once (its part_changed echo is skipped); another window's comes from the core", () => {
    activeBuildId.value = "a";
    parts.value = [{ part_id: "quad_arm", vars: {}, metrics: null, mtime: 1, label: "Quad arm" }];
    const vars = EVENTS.find((e) => e.data.summary === "set thickness=5.0") as MisesEvent;
    noteMyEdit("quad_arm", vars.data.version as number, describeVarsEdit("quad_arm", [{ name: "thickness", prev: 4, now: 5, unit: "mm" }]));
    onPartChanged(vars);
    const hole = EVENTS.find((e) => e.data.summary === "hole op on quad_arm") as MisesEvent;
    onPartChanged(hole);
    expect(userEdits.value.map((l) => l.text)).toEqual(["changed thickness 4 → 5 mm on Quad arm", "hole op on quad_arm"]);
    expect(knownVersions.value.get("quad_arm")).toBe(hole.data.version);
    expect(buildUserEdits.value.length).toBe(2);
  });

  it("an agent's part_changed is not a You line", () => {
    onPartChanged({ type: "part_changed", run_id: "r1", ts: 1, data: { part_id: "p", version: 4, by: "agent", run_id: "r1", summary: "set wall=4" } });
    expect(userEdits.value).toEqual([]);
  });
});
