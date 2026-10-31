// @vitest-environment node
// Contract test, Task 9: the rest of the core's routes (core/scripts/dump_ui_fixtures.py
// dump_more), and a coverage check: every route the core serves (its FastAPI decorators) has a
// real-core fixture checked by a contract test, or a written reason it has none.
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { z } from "zod";
import {
  agentDetailSchema, agentInfoSchema, agentRunSchema, approvalSchema, cancelAllSchema, cleanupResultSchema, coreErrorBodySchema, keyStatusSchema,
  localActionSchema, okSchema, runIdSchema, runNodeSchema,
  partSummarySchema,
} from "../src/api/schemas";
import { assemblyUndoSchema, parsePendingQuestions, toolListSchema } from "../src/api/client";
import type { Approval } from "../src/api/types";
import { loadScenario, startFakeCore, type FakeCore, type Scenario } from "./fake-core/server";
import { shapeDiff, type Json } from "./shape";
import { parseSkeleton } from "../src/ui/viewer/skeleton";

const HERE = import.meta.dirname;
const DIR = process.env.MISES_CONTRACT_FIXTURES ? resolve(process.env.MISES_CONTRACT_FIXTURES) : resolve(HERE, "../../core/tests/fixtures/ui");
const APP = resolve(HERE, "fixtures/core-ui");
const SERVER_SRC = resolve(HERE, "../../core/src/mises/server");
const read = (dir: string, name: string): unknown => JSON.parse(readFileSync(join(dir, name), "utf8"));

let core: FakeCore;
const fake = new Map<string, unknown>();

