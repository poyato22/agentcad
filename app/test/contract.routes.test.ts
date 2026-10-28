// @vitest-environment node
// Contract test, Task 9: the rest of the core's routes (core/scripts/dump_ui_fixtures.py
// dump_more), and a coverage check: every route the core serves (its FastAPI decorators) has a
// real-core fixture checked by a contract test, or a written reason it has none.
import { readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { z } from "zod";
import {
  agentRunSchema, approvalSchema, cancelAllSchema, coreErrorBodySchema, keyStatusSchema, localActionSchema, okSchema, runIdSchema,
  sketchSummarySchema, stepSchema,
} from "../src/api/schemas";
import type { Approval, Step } from "../src/api/types";
import { loadScenario, startFakeCore, type FakeCore, type Scenario } from "./fake-core/server";
import { shapeDiff, type Json } from "./shape";

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

/** A small real PNG (the core's sketch_upload fixture used a generated 64×48 one). */
function png(): string {
  // 1×1 PNG
  return "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4/58BAAT/Af9jgNErAAAAAElFTkSuQmCC";
}

/** Same body as the dump script's SKETCH_BODY. */
const SKETCH = {
  units: "mm" as const, width_mm: 120, height_mm: 80, notes: "a bracket, 3 mm wall",
  strokes: [
    { id: "s1", kind: "rect" as const, points: [[10, 10], [70, 10], [70, 40], [10, 40]] as [number, number][], closed: true, label: "plate" },
    { id: "s2", kind: "circle" as const, points: [[27.1, 25]] as [number, number][], closed: true, center: [25, 25] as [number, number], radius_mm: 2.1, label: "hole" },
  ],
  dimensions: [
    { stroke_id: "s1", text: "60", value_mm: 60, kind: "length" as const },
    { stroke_id: "s2", text: "⌀4.2", value_mm: 4.2, kind: "diameter" as const },
  ],
};

beforeAll(async () => {
  core = await startFakeCore({ port: 0, token: "contract", scenario: "base" });
  const runId = loadScenario("base").runs[0].id;
  core.reset("base");
  fake.set("health.json", await ok("GET", "/health"));
  fake.set("run.json", await ok("GET", `/runs/${runId}`));
  fake.set("context.json", await ok("GET", "/context"));
  fake.set("key_set.json", await ok("POST", "/keys/openai", { secret: "sk-fixture-0000" }));
  fake.set("key_delete.json", await ok("DELETE", "/keys/openai"));
  fake.set("drawing.dxf", await ok("GET", "/parts/mount_plate/drawing.dxf"));
  const up = (await ok("POST", "/sketches", { sketch: SKETCH, photo: { data_b64: png(), name: "napkin.png" }, render_png_b64: png() })) as { sketch_id: string };
  fake.set("sketch_upload.json", up);
  fake.set("sketch.json", await ok("GET", `/sketches/${up.sketch_id}`));
  const skRun = (await ok("POST", "/runs", { agent: "designer", prompt: "make this bracket", sketch_id: up.sketch_id })) as { run_id: string };
  fake.set("sketch_run.json", skRun);
  const steps = await until(async () => (await ok("GET", `/runs/${skRun.run_id}/steps?after_id=0`)) as Step[], (s) => s.length > 0);
  fake.set("sketch_run_steps.json", steps.slice(0, 1));
  const ext = (await ok("POST", "/runs/external", { agent: "fixture-acp", provider: "acp", model: "fixture" })) as { run_id: string };
  fake.set("run_external.json", ext);
  fake.set("run_cancel.json", await ok("POST", `/runs/${ext.run_id}/cancel`));
  const card = ((await ok("GET", "/approvals")) as Approval[])[0];
  fake.set("approval_decide.json", await ok("POST", `/approvals/${card.id}/decide`, { payload_hash: card.payload_hash, approve: false }));
  fake.set("cancel_all.json", await ok("POST", "/runs/cancel_all"));

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
const anyJson = z.unknown();
const CASES: [string, z.ZodType<unknown>][] = [
  ["health.json", okSchema], ["run.json", agentRunSchema], ["context.json", anyJson],
  ["key_set.json", keyStatusSchema], ["key_delete.json", keyStatusSchema],
  ["sketch_upload.json", sketchSummarySchema], ["sketch.json", sketchSummarySchema], ["sketch_run.json", runIdSchema],
  ["sketch_run_steps.json", z.array(stepSchema)],
  ["local_pull.json", localActionSchema], ["slice_cancel_finished.json", errorAnswer],
  ["run_external.json", runIdSchema], ["run_cancel.json", okSchema], ["approval_decide.json", approvalSchema],
  ["cancel_all.json", cancelAllSchema],
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
  "GET /parts": "parts.json", "GET /parts/{part_id}/model.glb": "mount_plate.glb",
  "GET /runs": "runs.json", "GET /runs/{run_id}": "run.json", "GET /runs/{run_id}/steps": "steps.json",
  "POST /runs": "sketch_run.json", "POST /runs/external": "run_external.json", "POST /runs/{run_id}/cancel": "run_cancel.json",
  "POST /runs/cancel_all": "cancel_all.json",
  "POST /runs/{run_id}/message": "EXEMPT: needs a live turn of a real model; the e2e suite drives it against the fake core",
  "POST /runs/{run_id}/resume": "EXEMPT: the app never calls it (CLI resume of an interrupted run)",
  "GET /approvals": "approvals.json", "POST /approvals/{approval_id}/decide": "approval_decide.json",
  "POST /questions/{question_id}/answer": "EXEMPT: needs a model that asks (ui_ask_user); its answer is {ok:true}, checked by e2e",
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
  "POST /sketches": "sketch_upload.json", "GET /sketches/{sketch_id}": "sketch.json",
  "GET /parts/{part_id}/drawing.json": "drawing.json", "GET /parts/{part_id}/drawing.svg": "drawing.svg",
  "GET /parts/{part_id}/drawing.dxf": "drawing.dxf",
  "GET /print/capabilities": "print_capabilities.json", "GET /printers": "printers.json", "POST /printers": "printer_add.json",
  "DELETE /printers/{printer_id}": "printer_delete.json", "GET /printers/{printer_id}/status": "printer_status.json",
  "POST /slices": "slice_start.json", "GET /slices/{slice_id}": "slice.json",
  "POST /slices/{slice_id}/cancel": "slice_cancel_finished.json",
  "GET /slices/{slice_id}/preview.png": "EXEMPT: the core tests' fake PrusaSlicer embeds no thumbnail; the 404 answer is a core error body",
  "POST /prints": "print_start.json", "GET /prints/{call_id}": "print_done.json",
};

function coreRoutes(): string[] {
  const out = new Set<string>();
  for (const f of readdirSync(SERVER_SRC).filter((x) => x.endsWith(".py"))) {
    const src = readFileSync(join(SERVER_SRC, f), "utf8");
    for (const m of src.matchAll(/@(?:app|router)\.(get|post|put|delete)\(\s*"([^"]+)"/g)) out.add(`${m[1].toUpperCase()} ${m[2]}`);
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
    const have = new Set(readdirSync(DIR));
    const missing = Object.entries(COVERAGE).filter(([, v]) => !v.startsWith("EXEMPT") && !have.has(v)).map(([k, v]) => `${k}: ${v}`);
    expect(missing).toEqual([]);
  });

  it("no stale entries (a route the core no longer serves)", () => {
    expect(Object.keys(COVERAGE).filter((r) => !routes.includes(r))).toEqual([]);
  });
});
