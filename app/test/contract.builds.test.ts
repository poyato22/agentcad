// @vitest-environment node
// Contract test, Plan 5 A3: builds, the part tree and live part edits. Real-core fixtures
// (core/scripts/dump_builds_edit_fixtures.py) ↔ the app's schemas ↔ the fake core's answers to
// the same requests (scenario "builds"), refusals included (status, code and message).
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { z } from "zod";
import {
  buildErrorLine, buildSchema, buildTabsProfileSchema, editConflictSchema, emptyTrashRefusedSchema, emptyTrashSchema, partChangedSchema, partEditResultSchema, partSourceSchema,
} from "../src/api/builds";
import { deletedRunIds } from "../src/state/reduce";
import { coreErrorBodySchema, partSummarySchema } from "../src/api/schemas";
import type { MisesEvent } from "../src/api/types";
import { startFakeCore, type FakeCore } from "./fake-core/server";
import { shapeDiff, type Json } from "./shape";

const HERE = import.meta.dirname;
const DIR = process.env.MISES_CONTRACT_FIXTURES ? resolve(process.env.MISES_CONTRACT_FIXTURES) : resolve(HERE, "../../core/tests/fixtures/ui");
const APP = resolve(HERE, "fixtures/core-ui");
const read = (dir: string, name: string): unknown => JSON.parse(readFileSync(join(dir, name), "utf8"));

let core: FakeCore;
const fake = new Map<string, unknown>();
const events: MisesEvent[] = [];

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

beforeAll(async () => {
  core = await startFakeCore({ port: 0, token: "contract", scenario: "builds" });
  const ws = new WebSocket(`${core.url.replace("http", "ws")}/events?token=contract`);
  ws.onmessage = (m) => events.push(JSON.parse(String(m.data)) as MisesEvent);
  await new Promise((r) => { ws.onopen = r; });

  fake.set("projects.json", await ok("GET", "/projects"));
  const bench = (await ok("POST", "/projects", { name: "Bench rig 2" })) as { id: string };
  fake.set("project_create.json", bench);
  fake.set("project_rename.json", await ok("PATCH", `/projects/${bench.id}`, { name: "Bench test rig" }));
  fake.set("project_create_empty.json", await req("POST", "/projects", { name: "  " }));

  fake.set("part_patch.json", await ok("PATCH", "/parts/quad_arm", { color: "#d9822b", label: "Quad arm" }));
  fake.set("part_patch_parent.json", await ok("PATCH", "/parts/top_plate", { parent_id: "quad_arm" }));
  fake.set("part_patch_cycle.json", await req("PATCH", "/parts/quad_arm", { parent_id: "top_plate" }));
  fake.set("part_patch_color_bad.json", await req("PATCH", "/parts/quad_arm", { color: "orange" }));
  await ok("PATCH", "/parts/top_plate", { parent_id: null });
  fake.set("parts_reorder.json", await ok("POST", "/parts/reorder", { project_id: "q0quadframe0", parent_id: null, ids: ["top_plate", "quad_arm"] }));
  fake.set("parts_reorder_bad.json", await req("POST", "/parts/reorder", { project_id: "q0quadframe0", parent_id: null, ids: ["top_plate", "top_plate"] }));

  const src = (await ok("GET", "/parts/quad_arm/source")) as { version: number };
  fake.set("part_source.json", src);
  let v = src.version;
  fake.set("part_vars.json", await ok("POST", "/parts/quad_arm/vars", { vars: { thickness: 5 }, base_version: v }));
  fake.set("part_vars_stale.json", await req("POST", "/parts/quad_arm/vars", { vars: { thickness: 6 }, base_version: v }));
  fake.set("part_vars_no_base.json", await req("POST", "/parts/quad_arm/vars", { vars: { thickness: 6 } }));
  v += 1;
  const arm = ((await ok("GET", "/parts?project_id=q0quadframe0")) as { id: string; metrics: { face_list: { id: string; normal: number[]; center: number[] }[]; edge_list: { id: string }[] } }[])
    .find((p) => p.id === "quad_arm");
  const top = arm!.metrics.face_list.find((f) => f.normal[2] > 0.9)!;
  fake.set("part_ops.json", await ok("POST", "/parts/quad_arm/ops", { op: "hole", target: { face_id: top.id }, params: { diameter: 3, through: true, point: top.center }, base_version: v }));
  v += 1;
  fake.set("part_ops_fillet.json", await ok("POST", "/parts/quad_arm/ops", { op: "fillet", target: { edge_ids: [arm!.metrics.edge_list[0].id] }, params: { radius: 1 }, base_version: v }));
  v += 1;
  fake.set("part_ops_bad_face.json", await req("POST", "/parts/quad_arm/ops", { op: "hole", target: { face_id: "f999" }, params: { diameter: 3 }, base_version: v }));
  const src2 = (await ok("GET", "/parts/quad_arm/source")) as { source: string; version: number };
  fake.set("part_source_ops.json", src2);
  const sphere = src2.source.replace("Box(", "Sphere(");
  fake.set("part_source_rematch_fail.json", await req("PUT", "/parts/quad_arm/source", { source: sphere, base_version: src2.version }));
  fake.set("part_source_put.json", await ok("PUT", "/parts/quad_arm/source", { source: `${src2.source}\n# tweak\n`, base_version: src2.version }));
  fake.set("part_source_stale.json", await req("PUT", "/parts/quad_arm/source", { source: src2.source, base_version: src2.version }));
  fake.set("parts_tree.json", await ok("GET", "/parts"));

  fake.set("project_trash.json", await ok("DELETE", `/projects/${bench.id}`));
  fake.set("projects_with_trash.json", await ok("GET", "/projects"));
  fake.set("project_restore.json", await ok("POST", `/projects/${bench.id}/restore`));
  await ok("DELETE", `/projects/${bench.id}`);
  fake.set("projects_trash_empty.json", await ok("POST", "/projects/trash/empty"));
  // D30: Delete forever on one Trash tile
  const scrap = (await ok("POST", "/projects", { name: "Scrap" })) as { id: string };
  await ok("DELETE", `/projects/${scrap.id}`);
  fake.set("projects_purge.json", await ok("POST", `/projects/${scrap.id}/purge`));
  // a trashed build with a live (external) run: Empty Trash refuses as a whole (QA I4)
  const doomed = (await ok("POST", "/projects", { name: "Doomed" })) as { id: string };
  const live = (await ok("POST", "/runs/external", { agent: "fixture-acp", provider: "acp", model: "fixture", project_id: doomed.id })) as { run_id: string };
  await ok("DELETE", `/projects/${doomed.id}`);
  fake.set("projects_trash_empty_busy.json", await req("POST", "/projects/trash/empty"));
  await ok("POST", `/runs/${live.run_id}/cancel`);
  await ok("DELETE", `/runs/${live.run_id}`);
  await ok("POST", "/projects/trash/empty");
  fake.set("profile_builds.json", await ok("PUT", "/profile", { active_project_id: "q0quadframe0", open_project_ids: ["q0quadframe0"] }));
  await new Promise((r) => setTimeout(r, 50));
  ws.close();
});
afterAll(async () => { await core.stop(); });

