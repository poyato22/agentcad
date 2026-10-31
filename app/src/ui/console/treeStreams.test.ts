import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { Step } from "../../api/types";
import { buildLog } from "./logModel";
import { dockTree } from "./runTree";
import { splitStreams } from "./streams";
import { input, resetIds, run, step, toolCall, toolResult } from "./testSteps";
import { buildThread } from "./threadModel";
import { treeStreams } from "./treeStreams";

// G1's real presented view (g1_cards.json), as the core stores it: a decision step on the acting
// run, mirrored onto its lead (core announce.announce)
const G1 = JSON.parse(readFileSync(resolve(__dirname, "../../../test/fixtures/core-ui/g1_cards.json"), "utf8")) as Record<string, Record<string, unknown>>;
const viewBy = (actor: string, onRun: string, ts: number, id: number): Step =>
  step(onRun, "decision", { agent_announce: { ...G1.view_presented, run_id: actor } }, ts, { id });

describe("View and Simulations over the build's agent tree (fix round 1, I1)", () => {
  const lead = run("L", { role: "lead", agent: "engineer" });
  const sub = run("S", { parent_id: "L", role: "sub", agent: "designer" });
  const subsub = run("SS", { parent_id: "S", role: "sub", agent: "simulation" });
  const elsewhere = run("X", { agent: "checker" });

  it("merges every run's views and studies by time, each tagged with its own run, a mirrored card once", () => {
    resetIds(100);
    const steps: Step[] = [
      toolCall("L", "l1", "sim_run", { type: "static", part_id: "p" }, 10), toolResult("L", "l1", "sim_run", 11, { data: { sim_id: "a" } }),
      toolCall("SS", "s1", "sim_run", { type: "modal", part_id: "p" }, 12),                       // in flight
      viewBy("S", "S", 13, 500), viewBy("S", "L", 13, 501),                                          // mirrored onto the lead
      toolCall("X", "x1", "sim_run", { type: "static", part_id: "q" }, 14),                       // another tree
    ];
    const tree = dockTree([elsewhere, subsub, sub, lead], lead);
    expect(tree.map((r) => r.id)).toEqual(["L", "S", "SS"]);
    const out = treeStreams(input({ runs: [lead, sub, subsub, elsewhere], stepList: steps }), tree);
    expect(out.sim.map((e) => [e.runId, e.agent, e.key])).toEqual([["L", "engineer", "L/c:l1"], ["SS", "simulation", "SS/c:s1"]]);
    expect(out.view.map((e) => [e.runId, e.agent])).toEqual([["S", "designer"]]);
    expect(new Set([...out.sim, ...out.view].map((e) => e.key)).size).toBe(3);
  });

  it("keeps the lead's mirrored card when the run that made it is outside the tree", () => {
    resetIds(200);
    const out = treeStreams(input({ runs: [lead], stepList: [viewBy("gone", "L", 5, 600)] }), [lead]);
    expect(out.view.map((e) => e.runId)).toEqual(["L"]);
  });

  it("the shown run's own tree joins when it sits outside the lead's", () => {
    expect(dockTree([lead, elsewhere], elsewhere).map((r) => r.id)).toEqual(["L", "X"]);
    expect(dockTree([elsewhere], null)).toEqual([]);
  });
});

describe("study keys are stable (fix round 1, M2)", () => {
  it("a study keeps its key when a second study joins its turn", () => {
    resetIds(300);
    const one = [toolCall("L", "a", "sim_run", { type: "static" }, 1), toolResult("L", "a", "sim_run", 2)];
    const two = [...one, toolCall("L", "b", "sim_run", { type: "modal" }, 3)];
    const keys = (list: Step[]) => splitStreams(buildThread(buildLog(input({ filter: { scope: "run", kind: "everything" }, selectedRunId: "L", stepList: list })))).sim.map((i) => i.key);
    expect(keys(one)).toEqual(["c:a"]);
    expect(keys(two)).toEqual(["c:a", "c:b"]);
  });
});
