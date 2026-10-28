// @vitest-environment node
// Plan 4 routes the fake core adds for the agent-UX work (shapes from the Task 2 core brief):
// GET /local/status, POST /local/start, POST /local/pull (+ `local_pull` events), /status
// `parallel`, profile `max_parallel_agents: "auto"` and `theme`, and runs beyond the parallel
// limit queueing (status "queued", event `run_queued`).
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import WebSocket from "ws";
import { CoreClient, CoreError } from "../../src/api/client";
import type { MisesEvent } from "../../src/api/types";
import { startFakeCore, type FakeCore } from "./server";

let core: FakeCore;
let client: CoreClient;

async function failure(p: Promise<unknown>): Promise<CoreError> {
  const e = await p.then(() => null, (err: unknown) => err);
  if (!(e instanceof CoreError)) throw new Error(`expected a CoreError, got ${String(e)}`);
  return e;
}

async function listen(): Promise<{ events: MisesEvent[]; close: () => void }> {
  const ws = new WebSocket(`${core.url.replace("http", "ws")}/events?token=${core.token}`);
  const events: MisesEvent[] = [];
  ws.on("message", (d) => events.push(JSON.parse(String(d)) as MisesEvent));
  await new Promise((ok, fail) => { ws.on("open", ok); ws.on("error", fail); });
  return { events, close: () => ws.close() };
}

const until = async (ok: () => boolean, ms = 3000) => {
  const end = Date.now() + ms;
  while (!ok()) {
    if (Date.now() > end) throw new Error("timed out");
    await new Promise((r) => setTimeout(r, 10));
  }
};

beforeEach(async () => {
  core = await startFakeCore({ port: 0, token: "t0k", scenario: "agent-ux" });
  client = new CoreClient({ baseUrl: core.url, wsUrl: core.url.replace("http", "ws") + "/events", token: core.token, owned: false });
});
afterEach(async () => { await core.stop(); });

describe("local model routes", () => {
  it("status_start_and_pull_with_progress_events", async () => {
    const st = await client.localStatus();
    expect([st.installed, st.running, st.models.map((m) => m.name)]).toEqual([true, false, ["gpt-oss:20b"]]);
    // like the core: refusals are 200 {ok:false, error, hint}; a bad name is a 400
    expect(await client.localPull("qwen3-coder:30b")).toMatchObject({ ok: false, error: "not_running" });
    expect((await failure(client.localPull("Not A Name!"))).code).toBe("invalid_input");
    expect(await client.localStart()).toMatchObject({ ok: true, running: true });
    expect((await client.localStatus()).running).toBe(true);
    const sock = await listen();
    expect(await client.localPull("qwen3-coder:30b")).toEqual({ ok: true });
    expect(await client.localPull("qwen3-coder:30b")).toEqual({ ok: false, already: true });
    await until(() => sock.events.some((e) => e.type === "local_pull" && e.data.status === "success"));
    sock.close();
    const pulls = sock.events.filter((e) => e.type === "local_pull").map((e) => e.data);
    expect(pulls.length).toBeGreaterThanOrEqual(3);
    expect(pulls.every((p) => p.model === "qwen3-coder:30b" && typeof p.completed === "number" && typeof p.total === "number")).toBe(true);
    expect((await client.localStatus()).models.map((m) => m.name)).toEqual(["gpt-oss:20b", "qwen3-coder:30b"]);
  });

  it("a_download_can_be_cancelled_which_ends_with_status_cancelled", async () => {
    await client.localStart();
    const sock = await listen();
    await client.localPull("qwen3-coder:30b");
    expect(await client.localPullCancel("qwen3-coder:30b")).toBe(true);
    expect(await client.localPullCancel("qwen3-coder:30b")).toBe(false);   // nothing downloading any more
    await until(() => sock.events.some((e) => e.type === "local_pull" && e.data.status === "cancelled"));
    await new Promise((r) => setTimeout(r, 700));                          // no progress after the cancel
    sock.close();
    const statuses = sock.events.filter((e) => e.type === "local_pull").map((e) => e.data.status);
    expect(statuses.at(-1)).toBe("cancelled");
    expect(statuses).not.toContain("success");
    expect((await client.localStatus()).models.map((m) => m.name)).toEqual(["gpt-oss:20b"]);
  });

  it("a_scenario_without_local_answers_404_like_an_older_core", async () => {
    core.reset("base");
    expect((await failure(client.localStatus())).status).toBe(404);
  });
});

describe("parallel agents", () => {
  it("status_reports_parallel_and_profile_takes_auto_and_theme", async () => {
    expect((await client.status()).parallel).toEqual({ limit: 3, auto: true, reason: "M5 Pro · 24 GB · cloud model → 3" });
    expect((await client.getProfile()).max_parallel_agents).toBe("auto");
    const p = await client.updateProfile({ max_parallel_agents: 2, theme: "light" });
    expect([p.max_parallel_agents, p.theme]).toEqual([2, "light"]);
    expect((await client.updateProfile({ max_parallel_agents: "auto" })).max_parallel_agents).toBe("auto");
    expect((await failure(client.updateProfile({ theme: "neon" as "dark" }))).status).toBe(422);
  });

  it("a_run_beyond_the_limit_queues", async () => {
    const sock = await listen();
    const ids: string[] = [];
    for (const task of ["one", "two", "three"]) ids.push((await client.createRun({ agent: "designer", prompt: task, model_ref: "claude-code/opus" })).run_id);
    // the base designer run is running too: limit 3 → the third new run waits
    const statuses = await Promise.all(ids.map((id) => client.getRun(id).then((r) => r.status)));
    expect(statuses).toEqual(["running", "running", "queued"]);
    await until(() => sock.events.some((e) => e.type === "run_queued"));
    sock.close();
    expect(sock.events.find((e) => e.type === "run_queued")?.run_id).toBe(ids[2]);
  });
});
