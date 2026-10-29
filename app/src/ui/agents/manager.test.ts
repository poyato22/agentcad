import { afterEach, describe, expect, it } from "vitest";
import type { AgentRun, ModelsInfo, RunNode } from "../../api/types";
import {
  agentManagerOpen, agoText, closeAgentManager, costText, flattenTree, groupByBuild, installManagerShortcut,
  modelRefs, refusalText, runTitle, sortRuns, subtreeIds, visibleRoots,
} from "./manager";

function node(id: string, over: Partial<RunNode> = {}, children: RunNode[] = []): RunNode {
  const run: AgentRun = {
    id, parent_id: null, agent: "designer", provider: "fake", model: "m", task: `task ${id}`, status: "done",
    branch: null, onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0, created_at: 1, updated_at: 1,
  };
  return { ...run, ...over, children };
}

describe("run tree", () => {
  const tree = [
    node("orch", { last_activity_at: 50, agent: "orchestrator" }, [node("w1", { parent_id: "orch", last_activity_at: 10 }), node("w2", { parent_id: "orch", last_activity_at: 40 })]),
    node("solo", { last_activity_at: 100 }),
  ];

  it("flattens depth-first, most recent first, workers under their orchestrator", () => {
    const rows = flattenTree(tree, new Set());
    expect(rows.map((r) => `${r.run.id}@${r.depth}`)).toEqual(["solo@0", "orch@0", "w2@1", "w1@1"]);
    expect(rows.find((r) => r.run.id === "orch")).toMatchObject({ hasChildren: true, expanded: true });
  });

  it("a collapsed orchestrator hides its workers", () => {
    expect(flattenTree(tree, new Set(["orch"])).map((r) => r.run.id)).toEqual(["solo", "orch"]);
  });

  it("pinned runs sort first", () => {
    expect(sortRuns([node("a", { last_activity_at: 9 }), node("b", { pinned: true, last_activity_at: 1 })]).map((r) => r.id)).toEqual(["b", "a"]);
  });

  it("subtree ids include every worker", () => {
    expect(subtreeIds(tree[0])).toEqual(["orch", "w1", "w2"]);
  });

  it("archived roots are hidden unless asked", () => {
    const list = [node("a", { archived: true }), node("b")];
    expect(visibleRoots(list, false).map((r) => r.id)).toEqual(["b"]);
    expect(visibleRoots(list, true)).toHaveLength(2);
  });

  it("groups roots by build name, runs without a build last", () => {
    const groups = groupByBuild([node("a", { project_id: "p2" }), node("b", { project_id: "p1" }), node("c")], new Map([["p1", "Drone"], ["p2", "Bracket"]]));
    expect(groups.map((g) => g.name)).toEqual(["Bracket", "Drone", "No build"]);
  });
});

describe("labels", () => {
  it("the title falls back to the task words", () => {
    expect(runTitle(node("a", { title: "Upright v2" }))).toBe("Upright v2");
    expect(runTitle(node("a", { title: "  ", task: "stiffen the upright bracket" }))).toBe("stiffen upright bracket");
  });

  it("cost text from the run_view cost, else the raw fields", () => {
    expect(costText(node("a", { cost: { usd: 0.042, tokens: 12_300 } }))).toBe("$0.04 · 12.3k tokens");
    expect(costText(node("a", { cost: { usd: null, tokens: 12 } }))).toBe("12 tokens");
    expect(costText(node("a"))).toBe("—");
  });

  it("relative time", () => {
    expect(agoText(1000, 1010)).toBe("just now");
    expect(agoText(0, 600)).toBe("10 min ago");
    expect(agoText(0, 7200)).toBe("2 h ago");
    expect(agoText(0, 3 * 86400)).toBe("3 d ago");
  });

  it("explains each 409 reason in plain words", () => {
    const label = (id: string) => `worker ${id}`;
    expect(refusalText({ reason: "running", children: [] }, label)).toBe("This run is still working. Stop it first, or use Cancel and delete.");
    expect(refusalText({ reason: "running", children: ["a"] }, label)).toContain("A worker of this run is still working (worker a)");
    expect(refusalText({ reason: "running", children: ["a", "b", "c", "d"] }, label)).toContain("4 workers of this run are still working (worker a, worker b, worker c and 1 more)");
    expect(refusalText({ reason: "pending_approval", children: [] }, label)).toContain("waiting for your approval");
    expect(refusalText({ reason: "pending_approval", children: [] }, label)).toContain("never applied");
  });

  it("a blocking worker that waits for approval is described as waiting, not working", () => {
    const label = (id: string) => `worker ${id}`;
    const waits = (id: string) => id === "w";
    expect(refusalText({ reason: "running", children: ["w"] }, label, waits))
      .toBe("A worker of this run is waiting for your approval (worker w). Stop or decide it first, or use Cancel and delete — the pending change is then rejected, never applied.");
    expect(refusalText({ reason: "running", children: ["a", "w"] }, label, waits))
      .toContain("A worker of this run is still working (worker a), and one is waiting for your approval (worker w).");
  });

  it("model refs: inherit first, claude-code aliases prefixed", () => {
    const models = { default: "anthropic/claude-sonnet-5-5", vendors: [
      { vendor: "claude-code", key: "signed_in", models: ["sonnet", "opus"] },
      { vendor: "ollama", key: "not_needed", models: ["ollama/qwen3"] },
    ] } as ModelsInfo;
    expect(modelRefs(models)).toEqual(["inherit", "claude-code/sonnet", "claude-code/opus", "ollama/qwen3", "anthropic/claude-sonnet-5-5"]);
    expect(modelRefs(null)).toEqual(["inherit"]);
  });
});

describe("⇧⌘A", () => {
  afterEach(() => { closeAgentManager(); });
  const press = () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "A", code: "KeyA", metaKey: true, shiftKey: true }));

  it("toggles the manager", () => {
    const off = installManagerShortcut({ isTrusted: () => true, isModalOpen: () => false });
    press();
    expect(agentManagerOpen.value).toBe(true);
    press();
    expect(agentManagerOpen.value).toBe(false);
    off();
  });

  it("does not open over another modal, and ignores synthetic keys", () => {
    const off = installManagerShortcut({ isTrusted: () => true, isModalOpen: () => true });
    press();
    expect(agentManagerOpen.value).toBe(false);
    off();
    const off2 = installManagerShortcut({ isTrusted: () => false, isModalOpen: () => false });
    press();
    expect(agentManagerOpen.value).toBe(false);
    off2();
  });
});
