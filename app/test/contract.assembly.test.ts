// @vitest-environment node
// Contract test, Plan 5 A4: the assembly routes (core/scripts/dump_ui_fixtures.py --only assembly).
// The fake core replays the dump's sequence (a hinge of two mount plates) and must answer with the
// real core's shapes, and — because it runs the app's port of the core solve — the same numbers.
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { z } from "zod";
import {
  assemblyPartSchema, assemblySchema, driveResultSchema, instanceDeletedSchema, instanceSchema, interferenceSchema, jointDeletedSchema,
  jointSchema, projectRowSchema, type Instance, type Joint,
} from "../src/api/assembly";
import { coreErrorBodySchema } from "../src/api/schemas";
import { startFakeCore, type FakeCore } from "./fake-core/server";
import { shapeDiff, type Json } from "./shape";

const HERE = import.meta.dirname;
const DIR = process.env.MISES_CONTRACT_FIXTURES ? resolve(process.env.MISES_CONTRACT_FIXTURES) : resolve(HERE, "../../core/tests/fixtures/ui");
const APP = resolve(HERE, "fixtures/core-ui");
const read = (dir: string, name: string): unknown => JSON.parse(readFileSync(join(dir, name), "utf8"));

let core: FakeCore;
const fake = new Map<string, unknown>();

