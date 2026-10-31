import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { Step } from "../../api/types";
import { buildLog } from "./logModel";
import { dockTree, liveLead } from "./runTree";
import { splitStreams } from "./streams";
import { input, message, resetIds, run, step, toolCall, toolResult } from "./testSteps";
import { buildThread } from "./threadModel";
import { createTreeStreams, treeStreams } from "./treeStreams";

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

describe("aggregation cost (fix round 2, N1: no lag while agents stream)", () => {
  /** 1500 steps per run: calls and results, a study every 25 calls, an assistant message per turn. */
  function runSteps(runId: string, base: number): Step[] {
    const out: Step[] = [];
    for (let i = 0; out.length < 1500; i++) {
      const t = base + i;
      const name = i % 25 === 0 ? "sim_run" : "mirror_measure";
      out.push(toolCall(runId, `${runId}c${i}`, name, { part_id: "p", type: "static" }, t));
      out.push(toolResult(runId, `${runId}c${i}`, name, t + 0.5, { data: name === "sim_run" ? { sim_id: `${runId}s${i}` } : {} }));
      if (i % 10 === 9) out.push(message(runId, "assistant", `step ${i}`, t + 0.7));
    }
    return out;
  }

  it("20 runs × 1500 steps: a token or another run's new step rebuilds only that run, in a few ms, and keeps the rest identical", () => {
    resetIds(1);
    const lead = run("R0", { role: "lead" });
    const runs = [lead, ...Array.from({ length: 19 }, (_, i) => run(`R${i + 1}`, { parent_id: "R0", role: "sub" }))];
    const steps = new Map(runs.map((r, i) => [r.id, runSteps(r.id, i * 10_000)]));
    const agg = createTreeStreams();
    const first = agg({ runs, steps }, runs);
    expect(agg.stats.rebuilt).toBe(20);
    expect(first.sim.length).toBe(20 * Math.ceil(1500 / 2.1 / 25));
    // a token arrives (liveText only): the aggregation's inputs are unchanged, nothing is rebuilt
    let t0 = performance.now();
    const same = agg({ runs, steps }, runs);
    const idle = performance.now() - t0;
    expect(agg.stats.rebuilt).toBe(0);
    expect(same).toBe(first);
    // one step lands on R7 (the store replaces only R7's array): only R7 is rebuilt
    const r7 = steps.get("R7") as Step[];
    const next = new Map(steps).set("R7", [...r7, toolCall("R7", "R7new", "sim_run", { type: "modal" }, 1e9)]);
    t0 = performance.now();
    const after = agg({ runs, steps: next }, runs);
    const oneRun = performance.now() - t0;
    expect(agg.stats.rebuilt).toBe(1);
    expect(after.sim.length).toBe(first.sim.length + 1);
    // every other entry is the very same object (Preact skips their cards)
    const byKey = new Map(first.sim.map((e) => [e.key, e]));
    for (const e of after.sim) if (!e.key.startsWith("R7/")) expect(e).toBe(byKey.get(e.key));
    expect(after.view).toBe(first.view);
    // budget: idle is free; one run's rebuild is a few ms (generous for a loaded CI machine)
    expect(idle).toBeLessThan(2);
    expect(oneRun).toBeLessThan(15);
  });
});

describe("the live lead (fix round 2, N2)", () => {
  it("prefers a running lead, then the newest, over list order", () => {
    const idleOld = run("A", { role: "lead", status: "done", created_at: 1 });
    const idleNew = run("B", { role: "lead", status: "done", created_at: 5 });
    const busy = run("C", { role: "lead", status: "running", created_at: 3 });
    expect(liveLead([idleOld, busy, idleNew])?.id).toBe("C");
    expect(liveLead([idleOld, idleNew])?.id).toBe("B");
    expect(liveLead([run("D", { role: "lead", archived: true })])).toBeNull();
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
