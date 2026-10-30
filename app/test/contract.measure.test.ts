// @vitest-environment node
// Contract test, Plan 7 U2a: measure and feedback pins. Real-core fixtures
// (core/scripts/dump_measure_feedback_fixtures.py) ↔ the app's schemas ↔ the fake core's answers
// to the same requests (scenario "base": the same 40 × 20 × 5 plate with two Ø4.2 holes).
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type { z } from "zod";
import { feedbackResultSchema, measureResultSchema } from "../src/api/measure";
import { coreErrorBodySchema, partSummarySchema } from "../src/api/schemas";
import type { PartSummary, Step } from "../src/api/types";
import { parseFeedback } from "../src/ui/viewer/pins";
import { startFakeCore, type FakeCore } from "./fake-core/server";
import { shapeDiff, type Json } from "./shape";

const HERE = import.meta.dirname;
const DIR = process.env.MISES_CONTRACT_FIXTURES ? resolve(process.env.MISES_CONTRACT_FIXTURES) : resolve(HERE, "../../core/tests/fixtures/ui");
const read = (name: string): unknown => JSON.parse(readFileSync(join(DIR, name), "utf8"));

let core: FakeCore;
const fake = new Map<string, unknown>();

async function req(method: string, path: string, body?: unknown): Promise<{ status: number; body: unknown }> {
  const headers: Record<string, string> = { Authorization: `Bearer ${core.token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(core.url + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  return { status: r.status, body: await r.json() };
}
async function ok(method: string, path: string, body?: unknown): Promise<unknown> {
  const r = await req(method, path, body);
  expect(r.status, `${method} ${path}: ${JSON.stringify(r.body)}`).toBe(200);
  return r.body;
}

let feedbackRun = "";
beforeAll(async () => {
  core = await startFakeCore({ port: 0, token: "contract", scenario: "base" });
  const parts = (await ok("GET", "/parts")) as PartSummary[];
  const plate = parts.find((p) => p.part_id === "mount_plate")!;
  const m = plate.metrics!;
  const v = plate.version;
  const circle = m.edge_list!.find((e) => e.type === "circle")!;
  const line = m.edge_list!.find((e) => e.type === "line")!;
  const top = m.face_list!.find((f) => f.normal?.[2] === 1)!;
  const bottom = m.face_list!.find((f) => f.normal?.[2] === -1)!;
  const url = "/parts/mount_plate/measure";
  fake.set("part_measure_edge.json", await ok("POST", url, { refs: [{ edge_id: circle.id }], base_version: v }));
  fake.set("part_measure_line.json", await ok("POST", url, { refs: [{ edge_id: line.id }], base_version: v }));
  fake.set("part_measure_face.json", await ok("POST", url, { refs: [{ face_id: top.id }], base_version: v }));
  fake.set("part_measure_two_faces.json", await ok("POST", url, { refs: [{ face_id: top.id }, { face_id: bottom.id }], base_version: v }));
  fake.set("part_measure_points.json", await ok("POST", url, { refs: [{ point: [-20, -10, 2.5] }, { point: [20, 10, 2.5] }] }));
  fake.set("part_measure_stale.json", await req("POST", url, { refs: [{ face_id: top.id }], base_version: (v ?? 0) + 5 }));
  fake.set("part_measure_bad_face.json", await req("POST", url, { refs: [{ face_id: "f999" }] }));
  const pid = ((await ok("GET", "/projects")) as { id: string }[])[0].id;
  const fb = (await ok("POST", `/projects/${pid}/feedback`, { part_id: "mount_plate", point: [7, 0, 2.5], face_id: top.id, text: "make these holes M3 clearance" })) as { run_id: string };
  fake.set("project_feedback.json", fb);
  feedbackRun = fb.run_id;
  fake.set("project_feedback_empty.json", await req("POST", `/projects/${pid}/feedback`, { part_id: "mount_plate", point: [7, 0, 2.5], text: " " }));
});
afterAll(async () => { await core.stop(); });

const CASES: [string, z.ZodType][] = [
  ["part_measure_edge.json", measureResultSchema], ["part_measure_line.json", measureResultSchema], ["part_measure_face.json", measureResultSchema],
  ["part_measure_two_faces.json", measureResultSchema], ["part_measure_points.json", measureResultSchema], ["project_feedback.json", feedbackResultSchema],
];
const REFUSALS = ["part_measure_stale.json", "part_measure_bad_face.json", "project_feedback_empty.json"];
const keys = (v: unknown) => ((v as { values: { key: string }[] }).values ?? []).map((x) => x.key);

describe("contract: measure and feedback ↔ schemas ↔ fake core", () => {
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

  it("the real values are the exact geometry; the fake names the same values", () => {
    const val = (name: string) => Object.fromEntries((read(name) as { values: { key: string; value: number }[] }).values.map((x) => [x.key, x.value]));
    expect(val("part_measure_edge.json")).toMatchObject({ diameter: 4.2, radius: 2.1 });
    expect(val("part_measure_line.json").length).toBeGreaterThan(0);
    expect(val("part_measure_two_faces.json")).toMatchObject({ distance: 5, normal_distance: 5, angle: 0 });
    expect(val("part_measure_points.json").distance).toBeCloseTo(Math.hypot(40, 20), 6);
    for (const [name] of CASES.slice(0, 4)) expect(keys(fake.get(name))).toEqual(keys(read(name)));
  });

  for (const name of REFUSALS) {
    it(`${name}: same status, code and fields as the core`, () => {
      const real = read(name) as { status: number; body: Record<string, unknown> };
      const f = fake.get(name) as { status: number; body: Record<string, unknown> };
      expect(f.status).toBe(real.status);
      expect(f.body.error).toBe(real.body.error);
      expect(Object.keys(f.body).sort()).toEqual(Object.keys(real.body).sort());
      expect(coreErrorBodySchema.safeParse(real.body).success).toBe(true);
    });
  }

  it("the real parts list carries hole radii the selection label reads", () => {
    const parts = (read("measure_parts.json") as unknown[]).map((p) => partSummarySchema.parse(p));
    const m = parts.find((p) => p.part_id === "mount_plate")!.metrics!;
    expect(m.edge_list!.filter((e) => e.type === "circle").every((e) => e.radius === 2.1)).toBe(true);
    expect(m.face_list!.filter((f) => f.type === "cylinder").every((f) => f.radius === 2.1)).toBe(true);
  });

  it("the fake's feedback message reads back as a pin, like the core's", async () => {
    const steps = (await ok("GET", `/runs/${feedbackRun}/steps`)) as Step[];
    const msg = steps.find((s) => s.payload["role"] === "user")!;
    const f = parseFeedback(String(msg.payload["text"]));
    expect(f).toMatchObject({ text: "make these holes M3 clearance", location: { partId: "mount_plate", point: [7, 0, 2.5] } });
  });
});
