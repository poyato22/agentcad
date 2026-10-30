import { describe, expect, it } from "vitest";
import type { AgentRun } from "../../api/types";
import { switcherRows } from "./RunSwitcher";

const run = (id: string, over: Partial<AgentRun> = {}): AgentRun => ({
  id, parent_id: null, agent: "designer", provider: "fake", model: "m", task: id, status: "done", branch: null,
  onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0, created_at: 1, updated_at: 1, ...over,
});

describe("run switcher order (m4)", () => {
  it("puts the live lead first with its sub-agents indented, then the rest", () => {
    const list = [run("sub2", { parent_id: "L", role: "sub" }), run("other"), run("L", { role: "lead" }), run("sub1", { parent_id: "L", role: "sub" }), run("old", { role: "lead", archived: true })];
    expect(switcherRows(list).map((r) => [r.run.id, r.depth])).toEqual([["L", 0], ["sub2", 1], ["sub1", 1], ["other", 0], ["old", 0]]);
  });
});
