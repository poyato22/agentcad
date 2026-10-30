import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { agentsViewSchema } from "../../api/schemas";
import type { AgentNode, AgentsView } from "../../api/types";
import { agentRowTitle, allNodes, viewRows, waitingText } from "./panelModel";

function node(over: Partial<AgentNode> & { id: string }): AgentNode {
  return {
    parent_id: null, agent: "designer", provider: "fake", model: "m", task: `task ${over.id}`,
    status: "running", branch: null, onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0,
    created_at: 1, updated_at: 1, queued: [], activity: null, children: [], ...over,
  };
}

/** The real U3c answer (core scripts/dump_agents_fixtures.py). */
const REAL = agentsViewSchema.parse(JSON.parse(readFileSync(resolve(import.meta.dirname, "../../../../core/tests/fixtures/ui/plan7/project_agents.json"), "utf8")));

describe("agents panel model", () => {
  it("rows: the lead and its sub-tree indented, then the other runs", () => {
    const view: AgentsView = {
      project_id: "b1",
      lead: node({ id: "lead", role: "lead", children: [node({ id: "s1", role: "sub", parent_id: "lead", title: "Design motor mount" })] }),
      others: [node({ id: "o1", agent: "verifier" }), node({ id: "o2", archived: true })],
    };
    const r = viewRows(view);
    expect(r.lead.map((x) => [x.node.id, x.depth])).toEqual([["lead", 0], ["s1", 1]]);
    expect(r.others.map((x) => x.node.id)).toEqual(["o1"]);              // archived left out
    expect(viewRows(view, new Set(["lead"])).lead.map((x) => x.node.id)).toEqual(["lead"]);   // folded
    expect(viewRows(view, new Set(), true).others).toHaveLength(2);
    expect(r.earlier).toEqual([]);                                       // an archived non-lead isn't an earlier lead
  });

  it("an archived lead (retired after a core restart) shows as an earlier lead with its sub-agents", () => {
    const old = node({ id: "old", role: "lead", archived: true, status: "done",
      children: [node({ id: "s0", role: "sub", parent_id: "old", title: "Design arm base" })] });
    const view: AgentsView = { project_id: "b1", lead: node({ id: "new", role: "lead" }), others: [old] };
    const r = viewRows(view);
    expect(r.lead.map((x) => x.node.id)).toEqual(["new"]);
    expect(r.others).toEqual([]);
    expect(r.earlier.map((x) => [x.node.id, x.depth])).toEqual([["old", 0], ["s0", 1]]);
  });

  it("labels: the user's rename, else the core's task title, else the agent", () => {
    expect(agentRowTitle({ title: "My arm", task: "Build me a drone", agent: "engineer" })).toBe("My arm");
    expect(agentRowTitle({ title: null, task: "Design motor mount", agent: "designer" })).toBe("Design motor mount");
    expect(agentRowTitle({ title: null, task: "", agent: "designer" })).toBe("designer");
  });

  it("reads the real core's agents view: lead, its titled sub-agent, and the other run", () => {
    expect(REAL.lead?.role).toBe("lead");
    const [sub] = REAL.lead?.children ?? [];
    expect(sub.role).toBe("sub");
    expect(agentRowTitle(sub)).toBe("Design motor mount");
    expect(REAL.others).toHaveLength(1);
    expect(allNodes(REAL)).toHaveLength(3);
  });

  it("says how many messages wait for the lead", () => {
    expect(waitingText({ queued: [] })).toBeNull();
    expect(waitingText({ queued: [{ message_id: "m", text: "x", position: 1 }] })).toBe("1 message waiting");
    expect(waitingText({ queued: [{ message_id: "m", text: "x", position: 1 }, { message_id: "n", text: "y", position: 2 }] })).toBe("2 messages waiting");
  });
});