const parts = z.array(partSummarySchema);
const CASES: [string, z.ZodType<unknown>][] = [
  ["projects.json", z.array(buildSchema)], ["projects_with_trash.json", z.array(buildSchema)], ["project_create.json", buildSchema],
  ["project_rename.json", buildSchema], ["project_trash.json", buildSchema], ["project_restore.json", buildSchema],
  ["projects_trash_empty.json", emptyTrashSchema], ["projects_purge.json", emptyTrashSchema], ["profile_builds.json", buildTabsProfileSchema],
  ["part_patch.json", partSummarySchema], ["part_patch_parent.json", partSummarySchema], ["parts_reorder.json", parts], ["parts_tree.json", parts],
  ["part_source.json", partSourceSchema], ["part_source_ops.json", partSourceSchema],
  ["part_vars.json", partEditResultSchema], ["part_ops.json", partEditResultSchema], ["part_ops_fillet.json", partEditResultSchema],
  ["part_source_put.json", partEditResultSchema],
];

/** Compared as the app reads them: the profile's build-tab fields only (the rest is the profile
 *  contract's), and a source's var rows normalized (the core leaves step / doc out of an
 *  inferred row, which a first-row shape comparison would flag). */
function pick(name: string, v: unknown): unknown {
  if (name === "profile_builds.json") return buildTabsProfileSchema.parse(v);
  if (name.startsWith("part_source") && !name.includes("put")) return partSourceSchema.parse(v);
  return v;
}

/** Refusals: [fixture, compare the message too]. */
const REFUSALS: [string, boolean][] = [
  ["project_create_empty.json", true], ["part_patch_cycle.json", true], ["part_patch_color_bad.json", true], ["parts_reorder_bad.json", true],
  ["part_vars_no_base.json", true], ["part_ops_bad_face.json", true], ["part_vars_stale.json", false], ["part_source_stale.json", false],
  ["part_source_rematch_fail.json", false],
];