async function req(method: string, path: string, body?: unknown): Promise<{ status: number; json: unknown }> {
  const headers: Record<string, string> = { Authorization: `Bearer ${core.token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(core.url + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  return { status: r.status, json: await r.json() };
}
async function ok(method: string, path: string, body?: unknown): Promise<unknown> {
  const r = await req(method, path, body);
  expect(r.status, `${method} ${path} ${JSON.stringify(r.json)}`).toBeLessThan(300);
  return r.json;
}

beforeAll(async () => {
  core = await startFakeCore({ port: 0, token: "contract", scenario: "base" });
  // the same steps as dump_assembly()
  const projects = (await ok("GET", "/projects")) as { id: string }[];
  fake.set("projects.json", projects);
  const base = `/projects/${projects[0].id}/assembly`;
  fake.set("assembly_parts.json", await ok("GET", `/parts?project_id=${projects[0].id}`));
  const b = (await ok("POST", `${base}/instances`, { part_id: "mount_plate", name: "base", fixed: true })) as Instance;
  const lid = (await ok("POST", `${base}/instances`, { part_id: "mount_plate", name: "lid" })) as Instance;
  fake.set("assembly_instance_add.json", lid);
  const joint = (await ok("POST", `${base}/joints`, {
    kind: "revolute",
    a: { instance_id: b.id, frame: { origin: [20, 0, 0], axis: [0, 1, 0] } },
    b: { instance_id: lid.id, frame: { origin: [-20, 0, 0], axis: [0, 1, 0] } },
    value: 0, limits: [0, 180],
  })) as Joint;
  fake.set("assembly_joint_add.json", joint);
  fake.set("assembly_drive.json", await ok("POST", `${base}/drive`, { joint_id: joint.id, value: 90 }));
  fake.set("assembly.json", await ok("GET", base));
  fake.set("assembly_interference.json", await ok("GET", `${base}/interference`));
  fake.set("assembly_drive_clamped.json", await ok("POST", `${base}/drive`, { joint_id: joint.id, value: 270 }));
  await ok("POST", `${base}/drive`, { joint_id: joint.id, value: 0 });
  fake.set("assembly_interference_none.json", await ok("GET", `${base}/interference`));
  fake.set("assembly_joint_patch.json", await ok("PATCH", `${base}/joints/${joint.id}`, { limits: [0, 120] }));
  fake.set("assembly_instance_patch.json", await ok("PATCH", `${base}/instances/${b.id}`, { transform: [[1, 0, 0, 0], [0, 1, 0, 30], [0, 0, 1, 0], [0, 0, 0, 1]] }));
  const loop = await req("POST", `${base}/joints`, {
    kind: "fixed",
    a: { instance_id: lid.id, frame: { origin: [0, 0, 0], axis: [0, 0, 1] } },
    b: { instance_id: b.id, frame: { origin: [0, 0, 0], axis: [0, 0, 1] } },
  });
  fake.set("assembly_joint_loop.json", { status: loop.status, body: loop.json });
  fake.set("assembly_joint_delete.json", await ok("DELETE", `${base}/joints/${joint.id}`));
  fake.set("assembly_instance_delete.json", await ok("DELETE", `${base}/instances/${lid.id}`));
});
afterAll(async () => { await core.stop(); });

/** Drive answers key transforms by instance id (data, not schema): compare their values. */
function openMaps(v: unknown): Json {
  const o = v as { transforms?: Record<string, unknown> };
  if (o && typeof o === "object" && !Array.isArray(o) && o.transforms) return { ...(o as Record<string, Json>), transforms: Object.values(o.transforms) as Json };
  return v as Json;
}

const CASES: [string, z.ZodType<unknown>][] = [
  ["projects.json", z.array(projectRowSchema)], ["assembly_parts.json", z.array(assemblyPartSchema)],
  ["assembly_instance_add.json", instanceSchema], ["assembly_joint_add.json", jointSchema],
  ["assembly_drive.json", driveResultSchema], ["assembly.json", assemblySchema],
  ["assembly_interference.json", z.array(interferenceSchema)], ["assembly_drive_clamped.json", driveResultSchema],
  ["assembly_interference_none.json", z.array(interferenceSchema)], ["assembly_joint_patch.json", jointSchema],
  ["assembly_instance_patch.json", instanceSchema], ["assembly_joint_loop.json", z.object({ status: z.number(), body: coreErrorBodySchema })],
  ["assembly_joint_delete.json", jointDeletedSchema], ["assembly_instance_delete.json", instanceDeletedSchema],
];

/** assembly_parts.json: the fake's part rows predate Plan 5 C1's tree fields (A3 adds them). */
const PART_TREE_FIELDS = new Set(["id", "project_id", "label", "parent_id", "order", "color", "kind", "version"]);

describe("contract: assembly routes ↔ schemas ↔ fake core", () => {
  for (const [name, schema] of CASES) {
    describe(name, () => {
      it("schema_parses_the_real_core_answer", () => {
        const p = schema.safeParse(read(DIR, name));
        expect(p.success, p.success ? "" : JSON.stringify(p.error.issues.slice(0, 5))).toBe(true);
      });
      it("fake_core_answer_has_the_same_shape", () => {
        expect(schema.safeParse(fake.get(name)).success).toBe(true);
        let diff = shapeDiff(openMaps(read(DIR, name)), openMaps(fake.get(name)), name);
        if (name === "assembly_parts.json") diff = diff.filter((d) => ![...PART_TREE_FIELDS].some((f) => d.includes(`.${f}`)));
        expect(diff).toEqual([]);
      });
      it("app_unit_test_copy_has_the_same_shape", () => {
        expect(shapeDiff(openMaps(read(DIR, name)), openMaps(read(APP, name)), name)).toEqual([]);
      });
    });
  }

  it("drive_answers_the_real_core's_transforms_and_clamp", () => {
    for (const name of ["assembly_drive.json", "assembly_drive_clamped.json"]) {
      const real = driveResultSchema.parse(read(DIR, name));
      const got = driveResultSchema.parse(fake.get(name));
      expect(got.applied).toBe(real.applied);
      const r = Object.values(real.transforms); const g = Object.values(got.transforms);
      expect(g).toHaveLength(r.length);
      r.forEach((m, k) => m.forEach((row, i) => row.forEach((v, j) => expect(g[k][i][j]).toBeCloseTo(v, 9))));
    }
  });

  it("interference_matches_the_real_core_on_the_hinge", () => {
    expect(fake.get("assembly_interference.json")).toEqual(read(DIR, "assembly_interference.json"));
    expect(fake.get("assembly_interference_none.json")).toEqual([]);
  });

  it("a_loop_is_refused_with_the_core's_status_and_code", () => {
    const real = read(DIR, "assembly_joint_loop.json") as { status: number; body: { error: string; message: string; hint: string } };
    const f = fake.get("assembly_joint_loop.json") as typeof real;
    expect(f.status).toBe(real.status);
    expect(f.body.error).toBe(real.body.error);
    expect(f.body.message.replace(/joint \w+/, "joint X")).toBe(real.body.message.replace(/joint \w+/, "joint X"));
    expect(f.body.hint).toBe(real.body.hint);
  });
});
