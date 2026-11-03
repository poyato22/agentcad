// @vitest-environment node
// Contract (Plan 13 H4): the real core's whole-assembly study routes (core
// scripts/dump_assembly_study_fixtures.py → core/tests/fixtures/ui/assembly_studies) parse with
// the app's schemas — the kinds with the studies agent's pick, a walk record (real MuJoCo) whose
// playback the assembly player plays, a drop record whose playback moves the whole assembly, the
// progress events and the background job — and the fake core answers in the same shapes.
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { z } from "zod";
import {
  assemblyKindsSchema, assemblyProgressSchema, assemblyStudyRecordSchema, assemblyStudySummarySchema,
} from "../src/api/assemblyStudies";
import { backgroundJobSchema, jobIdOf } from "../src/api/jobs";
import { parseAnimateSteps } from "../src/state/assemblyAnimation";
import { ROOT_JOINT } from "../src/ui/assembly/controller";
import { simpleFields } from "../src/ui/pages/studies/assembly/AssemblyStudies";
import { startFakeCore, type FakeCore } from "./fake-core/server";
import { shapeDiff } from "./shape";

const DIR = resolve(import.meta.dirname, "../../core/tests/fixtures/ui/assembly_studies");
const read = <T = unknown>(n: string): T => JSON.parse(readFileSync(join(DIR, n), "utf8")) as T;

let core: FakeCore;
const H = () => ({ Authorization: `Bearer ${core.token}`, "Content-Type": "application/json" });
async function call(method: string, path: string, body?: unknown): Promise<{ status: number; json: unknown }> {
  const r = await fetch(core.url + path, { method, headers: H(), body: body === undefined ? undefined : JSON.stringify(body) });
  return { status: r.status, json: await r.json() };
}
beforeAll(async () => { core = await startFakeCore({ port: 0, token: "asm", scenario: "base", }); });
afterAll(async () => { await core.stop(); });

describe("whole-assembly studies: real core ↔ schemas ↔ fake core", () => {
  it("the kinds recommend walk for the quadruped, each with a settings form", () => {
    const k = assemblyKindsSchema.parse(read("assembly_study_kinds.json")).kinds;
    expect(k.map((x) => x.kind).sort()).toEqual(["drop", "launch", "mechanism", "walk"]);
    expect(k[0]).toMatchObject({ kind: "walk", recommended: true, applicable: true });
    const walk = simpleFields(k[0].schema).map((f) => f.key);
    expect(walk).toEqual(expect.arrayContaining(["gait", "period_s", "cycles", "min_travel_mm"]));
    expect(walk).not.toContain("actuators");      // a map stays at its default
    const launch = simpleFields(k.find((x) => x.kind === "launch")!.schema);
    expect(launch.find((f) => f.key === "motors")?.kind).toBe("list");
  });

  it("the walk record: checks, charts and a playback the assembly player plays", () => {
    const r = assemblyStudyRecordSchema.parse(read("assembly_study_walk.json"));
    expect(r).toMatchObject({ kind: "walk", status: "done" });
    expect(r.checks.map((c) => c.name)).toEqual(expect.arrayContaining(["joint torque vs actuators", "range of motion collisions", "travel"]));
    expect(r.charts.length).toBeGreaterThan(0);
    const steps = parseAnimateSteps(r.playback!.steps);
    expect(steps).toHaveLength(r.playback!.steps.length);
    expect(steps[0].follow?.length).toBeGreaterThan(2);       // every other leg joint follows
    expect(r.fem).toMatchObject({ available: true, via: "dyn_to_fem" });
  });

  it("the drop record lifts the whole assembly and lands it", () => {
    const r = assemblyStudyRecordSchema.parse(read("assembly_study_drop.json"));
    const steps = parseAnimateSteps(r.playback!.steps);
    expect(steps.every((s) => s.joint_id === ROOT_JOINT)).toBe(true);
    expect((steps[0].from as number[])[2]).toBeGreaterThan(0);
    expect((steps.at(-1)!.to as number[])[2]).toBe(0);
    expect(r.verdict).toBe("fail");                     // ~10 g vs the 8 g limit
  });

  it("the list, the progress events and the job parse; a job id names the study", () => {
    const list = z.array(assemblyStudySummarySchema).parse(read("assembly_studies.json"));
    expect(list.map((s) => s.kind).sort()).toEqual(["drop", "walk"]);
    const evs = readFileSync(join(DIR, "assembly_study_events.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l) as { type: string; data: unknown });
    const prog = evs.filter((e) => e.type === "assembly_study_progress").map((e) => assemblyProgressSchema.parse(e.data));
    expect(prog.map((p) => p.phase)).toEqual(expect.arrayContaining(["queued", "dynamics", "stability", "range of motion", "done"]));
    const fin = evs.find((e) => e.type === "sim_finished")!.data as Record<string, unknown>;
    expect(fin).toMatchObject({ kind: "assembly_study", study_id: fin.job_id });
    expect(jobIdOf({ study_id: fin.job_id })).toBe(fin.job_id);
    const jobs = z.array(backgroundJobSchema).parse(read("assembly_study_jobs.json"));
    expect(jobs[0]).toMatchObject({ kind: "assembly_study", status: "running" });
  });

  it("the fake core plays a study in the real shapes, and Stop cancels it", async () => {
    const pid = core.state.builds.defaultId;
    const kinds = await call("GET", `/assembly_studies/kinds?project_id=${pid}`);
    expect(shapeDiff(read("assembly_study_kinds.json"), kinds.json)).toEqual([]);
    const started = await call("POST", "/assembly_studies", { project_id: pid, kind: "walk", settings: {} });
    expect(started.status).toBe(202);
    expect(shapeDiff(read("assembly_study_start.json"), started.json)).toEqual([]);
    let rows: { study_id: string; status: string }[] = [];
    for (let i = 0; i < 100; i++) {
      rows = (await call("GET", `/assembly_studies?project_id=${pid}`)).json as typeof rows;
      if (rows[0]?.status === "done") break;
      await new Promise((r) => setTimeout(r, 50));
    }
    expect(shapeDiff(read<unknown[]>("assembly_studies.json")[0], rows[0])).toEqual([]);
    const rec = await call("GET", `/assembly_studies/${rows[0].study_id}`);
    expect(shapeDiff(read("assembly_study_walk.json"), rec.json)).toEqual([]);
    const fem = await call("POST", `/assembly_studies/${rows[0].study_id}/fem`, {});
    expect(fem.status).toBe(202);
    const second = await call("POST", "/assembly_studies", { project_id: pid, kind: "drop", settings: {} });
    const jobs = (await call("GET", "/jobs")).json as { job_id: string; kind: string }[];
    const job = jobs.find((j) => j.kind === "assembly_study")!;
    expect(shapeDiff(read<unknown[]>("assembly_study_jobs.json")[0], job)).toEqual([]);
    expect((await call("POST", `/jobs/${job.job_id}/cancel`)).status).toBe(200);
    const after = (await call("GET", `/assembly_studies?project_id=${pid}`)).json as { status: string; run_id: string }[];
    expect(after.find((r) => r.run_id === (second.json as { run_id: string }).run_id)?.status).toBe("cancelled");
  });
});