async function req(method: string, path: string, body?: unknown): Promise<{ status: number; json: unknown }> {
  const headers: Record<string, string> = { Authorization: `Bearer ${core.token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(core.url + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  return { status: r.status, json: r.headers.get("content-type")?.includes("json") ? await r.json() : await r.text() };
}
async function ok(method: string, path: string, body?: unknown): Promise<unknown> {
  const r = await req(method, path, body);
  expect(r.status, `${method} ${path}`).toBe(200);
  return r.json;
}
async function until<T>(get: () => Promise<T>, done: (v: T) => boolean): Promise<T> {
  for (let i = 0; i < 300; i++) {
    const v = await get();
    if (done(v)) return v;
    await new Promise((r) => setTimeout(r, 10));
  }
  throw new Error("never settled");
}

beforeAll(async () => {
  core = await startFakeCore({ port: 0, token: "contract", scenario: "base" });
  const runId = loadScenario("base").runs[0].id;
  core.reset("base");
  fake.set("health.json", await ok("GET", "/health"));
  fake.set("run.json", await ok("GET", `/runs/${runId}`));
  // QA C-7 / C-2: the tools picker's list, and what the run is blocked on (its pending card)
  fake.set("tools.json", await ok("GET", "/tools"));
  fake.set("run_pending.json", await ok("GET", `/runs/${runId}/pending`));
  fake.set("context.json", await ok("GET", "/context"));
  fake.set("key_set.json", await ok("POST", "/keys/openai", { secret: "sk-fixture-0000" }));
  fake.set("key_delete.json", await ok("DELETE", "/keys/openai"));
  fake.set("drawing.dxf", await ok("GET", "/parts/mount_plate/drawing.dxf"));
  // Plan 7 U8's parts manager (dump_builds_edit_fixtures.py: duplicate mount_plate, delete the copy)
  const dup = (await ok("POST", "/parts/mount_plate/duplicate")) as { part_id: string };
  fake.set("part_duplicate.json", dup);
  fake.set("part_delete.json", await ok("DELETE", `/parts/${dup.part_id}`));
  // Plan 8 G3's skeleton panel route (dump_builds_edit_fixtures.py: the Default build, none yet)
  const builds = (await ok("GET", "/projects")) as { id: string }[];
  // Plan 9 D29 (dump_builds_edit_fixtures.py): a copy placed in the assembly, deleted with its
  // instance, then undone
  const dup2 = (await ok("POST", "/parts/mount_plate/duplicate")) as { part_id: string };
  await req("POST", `/projects/${builds[0].id}/assembly/instances`, { part_id: dup2.part_id, name: "plate copy", fixed: true });
  const gone = (await ok("DELETE", `/parts/${dup2.part_id}?cascade=true`)) as { undo: string };
  fake.set("part_delete_cascade.json", gone);
  fake.set("part_restore.json", await ok("POST", `/parts/${dup2.part_id}/restore`, { undo: gone.undo }));
  const taken = (await ok("POST", `/projects/${builds[0].id}/assembly/remove_parts`, { part_ids: [dup2.part_id] })) as { undo: unknown };
  fake.set("assembly_remove_parts.json", taken);
  fake.set("assembly_restore.json", await ok("POST", `/projects/${builds[0].id}/assembly/restore`, taken.undo));
  fake.set("project_skeleton_empty.json", await ok("GET", `/projects/${builds[0].id}/skeleton`));
  // POST /runs: the real core's dump started its run from a sketch (sketch_run.json); the answer is
  // the same {run_id} without one, and the app no longer sends sketches (Plan 7 U2b)
  fake.set("sketch_run.json", await ok("POST", "/runs", { agent: "designer", prompt: "make this bracket" }));
  const ext = (await ok("POST", "/runs/external", { agent: "fixture-acp", provider: "acp", model: "fixture" })) as { run_id: string };
  fake.set("run_external.json", ext);
  fake.set("run_cancel.json", await ok("POST", `/runs/${ext.run_id}/cancel`));
  const card = ((await ok("GET", "/approvals")) as Approval[])[0];
  fake.set("approval_decide.json", await ok("POST", `/approvals/${card.id}/decide`, { payload_hash: card.payload_hash, approve: false }));
  fake.set("cancel_all.json", await ok("POST", "/runs/cancel_all"));
  // Plan 5 (A2): same agent as the dump script's agent_get.json
  fake.set("agent_create.json", await ok("POST", "/agents", { name: "rib-checker", description: "Checks rib spacing", model: "inherit",
    tools: ["mirror_measure"], body: "Measure every rib.\nReport the spacing." }));
  fake.set("agent_get.json", await ok("GET", "/agents/rib-checker"));
  fake.set("agent_put.json", await ok("PUT", "/agents/rib-checker", { name: "rib-checker", description: "Checks rib spacing and height",
    model: "inherit", tools: ["mirror_measure"], body: "Measure every rib.\nReport spacing and height." }));
  fake.set("agent_delete.json", await ok("DELETE", "/agents/rib-checker"));
  // Plan 5 (C1 / A2): the agent manager's run routes, as in the dump script
  fake.set("runs_tree.json", await ok("GET", "/runs/tree"));
  fake.set("runs_cleanup.json", await ok("POST", "/runs/cleanup", { dry_run: true }));
  const ext2 = (await ok("POST", "/runs/external", { agent: "fixture-acp", provider: "acp", model: "fixture" })) as { run_id: string };
  await ok("POST", `/runs/${ext2.run_id}/cancel`);
  fake.set("run_patch.json", await ok("PATCH", `/runs/${ext2.run_id}`, { title: "Fixture agent", pinned: true }));
  fake.set("run_delete.json", await ok("DELETE", `/runs/${ext2.run_id}`));

  core.reset("agent-ux");
  await ok("POST", "/local/start");                        // like the dump: Ollama answers
  fake.set("local_pull.json", await ok("POST", "/local/pull", { model: "qwen3-coder:30b" }));

  const sc: Scenario = { ...loadScenario("base"), print: { slice_ms: 20, card_ms: 10 } };
  core.reset(sc);
  await ok("POST", "/printers", { name: "Bench MK4", host: "127.0.0.1:8080", api_key: "fake-key-abc" });
  const sl = (await ok("POST", "/slices", { part_id: "mount_plate", printer_id: "bench_mk4" })) as { slice_id: string };
  await until(async () => (await ok("GET", `/slices/${sl.slice_id}`)) as { status: string }, (v) => v.status !== "running");
  const refused = await req("POST", `/slices/${sl.slice_id}/cancel`);
  fake.set("slice_cancel_finished.json", { status: refused.status, body: refused.json });
});
afterAll(async () => { await core.stop(); });

const errorAnswer = z.object({ status: z.number(), body: coreErrorBodySchema });
/** GET /runs/{id}/pending (and GET /runs/{id}'s `pending`): open questions and pending cards. */
const runPendingSchema = z.object({ questions: z.array(z.unknown()), approvals: z.array(approvalSchema) });
const anyJson = z.unknown();
/** GET /projects/{id}/skeleton (core skeleton_tools.skeleton_view). */
const projectSkeletonSchema = z.object({
  project_id: z.string(), skeleton_id: z.string(), version: z.number(),
  skeleton: z.record(z.string(), z.unknown()).nullable(), dependents: z.array(z.unknown()),
});
/** DELETE /parts/{id} (Plan 9 D29: what went with it, and the Undo token). */
const partDeleteSchema = z.object({ deleted: z.string(), removed_instances: z.array(z.string()), removed_joints: z.array(z.string()), undo: z.string() });
const CASES: [string, z.ZodType<unknown>][] = [
  ["health.json", okSchema], ["run.json", agentRunSchema], ["context.json", anyJson],
  ["key_set.json", keyStatusSchema], ["key_delete.json", keyStatusSchema],
  ["sketch_run.json", runIdSchema],
  ["local_pull.json", localActionSchema], ["slice_cancel_finished.json", errorAnswer],
  ["run_external.json", runIdSchema], ["run_cancel.json", okSchema], ["approval_decide.json", approvalSchema],
  ["cancel_all.json", cancelAllSchema], ["agent_get.json", agentDetailSchema],
  ["agent_create.json", agentInfoSchema], ["agent_put.json", agentInfoSchema], ["agent_delete.json", okSchema],
  ["runs_tree.json", z.array(runNodeSchema)], ["runs_cleanup.json", cleanupResultSchema],
  ["run_patch.json", agentRunSchema], ["run_delete.json", okSchema],
  ["tools.json", toolListSchema], ["run_pending.json", runPendingSchema],
  ["part_duplicate.json", partSummarySchema], ["part_delete.json", partDeleteSchema],
  ["part_delete_cascade.json", partDeleteSchema],
  ["assembly_remove_parts.json", z.object({ removed_instances: z.array(z.string()), removed_joints: z.array(z.string()), undo: assemblyUndoSchema })],
  ["assembly_restore.json", z.object({ restored_instances: z.number(), restored_joints: z.number() })], ["part_restore.json", partSummarySchema.and(z.object({ restored_instances: z.number().int() }))],
  ["project_skeleton_empty.json", projectSkeletonSchema],
];

describe("contract: remaining routes ↔ schemas ↔ fake core", () => {
  for (const [name, schema] of CASES) {
    describe(name, () => {
      it("schema_parses_the_real_core_answer", () => {
        const p = schema.safeParse(read(DIR, name));
        expect(p.success, p.success ? "" : JSON.stringify(p.error.issues.slice(0, 5))).toBe(true);
      });
      it("fake_core_answer_has_the_same_shape", () => {
        expect(schema.safeParse(fake.get(name)).success).toBe(true);
        expect(shapeDiff(read(DIR, name) as Json, fake.get(name) as Json, name)).toEqual([]);
      });
      it("app_unit_test_copy_has_the_same_shape", () => {
        expect(shapeDiff(read(DIR, name) as Json, read(APP, name) as Json, name)).toEqual([]);
      });
    });
  }

  it("refusing to cancel a finished slice: same status, code and message as the core", () => {
    const real = read(DIR, "slice_cancel_finished.json") as { status: number; body: { error: string; message: string } };
    const f = fake.get("slice_cancel_finished.json") as typeof real;
    expect(f.status).toBe(real.status);
    expect(f.body.error).toBe(real.body.error);
    expect(f.body.message).toBe(real.body.message);
  });

  it("tools.json: every real tool keeps its name and safety class (the picker's list)", () => {
    const tools = toolListSchema.parse(read(DIR, "tools.json"));
    expect(tools.length).toBeGreaterThan(40);
    expect(tools.every((t) => t.name && t.safety_class)).toBe(true);
    expect(tools.find((t) => t.name === "print_send")?.safety_class).toBe("irreversible");
    // the fake core lists the same tools (the dump's test-only fixture_merge aside)
    const fakeNames = new Set(toolListSchema.parse(fake.get("tools.json")).map((t) => t.name));
    expect(tools.map((t) => t.name).filter((n) => n !== "fixture_merge" && !fakeNames.has(n))).toEqual([]);
  });

  it("pending: the run's open card, and a batched question a reloaded app recovers", () => {
    const run = read(DIR, "run.json") as { id: string; pending: unknown };
    expect(runPendingSchema.parse(run.pending).approvals).toHaveLength(1);
    const q = read(DIR, "run_pending_question.json") as { questions: unknown[] };
    const runId = (q.questions[0] as { run_id: string }).run_id;
    const qs = parsePendingQuestions(runId, q.questions);
    expect(qs).toHaveLength(1);
    expect(qs[0].items?.map((x) => x.id)).toEqual(["use", "size_class", "motor_prop"]);
    expect(qs[0].run_id).toBe(runId);
  });

  it("project_skeleton.json: the panel reads a saved skeleton's params, datums and envelope", () => {
    const real = projectSkeletonSchema.parse(read(DIR, "project_skeleton.json"));
    const v = parseSkeleton(real, real.skeleton_id);
    expect(v?.version).toBe(real.version);
    expect(v?.params.map(([k]) => k)).toEqual(["arm_len", "motor_d"]);
    expect(v?.datums.map(([k]) => k)).toEqual(["deck"]);
    expect(v?.envelope).not.toBeNull();
    expect(shapeDiff(read(DIR, "project_skeleton.json") as Json, read(APP, "project_skeleton.json") as Json, "project_skeleton.json")).toEqual([]);
  });

  it("drawing.dxf: both are DXF text (sections, an ENTITIES section, EOF)", () => {
    for (const dxf of [readFileSync(join(DIR, "drawing.dxf"), "utf8"), String(fake.get("drawing.dxf"))]) {
      expect(dxf).toMatch(/^\s*0\r?\nSECTION\r?\n/);
      expect(dxf).toMatch(/\n\s*2\r?\nENTITIES\r?\n/);
      expect(dxf.trimEnd()).toMatch(/\n\s*0\r?\nEOF$/);
    }
  });
});

// ---- every core route is covered

/** Route → the fixture(s) whose contract test covers it, or why it has none. */
const COVERAGE: Record<string, string> = {
  "GET /health": "health.json", "GET /status": "status.json", "GET /agents": "agents.json", "GET /models": "models.json",
  "GET /agents/{name}": "agent_get.json",
  "GET /parts": "parts.json", "GET /parts/{part_id}/model.glb": "mount_plate.glb",
  "GET /parts/{part_id}/thumb.png": "EXEMPT: a PNG rendered in the sandboxed worker (needs a real build); core test_thumb_api + test_thumbs cover it end to end",
  "GET /projects/{project_id}/thumb.png": "EXEMPT: a PNG render of the build's assembly in the sandboxed worker (D24 cover); core test_thumb_api + test_thumbs cover it",
  "GET /runs": "runs.json", "GET /runs/{run_id}": "run.json", "GET /runs/{run_id}/steps": "steps.json",
  "GET /runs/{run_id}/pending": "run_pending.json", "GET /tools": "tools.json",
  "POST /runs": "sketch_run.json", "POST /runs/external": "run_external.json", "POST /runs/{run_id}/cancel": "run_cancel.json",
  "POST /runs/cancel_all": "cancel_all.json",
  "POST /runs/{run_id}/message": "EXEMPT: needs a live turn of a real model; the e2e suite drives it against the fake core",
  "POST /runs/{run_id}/resume": "EXEMPT: the app never calls it (CLI resume of an interrupted run)",
  "GET /approvals": "approvals.json", "POST /approvals/{approval_id}/decide": "approval_decide.json",
  "POST /questions/{question_id}/answer": "question_answer.json",
  "GET /profile": "profile.json", "PUT /profile": "profile_put.json", "GET /keys": "keys.json",
  "POST /keys/{vendor}": "key_set.json", "DELETE /keys/{vendor}": "key_delete.json",
  "GET /setup": "setup.json", "POST /setup/complete": "setup_complete.json",
  "GET /claude-code/status": "claude_code_status.json", "POST /claude-code/login": "claude_code_login.json",
  "POST /claude-code/logout": "claude_code_logout.json", "POST /onshape/test": "onshape_test.json",
  "GET /context": "context.json", "PUT /context": "context_put.json",
  "POST /context": "EXEMPT: an alias of PUT /context (context_put.json); the app sends PUT",
  "GET /local/status": "local_status.json", "POST /local/test": "local_test.json", "POST /local/start": "local_start.json",
  "POST /local/pull": "local_pull.json", "POST /local/pull/cancel": "local_pull_cancel.json",
  "GET /sims": "sims_list.json", "POST /sims": "sims_start.json", "GET /sims/solvers": "sims_solvers.json",
  "GET /sims/{sim_id}": "sim_done.json", "GET /sims/{sim_id}/result.glb": "sim_result.glb",
  "POST /sketches": "EXEMPT: Plan 7 U2b removed the app's Sketch page; the app never calls it (core sketch tests cover it)",
  "GET /sketches/{sketch_id}": "EXEMPT: Plan 7 U2b removed the app's Sketch page; the app never calls it (core sketch tests cover it)",
  // Plan 8 G1 render images and reference uploads
  "GET /renders/{name}": "EXEMPT: PNG bytes of a sandboxed render; core test_renders_api covers ids, links and auth, and the cards that name them are g1_cards.json (contract-checked in the critique card tests)",
  "POST /references": "EXEMPT: Plan 8 G-R4 reference images; the app has no upload UI yet (the agent reads them from context); core test_references covers it",
  "GET /references/{reference_id}": "EXEMPT: Plan 8 G-R4 reference images; the app has no viewer for them yet; core test_references covers it",
  // Plan 7 U5 drawing import (contract.imports.test.ts)
  "POST /imports/drawing": "drawing_import_upload.json", "GET /imports/{import_id}": "drawing_import_done.json",
  "GET /imports/{import_id}/original": "drawing_import_original.svg",
  "GET /parts/{part_id}/drawing.json": "drawing.json", "GET /parts/{part_id}/drawing.svg": "drawing.svg",
  "GET /parts/{part_id}/drawing.dxf": "drawing.dxf",
  "GET /print/capabilities": "print_capabilities.json", "GET /printers": "printers.json", "POST /printers": "printer_add.json",
  "DELETE /printers/{printer_id}": "printer_delete.json", "GET /printers/{printer_id}/status": "printer_status.json",
  "POST /slices": "slice_start.json", "GET /slices/{slice_id}": "slice.json",
  "POST /slices/{slice_id}/cancel": "slice_cancel_finished.json",
  "GET /slices/{slice_id}/preview.png": "EXEMPT: the core tests' fake PrusaSlicer embeds no thumbnail; the 404 answer is a core error body",
  "POST /prints": "print_start.json", "GET /prints/{call_id}": "print_done.json",
  // Plan 5 A3 builds, part tree and live edits: contract.builds.test.ts
  "PATCH /projects/{project_id}": "project_rename.json", "PATCH /parts/{part_id}": "part_patch.json",
  // Plan 5 builds / runs / agents / assembly / live edits: real-core fixtures (contract.builds,
  // contract.assembly and this file's CASES); the few EXEMPT ones say why.
  "DELETE /agents/{name}": "agent_delete.json",
  "DELETE /projects/{project_id}": "project_trash.json",
  "DELETE /runs/{run_id}": "run_delete.json",
  "GET /projects": "projects_with_trash.json",
  "PATCH /projects/{project_id}/assembly/instances/{iid}": "assembly_instance_patch.json",
  "PATCH /projects/{project_id}/assembly/joints/{jid}": "assembly_joint_patch.json",
  "PATCH /runs/{run_id}": "run_patch.json",
  "GET /runs/tree": "runs_tree.json",
  "POST /agents": "agent_create.json",
  "POST /parts/reorder": "parts_reorder.json",
  // Plan 7 U8: the part menu's duplicate / delete / export (core tests/server/test_parts_manager_api.py)
  "POST /parts/{part_id}/duplicate": "part_duplicate.json",   // Plan 7 U8 (dump_builds_edit_fixtures.py)
  "DELETE /parts/{part_id}": "part_delete.json",
  "POST /parts/{part_id}/restore": "part_restore.json",
  "POST /projects/{project_id}/assembly/remove_parts": "assembly_remove_parts.json",   // Plan 9 D29
  "POST /projects/{project_id}/assembly/restore": "assembly_restore.json",   // Plan 9 D29 (dump_builds_edit_fixtures.py)
  "POST /parts/{part_id}/export": "EXEMPT: Plan 7 U8; writes to ~/Downloads (MISES_EXPORT_DIR), so the dump can't run it on the dev home; core test_parts_manager_api covers it",
  "POST /projects": "project_create.json",
  "POST /projects/trash/empty": "projects_trash_empty.json",
  "POST /projects/{project_id}/purge": "projects_purge.json",
  "POST /projects/{project_id}/restore": "project_restore.json",
  "POST /runs/cleanup": "runs_cleanup.json",
  "PUT /agents/{name}": "agent_put.json",
  "DELETE /projects/{project_id}/assembly/instances/{iid}": "assembly_instance_delete.json",
  "DELETE /projects/{project_id}/assembly/joints/{jid}": "assembly_joint_delete.json",
  "GET /projects/{project_id}/assembly": "assembly.json",
  "GET /projects/{project_id}/assembly.glb": "EXEMPT: the app poses each part's own model.glb (parts move at frame rate); the combined GLB is for export. Core API tests cover it",
  "GET /projects/{project_id}/assembly/interference": "assembly_interference.json",
  "POST /projects/{project_id}/assembly/drive": "assembly_drive.json",
  "POST /projects/{project_id}/assembly/instances": "assembly_instance_add.json",
  "POST /projects/{project_id}/assembly/joints": "assembly_joint_add.json",
  "GET /parts/{part_id}/source": "part_source.json",
  "PUT /parts/{part_id}/source": "part_source_put.json",
  "POST /parts/{part_id}/vars": "part_vars.json",
  "POST /parts/{part_id}/ops": "part_ops.json",
  "GET /system/check": "EXEMPT: Plan 5 C6 system check core route; A1's system check UI runs against the fake core's systemRoutes; real-core fixture to follow (core test_system_api covers it)",
  "POST /system/install/{dep}": "EXEMPT: Plan 5 C6 system check core route; A1's UI runs against the fake core's systemRoutes; real-core fixture to follow (core test_system_api covers it)",
  // Plan 5 C5 Onshape browser / budget / parts directory: contract.directory.test.ts (A6)
  "DELETE /catalog/allowed_domains/{domain}": "catalog_allowed_domains_remove.json",
  "DELETE /catalog/items/{item_id}": "catalog_delete.json",
  "GET /catalog/allowed_domains": "catalog_allowed_domains.json",
  "GET /catalog/categories": "catalog_categories.json",
  "GET /catalog/items/{item_id}": "catalog_item.json",
  "GET /catalog/search": "catalog_search.json",
  "GET /onshape/budget": "onshape_budget.json",
  "GET /onshape/documents": "onshape_documents.json",
  "GET /onshape/documents/{did}/elements": "onshape_elements.json",
  "GET /onshape/thumbnail": "onshape_thumbnail.png",
  "POST /catalog/allowed_domains": "catalog_allowed_domains_add.json",
  "POST /catalog/import": "catalog_import.json",
  "POST /catalog/items/{item_id}/insert": "catalog_insert.json",
  "POST /onshape/import": "onshape_import.json",
  "PATCH /catalog/items/{item_id}": "catalog_patch.json",
  // Plan 5 A7 spec panel (contract.spec.test.ts)
  "GET /projects/{project_id}/spec": "spec.json", "PUT /projects/{project_id}/spec": "spec_put.json",
  "GET /projects/{project_id}/studies": "studies.json", "GET /projects/{project_id}/spec/check": "spec_check.json",
  "POST /projects/{project_id}/studies/{study_id}/apply": "study_apply.json",
  "DELETE /sims/materials/{name}": "study_material_delete.json", // contract.studies.test.ts (Task A8)
  "DELETE /sims/templates/{tpl_id}": "study_template_delete.json", // contract.studies.test.ts (Task A8)
  "GET /sims/materials": "study_materials.json", // contract.studies.test.ts (Task A8)
  "GET /sims/templates": "study_templates.json", // contract.studies.test.ts (Task A8)
  "GET /sims/types": "study_types.json", // contract.studies.test.ts (Task A8)
  "GET /studies/context": "studies_context.json", // contract.studiesAgent.test.ts (Plan 9 U-D)
  "GET /sims/{sim_id}/charts": "study_wind_charts.json", // contract.studies.test.ts (Task A8)
  "GET /sims/{sim_id}/probe": "study_probe.json", // contract.studies.test.ts (Task A8)
  "GET /sims/{sim_id}/section": "study_section.json", // contract.studies.test.ts (Task A8)
  "GET /sims/{sim_id}/slices/{name}.png": "study_slice_xz.png", // contract.studies.test.ts (Task A8)
  "POST /sims/compare": "study_compare.json", // contract.studies.test.ts (Task A8)
  "POST /sims/materials": "study_material_add.json", // contract.studies.test.ts (Task A8)
  "POST /sims/templates": "study_template_add.json", // contract.studies.test.ts (Task A8)
  "POST /sims/{sim_id}/cancel": "study_cancel.json", // contract.studies.test.ts (Task A8)
  // Plan 9 B3: an agent's background jobs (contract.jobs.test.ts)
  "GET /jobs": "p9/streams_jobs.json", "POST /jobs/{job_id}/cancel": "p9/streams_job_cancel.json",
  // Plan 6 S3: core/scripts/dump_s3_fixtures.py; contract.studies.test.ts (S4 fix round 1)
  "GET /sims/calibrations": "s3_cal_list.json",
  "POST /sims/calibrations/fit": "s3_cal_fit.json",
  "POST /sims/calibrations": "s3_cal_save.json",
  "DELETE /sims/calibrations/{cal_id}": "s3_cal_delete.json",
  "GET /sims/calibrations/{cal_id}": "EXEMPT: the app reads the list (GET /sims/calibrations); one record has the list item's shape (s3_cal_list.json)",
  // Plan 7 U2a measure and feedback pins: contract.measure.test.ts
  "POST /parts/{part_id}/measure": "part_measure_edge.json",
  // POST /projects/{project_id}/feedback (U3c's agents_api.py, declared `@r.post`, which this scan
  // does not read) is covered by project_feedback*.json in contract.measure.test.ts
  // Plan 7 U4c agent browser / part cards / parts list / sites: contract.browser.test.ts (U4a),
  // fixtures from core/scripts/dump_browser_bom_fixtures.py
  "GET /runs/{run_id}/browser": "browser/browser_list_full.json",
  "GET /runs/{run_id}/part_cards": "browser/browser_cards.json",
  "GET /browser/snapshots/{sid}": "browser/browser_snapshot.json",
  "GET /browser/snapshots/{sid}/images/{n}": "EXEMPT: re-encoded PNG bytes from the core's image proxy (core test_browser_bom_api covers caps, types and caching); the app only checks the image content type",
  "GET /browser/cards/{card_id}": "browser/browser_card.json",
  "GET /browser/sites/defaults": "browser/sites_defaults.json",
  "POST /browser/sites/restore_defaults": "browser/sites_restore.json",
  "GET /projects/{project_id}/bom": "browser/bom.json",
  "GET /projects/{project_id}/bom.csv": "browser/bom.csv",
  "POST /projects/{project_id}/bom": "browser/bom_add.json",
  "PATCH /projects/{project_id}/bom/{item_id}": "browser/bom_patch.json",
  "GET /projects/{project_id}/skeleton": "project_skeleton_empty.json",
  // Plan 9 D14 generative design: contract.topopt.test.ts, fixtures from
  // core/scripts/dump_topopt_fixtures.py
  "POST /topopt": "topopt_start.json", "GET /topopt": "topopt_list.json",
  "GET /topopt/{topopt_id}": "topopt_record.json", "POST /topopt/{topopt_id}/cancel": "topopt_cancel_conflict.json",
  "POST /topopt/{topopt_id}/accept": "topopt_accept.json",
  "GET /topopt/{topopt_id}/variants/{vid}/{name}": "topopt_render.png",
  "DELETE /projects/{project_id}/bom/{item_id}": "EXEMPT: answers {ok: true} (the generic ok shape); contract.browser.test.ts and the fake-core tests drive it",
};

function coreRoutes(): string[] {
  const out = new Set<string>();
  for (const f of readdirSync(SERVER_SRC).filter((x) => x.endsWith(".py"))) {
    const src = readFileSync(join(SERVER_SRC, f), "utf8");
    for (const m of src.matchAll(/@(?:app|router)\.(get|post|put|patch|delete)\(\s*"([^"]+)"/g)) out.add(`${m[1].toUpperCase()} ${m[2]}`);
    // router.add_api_route("/context", put_context, methods=["PUT", "POST"])
    for (const m of src.matchAll(/add_api_route\(\s*"([^"]+)"[^)]*methods=\[([^\]]+)\]/g)) {
      for (const verb of m[2].matchAll(/"([A-Z]+)"/g)) out.add(`${verb[1]} ${m[1]}`);
    }
  }
  return [...out].sort();
}

describe("contract coverage", () => {
  const routes = coreRoutes();

  it("found the core's routes", () => {
    expect(routes.length).toBeGreaterThan(50);
  });

  it("every core route has a real-core fixture or a written exemption", () => {
    expect(routes.filter((r) => !(r in COVERAGE))).toEqual([]);
  });

  it("every listed fixture exists in the real core's dump", () => {
    // a fixture may sit in a subfolder of the dump (browser/…: dump_browser_bom_fixtures.py)
    const missing = Object.entries(COVERAGE).filter(([, v]) => !v.startsWith("EXEMPT") && !existsSync(join(DIR, v))).map(([k, v]) => `${k}: ${v}`);
    expect(missing).toEqual([]);
  });

  it("no stale entries (a route the core no longer serves)", () => {
    expect(Object.keys(COVERAGE).filter((r) => !routes.includes(r))).toEqual([]);
  });
});
