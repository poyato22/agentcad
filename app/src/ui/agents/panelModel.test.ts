import { describe, expect, it } from "vitest";
import type { RunNode } from "../../api/types";
import { agentRows, agentRowTitle, leadFirst } from "./panelModel";

function node(over: Partial<RunNode> & { id: string }): RunNode {
  return {
    parent_id: null, agent: "designer", provider: "fake", model: "m", task: `task ${over.id}`,
    status: "running", branch: null, onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0,
    created_at: 1, updated_at: 1, children: [], ...over,
  };
}

describe("agentsPanel helpers", () => {
  it("puts the lead first among roots", () => {
    const roots = [node({ id: "other" }), node({ id: "lead", role: "lead" })];
    expect(leadFirst(roots).map((r) => r.id)).toEqual(["lead", "other"]);
  });

  it("flattens the lead then its subs indented", () => {
    const lead = node({ id: "lead", role: "lead", children: [node({ id: "s1", role: "sub", parent_id: "lead", task_title: "gripper" })] });
    const rows = agentRows([lead]);
    expect(rows.map((r) => [r.run.id, r.depth])).toEqual([["lead", 0], ["s1", 1]]);
  });

  it("labels a sub by its task title, a lead by its run title", () => {
    expect(agentRowTitle(node({ id: "s1", role: "sub", task_title: "design the gripper" }))).toBe("design the gripper");
    expect(agentRowTitle(node({ id: "lead", role: "lead", title: "Arm build" }))).toBe("Arm build");
    // a sub with no task title falls back to the run title (taskWords drops filler like "a")
    expect(agentRowTitle(node({ id: "s2", role: "sub", task: "do a thing" }))).toBe("do thing");
  });
});
