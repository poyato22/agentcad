// @vitest-environment node
// Contract test, Plan 9 U-D: the fake core's GET /studies/context against the real core's answers
// (core/scripts/dump_studies_context_fixtures.py → core/tests/fixtures/ui/studies_context*.json),
// for the rule proposals and the studies agent's own, and the app's schema reads both.
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { studiesContextSchema } from "../src/api/studiesAgent";
import type { PartSummary } from "../src/api/types";
import { loadScenario, startFakeCore, type FakeCore, type Scenario } from "./fake-core/server";
import { shapeDiff, type Json } from "./shape";

const DIR = resolve(import.meta.dirname, "../../core/tests/fixtures/ui");
const read = (name: string): Json => JSON.parse(readFileSync(join(DIR, name), "utf8")) as Json;

let core: FakeCore;
const scenario = (mode: "agent" | "rules"): Scenario => ({
  ...loadScenario("base"), parts: read("study_parts.json") as unknown as PartSummary[], studies_agent: mode,
});
async function get(path: string): Promise<{ status: number; json: Json }> {
  const r = await fetch(core.url + path, { headers: { Authorization: `Bearer ${core.token}` } });
  return { status: r.status, json: (await r.json()) as Json };
}

beforeAll(async () => { core = await startFakeCore({ port: 0, token: "contract-token", scenario: "base" }); });
afterAll(async () => { await core.stop(); });

describe("GET /studies/context: fake core = real core", () => {
  it.each([["rules", "studies_context.json"], ["agent", "studies_context_agent.json"]] as const)("%s proposals", async (mode, file) => {
    core.reset(scenario(mode));
    const builds = (await get("/projects")).json as { id: string }[];
    const r = await get(`/studies/context?project_id=${builds[0].id}`);
    expect(r.status).toBe(200);
    expect(shapeDiff(read(file), r.json, "context")).toEqual([]);
    const ctx = studiesContextSchema.parse(r.json);
    expect(ctx.proposals_source).toBe(mode);
    expect(ctx.proposals.length).toBeGreaterThan(0);
    expect(ctx.proposals.every((p) => ["beam", "cube"].includes(p.part_id))).toBe(true);
  });

  it("the app's schema reads the real answers", () => {
    for (const f of ["studies_context.json", "studies_context_agent.json"]) {
      const ctx = studiesContextSchema.parse(read(f));
      expect(ctx.proposals.length).toBeGreaterThan(0);
      expect(ctx.solvers.map((s) => s.id)).toEqual(["fem", "fem_nonlinear", "cfd_builtin", "openfoam"]);
    }
  });

  it("the fake's past studies are its GET /sims list, and a proposal's Run studies the proposal's own case", async () => {
    core.reset({ ...scenario("agent"), sims: { studies: true, step_ms: 5 } } as Scenario);
    const pid = ((await get("/projects")).json as { id: string }[])[0].id;
    const listed = (await Promise.all(["beam", "cube"].map(async (p) => (await get(`/sims?part_id=${p}`)).json as unknown[]))).flat();
    let ctx = studiesContextSchema.parse((await get(`/studies/context?project_id=${pid}`)).json);
    expect(ctx.studies_total).toBe(listed.length);
    const prop = ctx.proposals.find((p) => p.type === "static")!;
    const r = await fetch(`${core.url}/sims`, { method: "POST", headers: { Authorization: `Bearer ${core.token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ type: prop.type, part_id: prop.part_id, settings: prop.settings }) });
    const { run_id } = (await r.json()) as { run_id: string };
    let rec: { status: string; results?: { governing_case?: string }; card?: { checks: unknown[] } } | undefined;
    for (let i = 0; i < 200 && rec?.status !== "done"; i++) {
      await new Promise((res) => setTimeout(res, 10));
      const [s] = (await get(`/sims?run_id=${run_id}`)).json as { sim_id: string }[];
      if (s) rec = (await get(`/sims/${s.sim_id}`)).json as typeof rec;
    }
    const caseName = (prop.settings.load_cases as { name: string }[])[0].name;
    expect(rec?.results?.governing_case).toBe(caseName);
    expect(rec?.card?.checks).toEqual([]);           // no requirements were posted
    ctx = studiesContextSchema.parse((await get(`/studies/context?project_id=${pid}`)).json);
    expect(ctx.studies_total).toBe(listed.length + 1);
  });

  it("an unknown build is 404, a bad id 400 (like the core)", async () => {
    core.reset(scenario("rules"));
    expect((await get("/studies/context?project_id=nope")).status).toBe(404);
    expect((await get("/studies/context?project_id=..%2Fx")).status).toBe(400);
  });
});
