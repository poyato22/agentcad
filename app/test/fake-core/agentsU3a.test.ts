// @vitest-environment node
// Plan 7 (U3c) contracts the fake core stands in for: the prompt router (POST /runs with no
// `agent`), lead/sub roles on run_view, and GET /projects/{id}/agents.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { AgentRun } from "../../src/api/types";
import { routeAgent, startFakeCore, type FakeCore } from "./server";

let core: FakeCore;

async function call(method: string, path: string, body?: unknown): Promise<{ status: number; json: unknown }> {
  const headers: Record<string, string> = { Authorization: `Bearer ${core.token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(core.url + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  return { status: r.status, json: r.headers.get("content-type")?.includes("json") ? await r.json() : null };
}

beforeEach(async () => { core = await startFakeCore({ port: 0, token: "t", scenario: "base" }); core.reset("base"); });
afterEach(async () => { await core.stop(); });

describe("routeAgent (the core's cheap classifier, as a stand-in)", () => {
  const names = ["designer", "engineer", "sim", "verifier", "drawing-import"];
  it("picks engineer for a system build, with a reason and confidence", () => {
    const r = routeAgent("make a robotic arm with servos", names);
    expect(r.agent).toBe("engineer");
    expect(r.reason).toBe("system build");
    expect(r.confidence).toBeGreaterThan(0.5);
  });
  it("picks designer for a single part, sim for analyze, verifier for review, drawing-import for a drawing", () => {
    expect(routeAgent("thicken this bracket wall", names).agent).toBe("designer");
    expect(routeAgent("analyze the stress under load", names).agent).toBe("sim");
    expect(routeAgent("review the design for errors", names).agent).toBe("verifier");
    expect(routeAgent("import this DXF drawing", names).agent).toBe("drawing-import");
  });
  it("falls back to an available agent when its first choice isn't registered", () => {
    expect(routeAgent("make a robotic arm", ["designer"]).agent).toBe("designer");
  });
});

describe("POST /runs with no agent routes, and the answer carries `routed`", () => {
  it("routes, marks the run a lead, and echoes the choice", async () => {
    const { status, json } = await call("POST", "/runs", { prompt: "design a gearbox for the drivetrain" });
    expect(status).toBe(200);
    const body = json as { run_id: string; routed?: { agent: string; reason: string; confidence: number } };
    expect(body.routed?.reason).toBe("system build");
    expect(body.routed?.agent).toBeTruthy();
    const read = (await call("GET", `/runs/${body.run_id}`)).json as AgentRun;
    expect(read.role).toBe("lead");
    expect(read.routed?.reason).toBe("system build");
  });
  it("an explicit agent keeps the pre-Plan-7 shape (no routed, no role)", async () => {
    const { json } = await call("POST", "/runs", { agent: "designer", prompt: "x" });
    const body = json as { run_id: string; routed?: unknown };
    expect(body.routed).toBeUndefined();
    const read = (await call("GET", `/runs/${body.run_id}`)).json as AgentRun;
    expect(read.role ?? null).toBeNull();
  });
});

describe("GET /projects/{id}/agents: the lead and its sub-tree", () => {
  it("returns the lead first with its subs nested, each carrying role and task title", async () => {
    const pid = core.state.builds.builds[0].id;
    const t = 2_000_000;
    const base: AgentRun = {
      id: "", parent_id: null, agent: "engineer", provider: "fake", model: "m", task: "build the arm",
      status: "running", branch: null, onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0,
      created_at: t, updated_at: t, owner_pid: null, external: false, session_ref: null, project_id: pid,
    };
    core.state.runs.set("lead", { ...base, id: "lead", role: "lead" });
    core.state.runs.set("sub1", { ...base, id: "sub1", parent_id: "lead", agent: "designer", role: "sub", task_title: "design the gripper" });
    const { status, json } = await call("GET", `/projects/${pid}/agents`);
    expect(status).toBe(200);
    type Node = { id: string; role: string; task_title: string | null; children: Node[] };
    const roots = json as Node[];
    expect(roots[0].id).toBe("lead");
    expect(roots[0].role).toBe("lead");
    expect(roots[0].children.map((c) => c.id)).toContain("sub1");
    const sub = roots[0].children.find((c) => c.id === "sub1");
    expect(sub?.role).toBe("sub");
    expect(sub?.task_title).toBe("design the gripper");
  });
  it("404s on an unknown build", async () => {
    expect((await call("GET", "/projects/nope/agents")).status).toBe(404);
  });
});
