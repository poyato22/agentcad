// @vitest-environment node
// Fake core sim routes: the Studies page's e2e runs against these, so they must behave like core
// server/sim_api.py (shapes are checked against the real fixtures in test/contract.test.ts).
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import WebSocket from "ws";
import { CoreClient, CoreError } from "../../src/api/client";
import type { MisesEvent } from "../../src/api/types";
import { loadScenario, startFakeCore, type FakeCore, type Scenario } from "./server";

let core: FakeCore;
let client: CoreClient;

function scenario(sims: Scenario["sims"]): Scenario {
  return { ...loadScenario("base"), sims };
}

async function events(): Promise<{ ws: WebSocket; got: MisesEvent[] }> {
  const ws = new WebSocket(`${core.url.replace("http", "ws")}/events?token=${core.token}`);
  const got: MisesEvent[] = [];
  ws.on("message", (d) => got.push(JSON.parse(String(d)) as MisesEvent));
  await new Promise((ok, fail) => { ws.on("open", ok); ws.on("error", fail); });
  return { ws, got };
}

const until = async (cond: () => boolean | Promise<boolean>, ms = 3000) => {
  const end = Date.now() + ms;
  while (!(await cond()) && Date.now() < end) await new Promise((r) => setTimeout(r, 10));
  expect(await cond()).toBe(true);
};

const BODY = { part_id: "mount_plate", fix: "x-min", load: { face: "x-max", newtons: 20, direction: "-z" as const }, material: "PETG", environment: "Desk" };

beforeEach(async () => {
  core = await startFakeCore({ port: 0, token: "t", scenario: scenario({ step_ms: 10 }) });
  client = new CoreClient({ baseUrl: core.url, wsUrl: "", token: "t", owned: false });
});
afterEach(async () => { await core.stop(); });

describe("fake core /sims", () => {
  it("solver status follows the scenario", async () => {
    expect((await client.simSolvers()).ccx.found).toBe(true);
    core.reset(scenario({ ccx: false }));
    const s = await client.simSolvers();
    expect(s.ccx.found).toBe(false);
    expect(s.ccx.install).toBe("conda install -c conda-forge calculix");
  });

  it("serves the real core's stored studies, newest first, filtered by part and run", async () => {
    core.reset(scenario({ records: "fixtures" }));
    const list = await client.listSims({ partId: "mount_plate" });
    expect(list.map((s) => s.status)).toEqual(["failed", "done"]);
    expect(await client.listSims({ partId: "other_part" })).toEqual([]);
    const done = list[1];
    expect((await client.listSims({ runId: done.run_id ?? "" })).map((s) => s.sim_id)).toEqual([done.sim_id]);
    const rec = await client.getSim(done.sim_id);
    expect(rec.solver?.name).toBe("CalculiX");
    const glb = await client.simGlb(done.sim_id);
    expect(glb).not.toBe("not-modified");
    if (glb !== "not-modified") {
      expect(new TextDecoder().decode(glb.bytes.slice(0, 4))).toBe("glTF");
      expect(await client.simGlb(done.sim_id, glb.etag)).toBe("not-modified");
    }
  });

  it("bad and unknown ids answer like the core", async () => {
    expect((await client.getSim("nope").catch((e: CoreError) => e) as CoreError).status).toBe(400);
    expect((await client.getSim("sim_000000000000").catch((e: CoreError) => e) as CoreError).status).toBe(404);
    expect((await client.listSims({ partId: "Bad Id" }).catch((e: CoreError) => e) as CoreError).status).toBe(400);
  });

  it("POST /sims: 202, a studies run, progress events in order, then a done record", async () => {
    const { ws, got } = await events();
    const started = await client.startSim(BODY);
    expect(started.status).toBe("running");
    const run = await client.getRun(started.run_id);
    expect([run.agent, run.provider, run.model, run.external]).toEqual(["studies", "ui", "studies-page", true]);
    await until(() => got.some((e) => e.type === "run_finished"));
    ws.close();
    const stages = got.filter((e) => e.type === "sim_progress").map((e) => e.data.stage);
    expect(stages).toEqual(["mesh_coarse", "solve_coarse", "mesh_fine", "solve_fine", "post"]);
    expect(got.map((e) => e.type)[0]).toBe("run_started");
    const [s] = await client.listSims({ runId: started.run_id });
    expect(s.status).toBe("done");
    const rec = await client.getSim(s.sim_id);
    expect(rec.inputs).toEqual({ fix: "x-min", load: { face: "x-max", newtons: 20, direction: "-z" }, material: "PETG", environment: "Desk" });
    expect(rec.results?.allowable_mpa).toBeCloseTo(32.9);
    expect((await client.getRun(started.run_id)).status).toBe("done");
  });

  it("without CalculiX the study fails with the install hint", async () => {
    core.reset(scenario({ ccx: false, step_ms: 10 }));
    const { run_id } = await client.startSim(BODY);
    await until(async () => (await client.listSims({ runId: run_id }))[0]?.status === "failed");
    const [s] = await client.listSims({ runId: run_id });
    expect(s.error).toBe("CalculiX (ccx) is not installed");
    expect(s.hint).toContain("conda install -c conda-forge calculix");
  });

  it("cancelling the run cancels a hanging study", async () => {
    core.reset(scenario({ outcome: "hang", step_ms: 10 }));
    const { run_id } = await client.startSim(BODY);
    await until(async () => (await client.listSims({ runId: run_id }))[0]?.stage === "solve_fine");
    await client.cancel(run_id);
    const [s] = await client.listSims({ runId: run_id });
    expect([s.status, s.stage]).toEqual(["cancelled", "cancelled"]);
    expect((await client.getRun(run_id)).status).toBe("cancelled");
  });

  it("validation: 422 for a bad face, 404 for an unbuilt part, a failed run for fix == load", async () => {
    const bad = await client.startSim({ ...BODY, fix: "middle" }).catch((e: CoreError) => e) as CoreError;
    expect(bad.status).toBe(422);
    const missing = await client.startSim({ ...BODY, part_id: "no_such_part" }).catch((e: CoreError) => e) as CoreError;
    expect([missing.status, missing.code]).toEqual([404, "not_found"]);
    const { run_id } = await client.startSim({ ...BODY, fix: "x-max" });
    await until(async () => (await client.getRun(run_id)).status === "failed");
    const steps = await client.steps(run_id);
    expect(steps.find((s) => s.kind === "error")?.payload.message).toBe("fix and load are the same face");
    expect(await client.listSims({ runId: run_id })).toEqual([]);
  });
});
