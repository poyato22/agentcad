// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import WebSocket from "ws";
import { loadScenario, startFakeCore, type FakeCore } from "./server";
import { CoreClient, CoreError } from "../../src/api/client";
import type { MisesEvent } from "../../src/api/types";

let core: FakeCore;
let client: CoreClient;
const RID = "b7c79a57cbb4";

function wsUrl(token: string, runId?: string) {
  const u = new URL(core.url.replace("http", "ws") + "/events");
  u.searchParams.set("token", token);
  if (runId) u.searchParams.set("run_id", runId);
  return u.toString();
}

function openWs(token: string, runId?: string): Promise<{ ws: WebSocket; events: MisesEvent[] }> {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl(token, runId));
    const events: MisesEvent[] = [];
    ws.on("message", (d) => events.push(JSON.parse(String(d)) as MisesEvent));
    ws.on("open", () => resolve({ ws, events }));
    ws.on("error", reject);
  });
}

async function failure(p: Promise<unknown>): Promise<CoreError> {
  const e = await p.then(() => null, (err: unknown) => err);
  if (!(e instanceof CoreError)) throw new Error(`expected a CoreError, got ${String(e)}`);
  return e;
}

const until = async (cond: () => boolean) => {
  for (let i = 0; i < 200 && !cond(); i++) await new Promise((r) => setTimeout(r, 5));
  expect(cond()).toBe(true);
};