describe("contract: builds, part tree, live edits ↔ schemas ↔ fake core", () => {
  for (const [name, schema] of CASES) {
    describe(name, () => {
      it("schema_parses_the_real_core_answer", () => {
        const p = schema.safeParse(read(DIR, name));
        expect(p.success, p.success ? "" : JSON.stringify(p.error.issues.slice(0, 5))).toBe(true);
      });
      it("fake_core_answer_has_the_same_shape", () => {
        expect(schema.safeParse(fake.get(name)).success).toBe(true);
        expect(shapeDiff(pick(name, read(DIR, name)) as Json, pick(name, fake.get(name)) as Json, name)).toEqual([]);
      });
      it("app_unit_test_copy_has_the_same_shape", () => {
        expect(shapeDiff(pick(name, read(DIR, name)) as Json, pick(name, read(APP, name)) as Json, name)).toEqual([]);
      });
    });
  }

  for (const [name, sameMessage] of REFUSALS) {
    it(`${name}: same status and code as the core${sameMessage ? ", and the same words" : ""}`, () => {
      const real = read(DIR, name) as { status: number; body: Record<string, unknown> };
      const f = fake.get(name) as { status: number; body: Record<string, unknown> };
      expect(f.status).toBe(real.status);
      expect(f.body.error).toBe(real.body.error);
      expect(Object.keys(f.body).sort()).toEqual(Object.keys(real.body).sort());
      if (sameMessage) {
        expect(f.body.message).toBe(real.body.message);
        expect(f.body.hint ?? null).toBe(real.body.hint ?? null);
      }
      if (real.status !== 422) expect(coreErrorBodySchema.safeParse(real.body).success).toBe(true);
    });
  }

  it("projects_trash_empty_busy.json: a live run in the Trash is 409 {reason, run_ids, …}, read as refused", () => {
    const real = read(DIR, "projects_trash_empty_busy.json") as { status: number; body: { run_ids: string[]; project_ids: string[]; message: string; hint: string } };
    const f = fake.get("projects_trash_empty_busy.json") as typeof real;
    expect(real.status).toBe(409);
    const refused = emptyTrashRefusedSchema.parse(real.body);
    expect(refused.reason).toBe("running");
    expect(refused.run_ids).toHaveLength(1);
    expect(f.status).toBe(409);
    expect(shapeDiff(real.body as unknown as Json, f.body as unknown as Json, "trash_busy")).toEqual([]);
    expect(emptyTrashRefusedSchema.parse(f.body)).toMatchObject({ reason: "running", message: real.body.message, hint: real.body.hint });
    expect(f.body.run_ids).toHaveLength(1);
  });

  it("run_deleted: the real event names the run (run_id and data.run_id), the fake's too", () => {
    const lines = readFileSync(join(DIR, "events.jsonl"), "utf8").split("\n").filter((l) => l.trim()).map((l) => JSON.parse(l) as MisesEvent);
    const real = lines.find((e) => e.type === "run_deleted")!;
    expect(real.run_id).toBe(real.data.run_id);
    expect(deletedRunIds(real)).toEqual([real.run_id]);
    const f = events.find((e) => e.type === "run_deleted");
    expect(f, "the fake core sent run_deleted").toBeDefined();
    expect(shapeDiff(real.data as Json, f!.data as Json, "$run_deleted")).toEqual([]);
  });

  it("a stale edit's 409 carries current_version (and, for a merge edit, the current vars)", () => {
    for (const name of ["part_vars_stale.json", "part_source_stale.json"]) {
      const real = editConflictSchema.parse((read(DIR, name) as { body: unknown }).body);
      const f = editConflictSchema.parse((fake.get(name) as { body: unknown }).body);
      expect(typeof real.current_version).toBe("number");
      expect(f.vars === null).toBe(real.vars === null);
      expect(f.source === null).toBe(real.source === null);
    }
  });

  it("a failed op re-match: both name the op in the line the app shows", () => {
    const line = (v: unknown) => buildErrorLine(String((v as { body: { message: string } }).body.message));
    expect(line(read(DIR, "part_source_rematch_fail.json"))).toMatch(/^mises-op 0 hole: target face not found .* — remove or redo the op$/);
    expect(line(fake.get("part_source_rematch_fail.json"))).toBe(line(read(DIR, "part_source_rematch_fail.json")));
  });

  it("part_changed events: the same data shape, by user on the ui run", () => {
    const real = readFileSync(join(DIR, "builds_events.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l) as MisesEvent);
    const mine = events.filter((e) => e.type === "part_changed");
    expect(mine.length).toBeGreaterThan(3);
    for (const e of [...real, ...mine]) expect(partChangedSchema.safeParse(e.data).success).toBe(true);
    expect(shapeDiff(real[2] as unknown as Json, mine[0] as unknown as Json, "event")).toEqual([]);
    expect(mine.every((e) => e.data.by === "user" && e.data.run_id === null)).toBe(true);
  });
});
