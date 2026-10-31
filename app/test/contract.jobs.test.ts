// @vitest-environment node
// Plan 9 B3 fix round 2: an agent's background jobs. The real core's GET /jobs, POST
// /jobs/{id}/cancel (200 and 409) and the cancelled job's sim_finished (core
// scripts/dump_streams_fixtures.py → core/tests/fixtures/ui/p9) parse with the app's schemas, and
// the fake core answers in the same shapes.
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { z } from "zod";
import { backgroundJobSchema, jobCancelSchema, jobIdOf, type BackgroundJob } from "../src/api/jobs";
import { eventSchema } from "../src/api/schemas";
import type { MisesEvent } from "../src/api/types";
import { startFakeCore, type FakeCore } from "./fake-core/server";
import { shapeDiff } from "./shape";

const DIR = resolve(import.meta.dirname, "../../core/tests/fixtures/ui/p9");
const read = <T>(n: string): T => JSON.parse(readFileSync(join(DIR, n), "utf8")) as T;
const REAL_JOBS = read<BackgroundJob[]>("streams_jobs.json");

let core: FakeCore;
async function call(method: string, path: string): Promise<{ status: number; json: unknown }> {
  const r = await fetch(core.url + path, { method, headers: { Authorization: `Bearer ${core.token}` } });
  return { status: r.status, json: await r.json() };
}

beforeAll(async () => {
  core = await startFakeCore({ port: 0, token: "jobs", scenario: "base" });
});
afterAll(async () => { await core.stop(); });

describe("background jobs: real core ↔ schemas ↔ fake core", () => {
  it("GET /jobs parses and the fake lists the same shape, filtered by build", async () => {
    const real = z.array(backgroundJobSchema).parse(REAL_JOBS);
    expect(real[0]).toMatchObject({ kind: "sim", status: "running", cancel_url: `/jobs/${real[0].job_id}/cancel` });
    core.state.jobs = structuredClone(REAL_JOBS);
    const all = await call("GET", "/jobs");
    expect(shapeDiff(REAL_JOBS, all.json)).toEqual([]);
    expect((await call("GET", `/jobs?project_id=${real[0].build_id}`)).json).toHaveLength(1);
    expect((await call("GET", "/jobs?project_id=elsewhere")).json).toEqual([]);
  });

  it("POST /jobs/{id}/cancel: 200 cancelling, then sim_finished cancelled, then 409", async () => {
    const realOk = read<unknown>("streams_job_cancel.json");
    const realGone = read<Record<string, unknown>>("streams_job_cancel_not_running.json");
    const realFin = read<MisesEvent>("streams_job_cancelled_event.json");
    expect(jobCancelSchema.parse(realOk).status).toBe("cancelling");
    expect(eventSchema.parse(realFin).stream).toBe("sim");
    expect(realFin.data.status).toBe("cancelled");
    core.state.jobs = structuredClone(REAL_JOBS);
    const id = REAL_JOBS[0].job_id;
    const got: MisesEvent[] = [];
    const ws = new WebSocket(`${core.url.replace("http", "ws")}/events?token=${core.token}`);
    ws.onmessage = (m) => got.push(JSON.parse(String(m.data)) as MisesEvent);
    await new Promise((ok) => { ws.onopen = ok; });
    const ok = await call("POST", `/jobs/${id}/cancel`);
    expect(ok.status).toBe(200);
    expect(shapeDiff(realOk, ok.json)).toEqual([]);
    for (let i = 0; i < 100 && !got.some((e) => e.type === "sim_finished"); i++) await new Promise((r) => setTimeout(r, 5));
    ws.close();
    const fin = got.find((e) => e.type === "sim_finished");
    expect(fin).toBeDefined();
    expect(shapeDiff(realFin.data, fin!.data, "$sim_finished")).toEqual([]);
    expect(fin!.stream).toBe("sim");
    expect((await call("GET", "/jobs")).json).toEqual([]);
    const gone = await call("POST", `/jobs/${id}/cancel`);
    expect(gone.status).toBe(409);
    expect(shapeDiff(realGone, gone.json)).toEqual([]);
    expect((await call("POST", "/jobs/review_0123456789ab/cancel")).status).toBe(404);
  });

  it("Stop all stops an idle agent's jobs and reports its run", async () => {
    core.state.jobs = structuredClone(REAL_JOBS);
    const r = await call("POST", "/runs/cancel_all");
    expect((r.json as { cancelled: string[] }).cancelled).toContain(REAL_JOBS[0].run_id);
    expect((await call("GET", "/jobs")).json).toEqual([]);
  });

  it("a study card's tool result names its job", () => {
    const steps = read<{ kind: string; payload: { name?: string; output?: { data?: Record<string, unknown> } } }[]>("streams_steps.json");
    const res = steps.find((s) => s.kind === "tool_result" && s.payload.name === "sim_run");
    expect(jobIdOf(res?.payload.output?.data)).toMatch(/^sim_[0-9a-f]{12}$/);
    expect(res?.payload.output?.data?.cancel_url).toBe(`/jobs/${jobIdOf(res?.payload.output?.data)}/cancel`);
    expect(jobIdOf({ review_id: "review_0123456789ab" })).toBe("review_0123456789ab");
    expect(jobIdOf({ sim_id: "nope" })).toBeNull();
  });
});
