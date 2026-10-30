// @vitest-environment node
// Contract test, Plan 5 A7: the engineering loop's routes (core server/spec_api.py) and the
// batched interview question. The real core's answers are core/tests/fixtures/ui/spec*.json,
// studies.json, study_apply.json, question_answer.json and spec_events.jsonl (written by
// core/scripts/dump_ui_fixtures.py --only spec, with a real 6-evaluation CalculiX study).
// For each: the app's schema parses it, and the fake core answers the same route with the same
// keys and value types.
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { z } from "zod";
import { CoreError, toError } from "../src/api/client";
import { coreErrorBodySchema, okSchema, questionAskedSchema } from "../src/api/schemas";
import { specCheckSchema, specDocSchema, STUDY_STATUSES, studyAppliedSchema, studyProgressSchema, studySchema } from "../src/api/spec";
import { emptyState, reduce } from "../src/state/reduce";
import type { MisesEvent } from "../src/api/types";
import { loadScenario, startFakeCore, type FakeCore, type Scenario } from "./fake-core/server";
import { FIXTURE_PROJECT, REAL_SPEC } from "./fake-core/spec";
import { shapeDiff, type Json } from "./shape";

const HERE = import.meta.dirname;
const DIR = process.env.MISES_CONTRACT_FIXTURES ? resolve(process.env.MISES_CONTRACT_FIXTURES) : resolve(HERE, "../../core/tests/fixtures/ui");
const read = (name: string): unknown => JSON.parse(readFileSync(join(DIR, name), "utf8"));

let core: FakeCore;
const fake = new Map<string, unknown>();

async function ok(method: string, path: string, body?: unknown): Promise<unknown> {
  const headers: Record<string, string> = { Authorization: `Bearer ${core.token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(core.url + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  expect(r.status, `${method} ${path}`).toBe(200);
  return r.json();
}

beforeAll(async () => {
  core = await startFakeCore({ port: 0, token: "contract", scenario: "base" });
  const pid = FIXTURE_PROJECT;   // the fake core's build (its one GET /projects); shapes are compared, not ids
  fake.set("spec_empty.json", await ok("GET", `/projects/${pid}/spec`));
  fake.set("spec_put.json", await ok("PUT", `/projects/${pid}/spec`, REAL_SPEC.spec.spec));
  fake.set("spec.json", await ok("GET", `/projects/${pid}/spec`));
  fake.set("spec_check.json", await ok("GET", `/projects/${pid}/spec/check?part_id=mount_plate`));
  const sc: Scenario = { ...loadScenario("base"), spec: { spec: "fixture", studies: "fixture" } };
  core.reset(sc);
  fake.set("studies.json", await ok("GET", `/projects/${pid}/studies`));
  const st = REAL_SPEC.studies[0];
  fake.set("study_apply.json", await ok("POST", `/projects/${pid}/studies/${st.id}/apply`, { iteration: st.best_id }));
  // the same click with the on-screen version from before that apply: 409 stale_version
  const r = await fetch(`${core.url}/projects/${pid}/studies/${st.id}/apply`, {
    method: "POST", headers: { Authorization: `Bearer ${core.token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ iteration: st.best_id, base_version: 0 }),
  });
  fake.set("study_apply_stale.json", { status: r.status, body: await r.json() });
});
afterAll(async () => { await core.stop(); });

const CASES: [string, z.ZodType<unknown>][] = [
  ["spec_empty.json", specDocSchema], ["spec_put.json", specDocSchema], ["spec.json", specDocSchema],
  ["spec_check.json", specCheckSchema], ["studies.json", z.array(studySchema)], ["study_apply.json", studyAppliedSchema],
];

describe("contract: spec / design-study routes ↔ schemas ↔ fake core", () => {
  for (const [name, schema] of CASES) {
    describe(name, () => {
      it("schema_parses_the_real_core_answer", () => {
        const p = schema.safeParse(read(name));
        expect(p.success, p.success ? "" : JSON.stringify(p.error.issues.slice(0, 5))).toBe(true);
      });
      it("fake_core_answer_has_the_same_shape", () => {
        expect(schema.safeParse(fake.get(name)).success).toBe(true);
        expect(shapeDiff(read(name) as Json, fake.get(name) as Json, name)).toEqual([]);
      });
    });
  }

  it("study_apply_stale.json: a stale base_version is 409 stale_version, from the fake core too", async () => {
    const real = read("study_apply_stale.json") as { status: number; body: unknown };
    const f = fake.get("study_apply_stale.json") as typeof real;
    expect(real.status).toBe(409);
    const body = coreErrorBodySchema.parse(real.body);
    expect(body.error).toBe("stale_version");
    expect(f.status).toBe(409);
    expect(shapeDiff(real.body as Json, f.body as Json, "stale")).toEqual([]);
    expect(coreErrorBodySchema.parse(f.body).error).toBe("stale_version");
    expect(coreErrorBodySchema.parse(f.body).hint).toBe(body.hint);
    // and the client turns the real answer into a stale_version CoreError (the reload / keep offer)
    const err = await toError(new Response(JSON.stringify(real.body), { status: 409, headers: { "Content-Type": "application/json" } }));
    expect(err).toBeInstanceOf(CoreError);
    expect(err.code).toBe("stale_version");
  });

  it("the real study keeps every iteration metric the panel reads (C3 I-6)", () => {
    const s = z.array(studySchema).parse(read("studies.json"))[0];
    expect(s.iterations.length).toBeGreaterThanOrEqual(6);
    for (const it of s.iterations) {
      expect(it.metrics.mass_g).toEqual(expect.any(Number));
      expect(it.metrics.min_sf).toEqual(expect.any(Number));
      expect(Object.keys(it.metrics.sf_by_case).sort()).toEqual(["desk_load", "drop_hit"]);
    }
    expect(s.iterations.some((i) => i.pareto)).toBe(true);
    expect(s.best_id).not.toBeNull();
  });

  it("the batch answer route answers {ok:true}", () => {
    expect(okSchema.safeParse(read("question_answer.json")).success).toBe(true);
  });

  it("the real batched question_asked event carries its questions into the store", () => {
    const events = readFileSync(join(DIR, "spec_events.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l) as MisesEvent);
    const asked = events.find((e) => e.type === "question_asked")!;
    const q = questionAskedSchema.parse(asked.data);
    expect(q.questions.map((x) => x.id)).toEqual(["use", "size_class", "motor_prop"]);
    const s = reduce(emptyState(), asked).state;
    expect(s.questions.get(q.question_id)?.items).toHaveLength(3);   // the app's batch field (A2)
    const progress = events.filter((e) => e.type === "study_progress").map((e) => studyProgressSchema.parse(e.data));
    // one per evaluation, then the terminal one with the final status (QA B2-1)
    expect(progress.map((p) => p.n)).toEqual([1, 2, 3, 4, 5, 6, 6]);
    expect(progress.slice(0, -1).every((p) => p.status == null)).toBe(true);
    expect(STUDY_STATUSES).toContain(progress.at(-1)?.status);
    expect(progress.at(-1)?.status).not.toBe("running");
  });

  it("the answer the app sends (a JSON object string) is what the core stored", () => {
    const out = read("interview_answer.json") as { data: { answers: Record<string, unknown>; answer: string } };
    expect(JSON.parse(out.data.answer)).toEqual(out.data.answers);
  });
});
