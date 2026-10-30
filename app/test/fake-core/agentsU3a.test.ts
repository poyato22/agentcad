// @vitest-environment node
// Plan 7 (U3c) contracts the fake core stands in for: the prompt router (POST /runs with no
// `agent`), one lead per build with its message queue, lead/sub roles on run_view, and
// GET /projects/{id}/agents. The shapes themselves are checked against the real core's dumps in
// test/contract.plan7.test.ts.
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
  it("picks engineer for a system build, with the core's reason", () => {
    const r = routeAgent("make a robotic arm with servos", names);
    expect([r.agent, r.reason]).toEqual(["engineer", "system build"]);
    expect(r.confidence).toBeGreaterThan(0.5);
  });
  it("picks designer for a single part, sim to test, verifier to review, drawing-import for a drawing", () => {
    expect(routeAgent("thicken this bracket wall", names).agent).toBe("designer");
    expect(routeAgent("analyze the stress under load", names).reason).toBe("test / analyze");
    expect(routeAgent("review the design for errors", names).reason).toBe("check / review");
    expect(routeAgent("import this DXF drawing", names).agent).toBe("drawing-import");
  });
  it("falls back to an available agent when its first choice isn't registered", () => {
    expect(routeAgent("make a robotic arm", ["designer"]).agent).toBe("designer");
  });
});

describe("POST /runs", () => {
  it("with no agent: routes, starts the build's lead, answers routed / new_lead / queued", async () => {
    const { status, json } = await call("POST", "/runs", { prompt: "design a gearbox for the drivetrain" });
    expect(status).toBe(200);
    const body = json as { run_id: string; routed: { reason: string }; role: string; new_lead: boolean; queued: boolean; notes: string[] };
    expect([body.routed.reason, body.role, body.new_lead, body.queued, body.notes]).toEqual(["system build", "lead", true, false, []]);
    const read = (await call("GET", `/runs/${body.run_id}`)).json as AgentRun;
    expect([read.role, read.routed?.reason]).toEqual(["lead", "system build"]);
  });
  it("an explicit agent is an ordinary run: {run_id} only, role and routed null", async () => {
    const { json } = await call("POST", "/runs", { agent: "designer", prompt: "x" });
    expect(Object.keys(json as object)).toEqual(["run_id"]);
    const read = (await call("GET", `/runs/${(json as { run_id: string }).run_id}`)).json as AgentRun;
    expect([read.role, read.routed]).toEqual([null, null]);
  });
  it("follow-ups go to the same lead; while it works they queue; past 20 waiting it's a 429", async () => {
    core.state.runs.clear();
    const a = (await call("POST", "/runs", { prompt: "build a drone" })).json as { run_id: string };
    const b = (await call("POST", "/runs", { prompt: "add landing gear" })).json as { run_id: string; new_lead: boolean; queued: boolean; message_id: string; position: number };
    expect([b.run_id, b.new_lead, b.queued, b.position]).toEqual([a.run_id, false, true, 1]);
    for (let i = 0; i < 19; i++) await call("POST", "/runs", { prompt: `x${i}` });
    const full = await call("POST", "/runs", { prompt: "too many" });
    expect(full.status).toBe(429);
    expect((full.json as { error: string }).error).toBe("invalid_input");
    // the lead's turn ends: its first waiting message is delivered and it works again
    core.emit({ type: "run_finished", run_id: a.run_id, data: { status: "done" } });
    await new Promise((r) => setTimeout(r, 20));
    const steps = (await call("GET", `/runs/${a.run_id}/steps?after_id=0`)).json as { payload: Record<string, unknown> }[];
    expect(steps.some((s) => (s.payload.queued_message_delivered as { message_id?: string } | undefined)?.message_id === b.message_id)).toBe(true);
    expect(core.state.runs.get(a.run_id)?.status).toBe("running");
  });
  it("cancelling the lead drops its waiting messages", async () => {
    core.state.runs.clear();
    const a = (await call("POST", "/runs", { prompt: "build a drone" })).json as { run_id: string };
    await call("POST", "/runs", { prompt: "add landing gear" });
    await call("POST", `/runs/${a.run_id}/cancel`);
    await new Promise((r) => setTimeout(r, 20));
    const steps = (await call("GET", `/runs/${a.run_id}/steps?after_id=0`)).json as { payload: Record<string, unknown> }[];
    expect(steps.some((s) => "queued_message_dropped" in s.payload)).toBe(true);
  });
});

describe("GET /projects/{id}/agents: {project_id, lead, others} (U3c lead.agents_view)", () => {
  it("the lead with its sub-agents (task = title), and the build's other runs", async () => {
    const pid = core.state.builds.builds[0].id;
    const t = 2_000_000;
    const base: AgentRun = {
      id: "", parent_id: null, agent: "engineer", provider: "fake", model: "m", task: "build the arm",
      status: "running", branch: null, onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0,
      created_at: t, updated_at: t, owner_pid: null, external: false, session_ref: null, project_id: pid,
    };
    core.state.runs.clear();
    core.state.runs.set("lead", { ...base, id: "lead", role: "lead" });
    core.state.runs.set("sub1", { ...base, id: "sub1", parent_id: "lead", agent: "designer", role: "sub", title: "design the gripper", created_at: t + 1 });
    core.state.runs.set("other", { ...base, id: "other", agent: "verifier", task: "check" });
    const { status, json } = await call("GET", `/projects/${pid}/agents`);
    expect(status).toBe(200);
    type Node = { id: string; role: string | null; task: string; children: Node[]; queued: unknown[] };
    const view = json as { project_id: string; lead: Node | null; others: Node[] };
    expect(view.project_id).toBe(pid);
    expect(view.lead?.id).toBe("lead");
    expect(view.lead?.children.map((c) => [c.id, c.role, c.task])).toEqual([["sub1", "sub", "design the gripper"]]);
    expect(view.others.map((o) => o.id)).toEqual(["other"]);
    expect(view.lead?.queued).toEqual([]);
  });
  it("a build with no lead answers lead: null; an unknown build 404s", async () => {
    const pid = core.state.builds.builds[0].id;
    core.state.runs.clear();
    expect(((await call("GET", `/projects/${pid}/agents`)).json as { lead: unknown }).lead).toBeNull();
    expect((await call("GET", "/projects/nope/agents")).status).toBe(404);
  });
});