describe("fake core", () => {
  beforeEach(async () => {
    core = await startFakeCore({ port: 0, token: "t0k", scenario: "base" });
    client = new CoreClient({ baseUrl: core.url, wsUrl: core.url.replace("http", "ws") + "/events", token: core.token, owned: false });
  });
  afterEach(async () => { await core.stop(); });

  it("requires_bearer_with_real_401_body", async () => {
    const r = await fetch(core.url + "/runs");
    expect(r.status).toBe(401);
    expect(r.headers.get("www-authenticate")).toBe("Bearer");
    expect(await r.json()).toEqual({ error: "unauthorized", message: "missing or bad token" });
    const bad = await fetch(core.url + "/health", { headers: { Authorization: "Bearer nope" } });
    expect(bad.status).toBe(401);
  });

  it("cors_preflight_without_token_for_allowed_origin_only", async () => {
    const ok = await fetch(core.url + "/runs", { method: "OPTIONS", headers: { Origin: "http://localhost:1420", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type" } });
    expect(ok.status).toBe(200);
    expect(ok.headers.get("access-control-allow-origin")).toBe("http://localhost:1420");
    expect(ok.headers.get("access-control-allow-headers")?.toLowerCase()).toContain("authorization");
    const evil = await fetch(core.url + "/runs", { method: "OPTIONS", headers: { Origin: "http://evil.example", "Access-Control-Request-Method": "POST" } });
    expect(evil.status).toBe(400);
    expect(evil.headers.get("access-control-allow-origin")).toBeNull();
    const simple = await fetch(core.url + "/health", { headers: { Origin: "tauri://localhost", Authorization: "Bearer t0k" } });
    expect(simple.headers.get("access-control-allow-origin")).toBe("tauri://localhost");
  });

  it("cors_preflight_allows_if_none_match_and_exposes_etag_like_the_core", async () => {
    const pre = await fetch(core.url + "/parts/mount_plate/model.glb", { method: "OPTIONS", headers: { Origin: "http://localhost:1420", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization,if-none-match" } });
    expect(pre.status).toBe(200);
    expect(pre.headers.get("access-control-allow-headers")?.toLowerCase()).toContain("if-none-match");
    // like Starlette's CORSMiddleware: a header outside the allow list fails the preflight
    const odd = await fetch(core.url + "/runs", { method: "OPTIONS", headers: { Origin: "http://localhost:1420", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "x-evil" } });
    expect(odd.status).toBe(400);
    const glb = await fetch(core.url + "/parts/mount_plate/model.glb", { headers: { Origin: "http://localhost:1420", Authorization: "Bearer t0k" } });
    expect(glb.headers.get("access-control-expose-headers")).toBe("ETag");
  });

  it("if_none_match_matches_like_the_core", async () => {
    const auth = { Authorization: "Bearer t0k" };
    const first = await fetch(core.url + "/parts/mount_plate/model.glb", { headers: auth });
    const etag = first.headers.get("etag") as string;
    await first.arrayBuffer();
    for (const inm of [etag, `W/${etag}`, `"nope", ${etag}`, "*"]) {
      const r = await fetch(core.url + "/parts/mount_plate/model.glb", { headers: { ...auth, "If-None-Match": inm } });
      expect(r.status, inm).toBe(304);
    }
    const miss = await fetch(core.url + "/parts/mount_plate/model.glb", { headers: { ...auth, "If-None-Match": '"other"' } });
    expect(miss.status).toBe(200);
  });

  it("scenario_triggers_play_for_the_new_run_and_after_a_decision", async () => {
    const sc = loadScenario("base");
    sc.on = {
      create_run: [{ at_ms: 0, event: { type: "tool_call", run_id: "$run", data: { call_id: "k1", name: "mirror_measure", input: {} } },
        steps: [{ kind: "tool_call", run_id: "$run", payload: { call_id: "k1", name: "mirror_measure", input: {} } }] }],
      decide: [{ at_ms: 0, event: { type: "run_finished", run_id: "$run", data: { status: "done", stop_reason: "end_turn", error: null, final_text: "ok" } } }],
    };
    core.reset(sc);
    const { ws, events } = await openWs("t0k");
    const { run_id } = await client.createRun({ agent: "designer", prompt: "go" });
    await until(() => events.some((e) => e.type === "tool_call" && e.run_id === run_id));
    expect((await client.steps(run_id)).map((s) => s.kind)).toEqual(["message", "tool_call"]);
    const a = (await client.pendingApprovals())[0];
    await client.decide(a.id, { payload_hash: a.payload_hash, approve: true });
    await until(() => events.some((e) => e.type === "run_finished" && e.run_id === a.run_id));
    expect((await client.getRun(a.run_id)).status).toBe("done");
    ws.close();
  });

  it("call_log_records_if_none_match", async () => {
    await fetch(core.url + "/parts/mount_plate/model.glb", { headers: { Authorization: "Bearer t0k", "If-None-Match": '"x"' } });
    expect(core.state.calls.at(-1)?.ifNoneMatch).toBe('"x"');
  });

  it("serves_base_scenario_through_the_typed_client", async () => {
    expect(await client.health()).toEqual({ ok: true });
    const runs = await client.listRuns();
    expect(runs.map((r) => r.agent)).toEqual(["designer"]);
    const steps = await client.steps(RID);
    expect(steps.map((s) => s.kind)).toContain("diff");
    expect((await client.steps(RID, 10)).every((s) => s.id > 10)).toBe(true);
    const pending = await client.pendingApprovals();
    expect(pending).toHaveLength(1);
    expect(pending[0].action).toBe("fixture_merge");
    expect((await client.agents()).map((a) => a.name)).toContain("designer");
    expect((await client.models()).default).toBe("anthropic/claude-sonnet-5-5");
    const status = await client.status();
    expect(status.runs_running).toBe(1);
    expect(status.approvals_pending).toBe(1);
    expect((await client.parts())[0].part_id).toBe("mount_plate");
  });

  it("decide_checks_hash_and_first_decision_wins", async () => {
    const [a] = await client.pendingApprovals();
    const stale = await client.decide(a.id, { payload_hash: "0".repeat(64), approve: true }).catch((e: unknown) => e);
    expect(stale).toBeInstanceOf(CoreError);
    expect((stale as CoreError).code).toBe("stale_version");
    expect((stale as CoreError).status).toBe(409);
    const ok = await client.decide(a.id, { payload_hash: a.payload_hash, approve: true });
    expect(ok.status).toBe("approved");
    expect(ok.decided_by).toBe("local-user");
    const again = await client.decide(a.id, { payload_hash: a.payload_hash, approve: false });
    expect(again.status).toBe("approved");
    expect(await client.pendingApprovals()).toEqual([]);
    const calls = await (await fetch(core.url + "/__fake/calls", { headers: { Authorization: "Bearer t0k" } })).json() as { method: string; path: string; body: unknown }[];
    const decides = calls.filter((c) => c.path.endsWith("/decide"));
    expect(decides).toHaveLength(3);
    expect(decides[1].body).toEqual({ payload_hash: a.payload_hash, approve: true });
  });

  it("errors_use_real_status_codes", async () => {
    const e404 = await failure(client.getRun("nope"));
    expect(e404.code).toBe("not_found");
    expect(e404.status).toBe(404);
    const busy = await failure(client.message(RID, "hi"));
    expect(busy.code).toBe("invalid_input");
    expect(busy.status).toBe(400);
    expect(busy.message).toContain("still working");
    const unknownAgent = await failure(client.createRun({ agent: "ghost", prompt: "x" }));
    expect(unknownAgent.status).toBe(404);
    const noKey = await failure(client.createRun({ agent: "designer", prompt: "x", model_ref: "openai/gpt-5" }));
    expect(noKey.status).toBe(403);
    expect(noKey.code).toBe("denied");
    expect(noKey.hint).toContain("mises keys set openai");
    const badRef = await failure(client.createRun({ agent: "designer", prompt: "x", model_ref: "nonsense" }));
    expect(badRef.status).toBe(400);
    const v = await fetch(core.url + "/runs", { method: "POST", headers: { Authorization: "Bearer t0k", "Content-Type": "application/json" }, body: "{}" });
    expect(v.status).toBe(422);
    expect(Array.isArray(((await v.json()) as { detail: unknown }).detail)).toBe(true);
    const q = await failure(client.answer("nope", "x"));
    expect(q.status).toBe(404);
    const filt = await fetch(core.url + "/approvals?status=approved", { headers: { Authorization: "Bearer t0k" } });
    expect(filt.status).toBe(400);
    expect(await filt.json()).toEqual({ error: "invalid_input", message: "unsupported status filter: approved", hint: "only status=pending is supported", retryable: false });
  });

  it("ws_rejects_bad_token_with_4401", async () => {
    const code = await new Promise<number>((resolve) => {
      const ws = new WebSocket(wsUrl("wrong"));
      ws.on("close", (c) => resolve(c));
    });
    expect(code).toBe(4401);
  });

  it("emit_broadcasts_and_appends_steps_and_approvals", async () => {
    const { ws, events } = await openWs("t0k");
    const res = await fetch(core.url + "/__fake/emit", {
      method: "POST", headers: { Authorization: "Bearer t0k", "Content-Type": "application/json" },
      body: JSON.stringify({ event: { type: "tool_call", run_id: RID, data: { call_id: "c9", name: "mirror_measure", input: {} } },
        steps: [{ run_id: RID, kind: "tool_call", payload: { call_id: "c9", name: "mirror_measure", input: {} } }] }),
    });
    expect(res.status).toBe(200);
    await until(() => events.length === 1);
    expect(events[0].type).toBe("tool_call");
    expect(typeof events[0].ts).toBe("number");
    const steps = await client.steps(RID, 12);
    expect(steps).toHaveLength(1);
    expect(steps[0].id).toBe(13);
    ws.close();
  });

  it("a question or approval puts the run in waiting_user / waiting_approval; answering resumes it", async () => {
    const { ws, events } = await openWs("t0k");
    const states = () => events.filter((e) => e.type === "agent_activity" && e.run_id === RID).map((e) => e.data.state);
    core.emit({ type: "question_asked", run_id: RID, data: { question_id: "q_w", question: "Which?", options: ["a"] } });
    await until(() => states().includes("waiting_user"));
    await client.answer("q_w", "a");
    await until(() => states().includes("thinking"));
    core.emit({ type: "approval_requested", run_id: RID, data: { id: "ap_w" } });
    await until(() => states().includes("waiting_approval"));
    expect(states()).toEqual(["waiting_user", "thinking", "waiting_approval"]);
    ws.close();
  });

  it("POST /runs files the run under the explicit project_id; an unknown build is 404", async () => {
    const builds = await client.listBuilds();
    const target = builds.at(-1)!.id;
    const { run_id } = await client.createRun({ agent: "designer", prompt: "go", project_id: target });
    expect((await client.getRun(run_id)).project_id).toBe(target);
    await expect(client.createRun({ agent: "designer", prompt: "go", project_id: "nope" })).rejects.toMatchObject({ status: 404 });
  });

  it("ws_filters_by_run_id", async () => {
    const { ws, events } = await openWs("t0k", "other");
    core.emit({ type: "note", run_id: RID, data: { text: "a" } });
    core.emit({ type: "note", run_id: "other", data: { text: "b" } });
    await until(() => events.length === 1);
    expect(events[0].data).toEqual({ text: "b" });
    ws.close();
  });

  it("cancel_all_cancels_running_runs_and_emits_run_finished", async () => {
    const { ws, events } = await openWs("t0k");
    expect(await client.cancelAll()).toEqual({ cancelled: [RID], failed: [] });
    await until(() => events.some((e) => e.type === "run_finished"));
    expect((await client.getRun(RID)).status).toBe("cancelled");
    expect(await client.pendingApprovals()).toEqual([]);
    // like the real core: the cancelled run's pending approval is rejected by the system
    const a = core.state.approvals.get("a332e73aa8f2");
    expect(a?.status).toBe("rejected");
    expect(a?.decided_by).toBe("system:cancel");
    expect(typeof a?.decided_at).toBe("number");
    expect(await client.cancelAll()).toEqual({ cancelled: [], failed: [] });
    ws.close();
  });

  it("create_run_and_questions", async () => {
    const { run_id } = await client.createRun({ agent: "designer", prompt: "make a bracket" });
    expect((await client.getRun(run_id)).task).toBe("make a bracket");
    expect((await client.steps(run_id))[0].payload).toEqual({ role: "user", text: "make a bracket" });
    core.emit({ type: "question_asked", run_id, data: { question_id: "q1", question: "Which side?", options: ["left"] } });
    expect(await client.answer("q1", "left")).toEqual({ ok: true });
    await expect(client.answer("q1", "left")).rejects.toBeInstanceOf(CoreError);
  });

  it("external_runs_carry_the_flag_and_refuse_messages_like_the_core", async () => {
    const res = await fetch(`${core.url}/runs/external`, {
      method: "POST", headers: { Authorization: `Bearer ${core.token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ agent: "codex", provider: "openai", model: "gpt-x" }),
    });
    const { run_id } = (await res.json()) as { run_id: string };
    expect((await client.getRun(run_id)).external).toBe(true);
    expect((await client.getRun(RID)).external).toBe(false);
    core.emit({ type: "run_finished", run_id, data: { status: "done", stop_reason: "end_turn", error: null, final_text: "", external: true } });
    const e = await failure(client.message(run_id, "hi"));
    expect([e.code, e.message]).toEqual(["invalid_input", "run is driven by an external agent"]);
  });

  it("glb_route_validates_ids", async () => {
    const bad = await fetch(core.url + "/parts/Bad1/model.glb", { headers: { Authorization: "Bearer t0k" } });
    expect(bad.status).toBe(400);
    const missing = await fetch(core.url + "/parts/no_such_part/model.glb", { headers: { Authorization: "Bearer t0k" } });
    expect(missing.status).toBe(404);
  });

  it("reset_restores_scenario_and_clears_calls", async () => {
    await client.cancelAll();
    await fetch(core.url + "/__fake/reset", { method: "POST", headers: { Authorization: "Bearer t0k", "Content-Type": "application/json" }, body: JSON.stringify({ scenario: "base" }) });
    expect((await client.getRun(RID)).status).toBe("running");
    const calls = await (await fetch(core.url + "/__fake/calls", { headers: { Authorization: "Bearer t0k" } })).json() as unknown[];
    expect(calls).toHaveLength(1); // only the getRun above
  });

  it("control_routes_need_token", async () => {
    expect((await fetch(core.url + "/__fake/calls")).status).toBe(401);
  });

  it("rotate_token_closes_sockets_and_rejects_old_token", async () => {
    const { ws } = await openWs("t0k");
    const closed = new Promise<number>((r) => ws.on("close", (c) => r(c)));
    await fetch(core.url + "/__fake/rotate_token", { method: "POST", headers: { Authorization: "Bearer t0k", "Content-Type": "application/json" }, body: JSON.stringify({ token: "fresh" }) });
    expect(await closed).not.toBe(4401);
    expect(core.token).toBe("fresh");
    expect((await fetch(core.url + "/health", { headers: { Authorization: "Bearer t0k" } })).status).toBe(401);
    expect((await fetch(core.url + "/health", { headers: { Authorization: "Bearer fresh" } })).status).toBe(200);
  });
});
