// @vitest-environment node
// The Plan 5 (C1) run-management and agent-CRUD routes the fake core serves, matching the real
// core's contract (projects.run_view / delete_reason / cleanup_candidates; projects_api agents).
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { AgentRun, Approval } from "../../src/api/types";
import { startFakeCore, type FakeCore } from "./server";

let core: FakeCore;

async function call(method: string, path: string, body?: unknown): Promise<{ status: number; json: unknown }> {
  const headers: Record<string, string> = { Authorization: `Bearer ${core.token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(core.url + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  return { status: r.status, json: r.headers.get("content-type")?.includes("json") ? await r.json() : null };
}

function seedRun(over: Partial<AgentRun> & { id: string }): AgentRun {
  const t = 1_000_000;
  const run: AgentRun = {
    parent_id: null, agent: "designer", provider: "fake", model: "m", task: "t",
    status: "done", branch: null, onshape_calls: 0, input_tokens: 10, output_tokens: 5, usd: 0.02,
    created_at: t, updated_at: t, owner_pid: null, external: false, session_ref: null, ...over,
  };
  core.state.runs.set(run.id, run);
  return run;
}

beforeEach(async () => { core = await startFakeCore({ port: 0, token: "t", scenario: "base" }); core.reset("base"); core.state.runs.clear(); });
afterEach(async () => { await core.stop(); });

describe("GET /runs/tree", () => {
  it("nests workers under their orchestrator and enriches with run_view fields", async () => {
    seedRun({ id: "orch", agent: "orchestrator" });
    seedRun({ id: "w1", parent_id: "orch", agent: "designer" });
    seedRun({ id: "w2", parent_id: "orch", agent: "verifier" });
    seedRun({ id: "solo" });
    const { status, json } = await call("GET", "/runs/tree");
    expect(status).toBe(200);
    type Node = { id: string; parent_run_id: string | null; cost: { tokens: number }; children: Node[] };
    const roots = json as Node[];
    const ids = roots.map((r) => r.id).sort();
    expect(ids).toEqual(["orch", "solo"]);
    const orch = roots.find((r) => r.id === "orch")!;
    expect(orch.children.map((c) => c.id).sort()).toEqual(["w1", "w2"]);
    expect(orch.cost.tokens).toBe(15);              // input 10 + output 5
    expect(orch.children[0].parent_run_id).toBe("orch");
  });
});

describe("DELETE /runs/{id}", () => {
  it("deletes a finished run and its steps", async () => {
    seedRun({ id: "r1" });
    core.state.steps.push({ id: 1, run_id: "r1", ts: 0, kind: "message", payload: {}, evidence_hash: "", prev_hash: null, collapsed: false });
    expect((await call("DELETE", "/runs/r1")).json).toEqual({ ok: true });
    expect(core.state.runs.has("r1")).toBe(false);
    expect(core.state.steps.some((s) => s.run_id === "r1")).toBe(false);
  });

  it("refuses a running run with reason 'running'", async () => {
    seedRun({ id: "r1", status: "running" });
    const { status, json } = await call("DELETE", "/runs/r1");
    expect(status).toBe(409);
    expect(json).toEqual({ reason: "running", children: [] });
    expect(core.state.runs.has("r1")).toBe(true);
  });

  it("refuses when a worker is still running, naming the blocking child", async () => {
    seedRun({ id: "orch" });
    seedRun({ id: "w1", parent_id: "orch", status: "running" });
    const { status, json } = await call("DELETE", "/runs/orch");
    expect(status).toBe(409);
    expect(json).toEqual({ reason: "running", children: ["w1"] });
  });

  it("refuses a run awaiting approval with reason 'pending_approval'", async () => {
    seedRun({ id: "r1" });
    const ap: Approval = {
      id: "a1", run_id: "r1", action: "merge", payload: {}, payload_hash: "h", microversion: null,
      deltas: {}, status: "pending", expires_at: 9e12, decided_by: null, decided_at: null, edited_payload: null,
    };
    core.state.approvals.set("a1", ap);
    const { status, json } = await call("DELETE", "/runs/r1");
    expect(status).toBe(409);
    expect(json).toEqual({ reason: "pending_approval", children: [] });
  });
});

describe("POST /runs/cleanup", () => {
  it("dry-run lists finished runs without deleting; real run removes them", async () => {
    seedRun({ id: "done1", status: "done" });
    seedRun({ id: "fail1", status: "failed" });
    seedRun({ id: "run1", status: "running" });
    const dry = (await call("POST", "/runs/cleanup", { dry_run: true })).json as { ids: string[]; count: number };
    expect(dry.ids.sort()).toEqual(["done1", "fail1"]);
    expect(dry.count).toBe(2);
    expect(core.state.runs.has("done1")).toBe(true);           // nothing removed on a dry run
    const real = (await call("POST", "/runs/cleanup", { dry_run: false, status_in: ["done"] })).json as { ids: string[] };
    expect(real.ids).toEqual(["done1"]);
    expect(core.state.runs.has("done1")).toBe(false);
    expect(core.state.runs.has("fail1")).toBe(true);
  });
});

describe("PATCH /runs/{id}", () => {
  it("renames, pins and archives", async () => {
    seedRun({ id: "r1" });
    const out = (await call("PATCH", "/runs/r1", { title: "Upright v2", pinned: true, archived: true })).json as AgentRun;
    expect(out.title).toBe("Upright v2");
    expect(out.pinned).toBe(true);
    expect(out.archived).toBe(true);
    expect(core.state.runs.get("r1")?.title).toBe("Upright v2");
  });
});

describe("agents CRUD", () => {
  it("creates, lists, edits and deletes a custom agent; refuses editing a built-in", async () => {
    // a Plan 5 core marks every agent; opt the scenario's built-ins into that shape
    core.state.agents = core.state.agents.map((a) => ({ ...a, builtin: true, tools: null }));
    const created = (await call("POST", "/agents", { name: "ribber", description: "adds ribs", model: "inherit", tools: ["mirror_measure"] })).json as { name: string; builtin: boolean };
    expect(created).toMatchObject({ name: "ribber", builtin: false });

    const list = (await call("GET", "/agents")).json as { name: string; builtin: boolean; hidden: boolean }[];
    const ribber = list.find((a) => a.name === "ribber")!;
    expect(ribber.builtin).toBe(false);
    expect(list.find((a) => a.name === "designer")?.builtin).toBe(true);

    const edited = (await call("PUT", "/agents/ribber", { description: "adds ribs fast", model: "inherit", tools: null })).json as { description: string };
    expect(edited.description).toBe("adds ribs fast");

    // a built-in cannot be edited or deleted
    expect((await call("PUT", "/agents/designer", { description: "x" })).status).toBe(403);
    expect((await call("DELETE", "/agents/designer")).status).toBe(403);

    expect((await call("DELETE", "/agents/ribber")).json).toEqual({ ok: true });
    expect(((await call("GET", "/agents")).json as { name: string }[]).some((a) => a.name === "ribber")).toBe(false);
  });

  it("rejects a duplicate or malformed name", async () => {
    expect((await call("POST", "/agents", { name: "designer" })).status).toBe(400);
    expect((await call("POST", "/agents", { name: "Bad Name" })).status).toBe(400);
  });
});
