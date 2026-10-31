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

  it("an unknown build is 404, a bad id 400 (like the core)", async () => {
    core.reset(scenario("rules"));
    expect((await get("/studies/context?project_id=nope")).status).toBe(404);
    expect((await get("/studies/context?project_id=..%2Fx")).status).toBe(400);
  });
});
