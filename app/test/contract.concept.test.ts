// @vitest-environment node
// Contract (Plan 9 G-A): the real core's concept answers (core/scripts/dump_concept_fixtures.py →
// core/tests/fixtures/ui/concept) ↔ the app's schemas ↔ the fake core, which is built from the
// same dump (fake-core/concept.ts), so every answer can be compared key for key and type for type.
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type { z } from "zod";
import {
  conceptDecisionSchema, conceptSketchSchema, conceptViewSchema, feedbackSentSchema, pitchChosenSchema,
} from "../src/api/concept";
import { CONCEPT_FIXTURES } from "./fake-core/concept";
import { loadScenario, SCENARIO_DIR, startFakeCore, type FakeCore, type Scenario } from "./fake-core/server";
import { shapeDiff } from "./shape";

const real = (name: string): unknown => JSON.parse(readFileSync(resolve(CONCEPT_FIXTURES, name), "utf8"));
const PID = "74ab71a5279a";

const PARSE: [string, z.ZodType<unknown>][] = [
  ["concept_view.json", conceptViewSchema], ["concept_view_pitching.json", conceptViewSchema],
  ["concept_view_approved.json", conceptViewSchema], ["concept_sketch.json", conceptSketchSchema],
  ["concept_feedback.json", feedbackSentSchema], ["concept_approve.json", conceptDecisionSchema],
  ["concept_skip.json", conceptDecisionSchema], ["concept_choose.json", pitchChosenSchema],
];

let core: FakeCore;
let pitchCore: FakeCore;
const withStart = (start: string): Scenario => {
  const sc = loadScenario("concept");
  return { ...sc, concept: { ...sc.concept, start: start as "v2", revise_ms: null } };
};

beforeAll(async () => {
  core = await startFakeCore({ port: 0, token: "t", scenario: withStart("v2") });
  pitchCore = await startFakeCore({ port: 0, token: "t", scenario: withStart("pitching") });
});
afterAll(async () => { await core.stop(); await pitchCore.stop(); });

async function call(c: FakeCore, method: string, path: string, body?: unknown): Promise<{ status: number; json: unknown }> {
  const r = await fetch(c.url + path, {
    method, headers: { Authorization: "Bearer t", ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return { status: r.status, json: await r.json() };
}

describe("contract: real core concept answers ↔ app schemas ↔ fake core", () => {
  for (const [name, schema] of PARSE) {
    it(`the app schema parses the real ${name}`, () => {
      const p = schema.safeParse(real(name));
      expect(p.success, p.success ? "" : JSON.stringify(p.error.issues.slice(0, 3))).toBe(true);
    });
  }

  it("every render the fixtures name is served by the fake core", () => {
    const ids = JSON.stringify(real("concept_view.json")).match(/"(ck[spf]_[0-9a-f]{12}[a-z0-9_]*)"/g) ?? [];
    const renders = [...new Set(ids.map((s) => s.slice(1, -1)))].filter((s) => /_(three_quarter|side|front|sil_front|sil_side)$/.test(s));
    expect(renders.length).toBeGreaterThan(4);
    for (const id of renders) expect(existsSync(resolve(SCENARIO_DIR, "renders", `${id}.png`)), id).toBe(true);
  });

  it("GET /projects/{id}/concept and GET /concept/sketches/{id} have the real shape", async () => {
    const view = await call(core, "GET", `/projects/${PID}/concept`);
    expect(view.status).toBe(200);
    expect(shapeDiff(real("concept_view.json"), view.json, "concept_view")).toEqual([]);
    const v1 = (view.json as { sketches: { sketch_id: string }[] }).sketches[0].sketch_id;
    const sk = await call(core, "GET", `/concept/sketches/${v1}`);
    expect(shapeDiff(real("concept_sketch.json"), sk.json, "concept_sketch")).toEqual([]);
    const pitching = await call(pitchCore, "GET", `/projects/${PID}/concept`);
    expect(shapeDiff(real("concept_view_pitching.json"), pitching.json, "concept_view_pitching")).toEqual([]);
  });

  it("feedback, approve, skip and choose answer like the real core; bad marks are refused alike", async () => {
    const view = (await call(core, "GET", `/projects/${PID}/concept`)).json as { sketches: { sketch_id: string }[]; pitches: { pitch_id: string }[] };
    const sid = view.sketches[1].sketch_id;
    const fb = await call(core, "POST", `/concept/sketches/${sid}/feedback`, { comment: "c", marks: [
      { kind: "circle", cx: 0.5, cy: 0.4, r: 0.1, comment: "a" }, { kind: "pin", x: 0.2, y: 0.2, image: 1, comment: "b" }] });
    expect(fb.status).toBe(200);
    expect(shapeDiff(real("concept_feedback.json"), fb.json, "concept_feedback")).toEqual([]);
    expect((await call(core, "POST", `/concept/sketches/${sid}/feedback`, {})).status).toBe(400);
    expect((await call(core, "POST", `/concept/sketches/${sid}/feedback`, { marks: [{ kind: "pin", x: 2, y: 0 }] })).status).toBe(422);
    expect((await call(core, "POST", `/concept/sketches/${sid}/feedback`, { marks: [{ kind: "pin", x: 0.1, y: 0.1, image: 5 }] })).status).toBe(400);
    const ap = await call(core, "POST", `/projects/${PID}/concept/approve`, {});
    expect(shapeDiff(real("concept_approve.json"), ap.json, "concept_approve")).toEqual([]);
    const skip = await call(core, "POST", `/projects/${PID}/concept/skip`);
    expect(shapeDiff(real("concept_skip.json"), skip.json, "concept_skip")).toEqual([]);
    const pv = (await call(pitchCore, "GET", `/projects/${PID}/concept`)).json as { pitches: { pitch_id: string; options: { name: string }[] }[] };
    const [a, b] = pv.pitches[0].options;
    const ch = await call(pitchCore, "POST", `/concept/pitches/${pv.pitches[0].pitch_id}/choose`, { mix: [{ option: a.name, take: "body" }, { option: b.name, take: "legs" }], comment: "x" });
    expect(shapeDiff(real("concept_choose.json"), ch.json, "concept_choose")).toEqual([]);
    expect((await call(pitchCore, "POST", `/concept/pitches/${pv.pitches[0].pitch_id}/choose`, { option: "Nope" })).status).toBe(400);
  });
});
