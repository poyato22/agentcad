// @vitest-environment node
// Contract test, Task A8: the fake core's Plan 5 C7 study routes against the real core's answers
// (core/scripts/dump_study_fixtures.py → core/tests/fixtures/ui/study_*): same shapes for types,
// templates, materials, records, charts, probe, section, slices, compare, cancel and the events
// and tool_result card of a study it runs.
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { WebSocket } from "ws";
import { STUDY_TYPES } from "../src/api/studies";
import type { MisesEvent } from "../src/api/types";
import type { PartSummary } from "../src/api/types";
import { p6Types } from "./fake-core/accuracy";
import { loadScenario, startFakeCore, type FakeCore, type Scenario } from "./fake-core/server";
import { shapeDiff, type Json } from "./shape";

const DIR = resolve(import.meta.dirname, "../../core/tests/fixtures/ui");
const read = (name: string): Json => JSON.parse(readFileSync(join(DIR, name), "utf8")) as Json;

let core: FakeCore;
const scenario = (sims: Record<string, unknown> = {}): Scenario => ({ ...loadScenario("base"), parts: read("study_parts.json") as unknown as PartSummary[], sims: { studies: true, step_ms: 5, ...sims } });

async function call(method: string, path: string, body?: unknown): Promise<{ status: number; json: Json; type: string }> {
  const headers: Record<string, string> = { Authorization: `Bearer ${core.token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(core.url + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const type = r.headers.get("content-type") ?? "";
  return { status: r.status, type, json: type.includes("json") ? (await r.json()) as Json : null };
}
const get = async (p: string) => (await call("GET", p)).json;
const id = (t: string) => (read(`study_${t}.json`) as { sim_id: string }).sim_id;

beforeAll(async () => { core = await startFakeCore({ port: 0, token: "contract-token", scenario: "base" }); });
afterAll(async () => { await core.stop(); });

/** Settings defaults the merged Plan 6 core adds (S1 nonlinear + mesh.refine_points, S2 wind cpus +
 *  openfoam_iterations, S3 fatigue + material anisotropy / printer / calibrated): the C7 fixtures predate them. */
const P6_DEFAULTS = /settings\.(fatigue|nonlinear|cpus|openfoam_iterations|mesh\.refine_points|material\.(anisotropy|printer|calibrated)): extra/;

describe("C7 study routes: fake core = real core", () => {
  it("types, solvers, templates, materials", async () => {
    core.reset(scenario());
    // the schemas are data the form reads: served exactly as the real S3 core wrote them, plus the
    // assumed Plan 6 S1 / S2 settings (fake-core/accuracy.ts; S5 re-dumps those)
    expect(await get("/sims/types")).toEqual(p6Types(read("s3_types.json") as unknown as { id: string; schema: unknown }[]));
    expect(shapeDiff(read("study_solvers.json"), await get("/sims/solvers"), "solvers")).toEqual([]);
    const realT = read("study_templates.json") as { type: string }[];
    for (const t of await get("/sims/templates") as { type: string }[]) expect(shapeDiff(realT.find((x) => x.type === t.type), t, "template")).toEqual([]);
    const add = await call("POST", "/sims/templates", { name: "Tip load 2", type: "static", settings: {} });
    expect(add.status).toBe(201);
    // a template stores every default: the Plan 6 core's settings add these (the template fixture predates Plan 6)
    expect(shapeDiff(read("study_template_add.json"), add.json, "template").filter((x) => !P6_DEFAULTS.test(x))).toEqual([]);
    const dup = await call("POST", "/sims/templates", { name: "tip load 2", type: "static", settings: {} });
    expect(dup.status).toBe(409);
    expect(shapeDiff(read("study_template_conflict.json"), dup.json, "conflict")).toEqual([]);
    const del = await call("DELETE", `/sims/templates/${(add.json as { id: string }).id}`);
    expect(shapeDiff(read("study_template_delete.json"), del.json, "del")).toEqual([]);
    const mat = await call("POST", "/sims/materials", { name: "Spool PETG-CF", e_mpa: 3800, nu: 0.35, yield_mpa: 45, density_g_cm3: 1.3 });
    expect(mat.status).toBe(201);
    expect(shapeDiff(read("study_material_add.json"), mat.json, "material")).toEqual([]);
    // the S3 core's material rows (basis, Z data, S-N keys)
    expect(shapeDiff(read("s3_materials.json"), await get("/sims/materials"), "materials")).toEqual([]);
    expect(shapeDiff(read("study_material_delete.json"), (await call("DELETE", "/sims/materials/Spool%20PETG-CF")).json, "del")).toEqual([]);
  });

  it.each(STUDY_TYPES)("the %s record, list row and charts", async (t) => {
    core.reset(scenario());
    expect(shapeDiff(read(`study_${t}.json`), await get(`/sims/${id(t)}`), t)).toEqual([]);
    expect(await get(`/sims/${id(t)}/charts`)).toEqual(read(`study_${t}_charts.json`));
    const list = await get(`/sims?part_id=${(read(`study_${t}.json`) as { part_id: string }).part_id}`) as Json[];
    const real = (read("study_list.json") as { sim_id: string }[]).find((x) => x.sim_id === id(t)) ?? (read("study_list.json") as Json[])[0];
    expect(shapeDiff(real, list.find((x) => (x as { sim_id: string }).sim_id === id(t)), "list[]")).toEqual([]);
  });

  it("probe, section, slices, compare, cancel when not running", async () => {
    core.reset(scenario());
    expect(shapeDiff(read("study_probe.json"), await get(`/sims/${id("static")}/probe?x=50&y=5&z=5`), "probe")).toEqual([]);
    expect(shapeDiff(read("study_probe_wind.json"), await get(`/sims/${id("wind")}/probe?x=0&y=0&z=40`), "probe")).toEqual([]);
    expect(shapeDiff(read("study_section.json"), await get(`/sims/${id("static")}/section?axis=y&offset=5&channel=von_mises&n=16`), "section")).toEqual([]);
    expect(shapeDiff(read("study_section_wind_velocity.json"), await get(`/sims/${id("wind")}/section?axis=y&offset=0&channel=velocity`), "section")).toEqual([]);
    const png = await call("GET", `/sims/${id("wind")}/slices/slice_xz.png`);
    expect([png.status, png.type]).toEqual([200, "image/png"]);
    const cmp = await call("POST", "/sims/compare", { ids: [id("static"), id("drop")] });
    expect(shapeDiff(read("study_compare.json"), cmp.json, "compare")).toEqual([]);
    const nr = await call("POST", `/sims/${id("static")}/cancel`);
    expect(nr.status).toBe(409);
    expect(shapeDiff(read("study_cancel_not_running.json"), nr.json, "cancel")).toEqual([]);
    const bad = await call("POST", "/sims", { type: "modal", part_id: "beam", settings: { n_modes: 500 } });
    expect(bad.status).toBe(400);
    expect(shapeDiff(read("study_start_invalid.json"), bad.json, "invalid")).toEqual([]);
  });

  it("a study it runs: the same events, tool_result card and record as the real core's", async () => {
    core.reset(scenario());
    const ws = new WebSocket(`${core.url.replace("http", "ws")}/events?token=${core.token}`);
    const got: MisesEvent[] = [];
    ws.on("message", (d) => got.push(JSON.parse(String(d)) as MisesEvent));
    await new Promise((ok, fail) => { ws.on("open", ok); ws.on("error", fail); });
    const started = await call("POST", "/sims", { type: "wind", part_id: "cube", settings: { speed_m_s: 5 }, reason: "drag" });
    expect(started.status).toBe(202);
    expect(shapeDiff(read("study_start.json"), started.json, "start")).toEqual([]);
    const runId = (started.json as { run_id: string }).run_id;
    for (let i = 0; i < 200 && !got.some((e) => e.type === "run_finished"); i++) await new Promise((r) => setTimeout(r, 5));
    ws.close();
    const realEvents = readFileSync(join(DIR, "study_events.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l) as MisesEvent);
    const realProg = realEvents.find((e) => e.type === "sim_progress" && (e.data as { type: string }).type === "wind")!;
    const prog = got.filter((e) => e.type === "sim_progress");
    expect(prog.length).toBeGreaterThan(2);
    for (const p of prog) expect(shapeDiff(realProg.data, p.data, "sim_progress")).toEqual([]);
    expect(got.map((e) => e.type).filter((t) => t !== "sim_progress")).toEqual(["run_started", "tool_call", "tool_result", "run_finished"]);
    const steps = (await get(`/runs/${runId}/steps`)) as { kind: string; payload: { output?: { data?: Json } } }[];
    const realStep = (read("study_agent_steps.json") as { kind: string; payload: { call_id: string; output?: { data?: Json } } }[]).find((s) => s.kind === "sim_run" && s.payload.call_id === "s6")!;
    const res = steps.find((s) => s.kind === "sim_run")!;
    expect(shapeDiff((realStep.payload.output!.data as { card: Json }).card, (res.payload.output!.data as { card: Json }).card, "card")).toEqual([]);
    const list = await get(`/sims?run_id=${runId}`) as { sim_id: string }[];
    expect(shapeDiff(read("study_wind.json"), await get(`/sims/${list[0].sim_id}`), "wind").filter((x) => !P6_DEFAULTS.test(x))).toEqual([]);
  });

  it("cancel by sim_id while it runs ends it cancelled, like the real core", async () => {
    core.reset(scenario({ outcome: "hang" }));
    const started = await call("POST", "/sims", { type: "wind", part_id: "cube", settings: {} });
    const runId = (started.json as { run_id: string }).run_id;
    await new Promise((r) => setTimeout(r, 30));
    const [s] = await get(`/sims?run_id=${runId}`) as { sim_id: string }[];
    const c = await call("POST", `/sims/${s.sim_id}/cancel`);
    expect(shapeDiff(read("study_cancel.json"), c.json, "cancel")).toEqual([]);
    const rec = await get(`/sims/${s.sim_id}`) as { status: string };
    expect(rec.status).toBe("cancelled");
    expect(shapeDiff(read("study_cancelled.json"), rec, "cancelled").filter((x) => !P6_DEFAULTS.test(x))).toEqual([]);
  });
});

// Plan 6 S3: the fake core against the real S3 core (core/scripts/dump_s3_fixtures.py → s3_*)
describe("S3 calibration, anisotropy and fatigue: fake core = real core", () => {
  const until = async (cond: () => Promise<boolean>) => { for (let i = 0; i < 300 && !(await cond()); i++) await new Promise((r) => setTimeout(r, 10)); };
  const runStatic = async (settings: Record<string, unknown>) => {
    const r = await call("POST", "/sims", { type: "static", part_id: "beam", settings });
    const runId = (r.json as { run_id: string }).run_id;
    await until(async () => ((await get(`/sims?run_id=${runId}`)) as { status: string }[])[0]?.status === "done");
    return ((await get(`/sims?run_id=${runId}`)) as { sim_id: string }[])[0].sim_id;
  };
  const CASE = { name: "tip", supports: [{ kind: "fixed", face: "x-min" }], loads: [{ kind: "force", face: "x-max", newtons: 10, direction: "-z" }] };
  const body = (simId: string) => ({
    material: "PLA", printer: "MK4",
    tensile: [1300, 1180, 1240].map((f) => ({ force_n: f, width_mm: 10, thickness_mm: 4 })).concat([{ force_n: 720, width_mm: 10, thickness_mm: 4, orientation: "z" } as never]),
    bend: [2.0, 2.1].map((d) => ({ span_mm: 64, width_mm: 10, thickness_mm: 4, load_n: 40, deflection_mm: d })),
    part: [{ sim_id: simId, point: [100, 5, 10], load_n: 10, deflection_mm: 1.9 }],
  });

  it("fit preview, save (= apply), list, invalid, delete", async () => {
    core.reset(scenario());
    const plain = await runStatic({ load_cases: [CASE], material: { name: "PLA" } });
    const fit = await call("POST", "/sims/calibrations/fit", body(plain));
    expect(fit.status).toBe(200);
    expect(shapeDiff(read("s3_cal_fit.json"), fit.json, "fit")).toEqual([]);
    // the same E, strength and knock-down for the coupons (the part test's E is the fake's estimate)
    const real = (read("s3_cal_fit.json") as { fit: { strength: { value_mpa: number; knockdown: number }; z_strength: { value_mpa: number } } }).fit;
    const mine = (fit.json as { fit: typeof real }).fit;
    expect(mine.strength.value_mpa).toBeCloseTo(real.strength.value_mpa, 6);
    expect(mine.strength.knockdown).toBeCloseTo(real.strength.knockdown, 6);
    expect(mine.z_strength.value_mpa).toBeCloseTo(real.z_strength.value_mpa, 6);
    const bad = await call("POST", "/sims/calibrations/fit", { material: "PLA", printer: "MK4", tensile: [{ force_n: 5 }] });
    expect(bad.status).toBe(422);
    expect(shapeDiff(read("s3_cal_invalid.json"), bad.json, "invalid")).toEqual([]);
    const extra = await call("POST", "/sims/calibrations/fit", { ...body(plain), printer_profile: "MK4" });
    expect(extra.status).toBe(422);
    const saved = await call("POST", "/sims/calibrations", body(plain));
    expect(saved.status).toBe(201);
    expect(shapeDiff(read("s3_cal_save.json"), saved.json, "save")).toEqual([]);
    expect(shapeDiff(read("s3_cal_list.json"), await get("/sims/calibrations"), "list")).toEqual([]);
    const del = await call("DELETE", `/sims/calibrations/${(saved.json as { id: string }).id}`);
    expect(shapeDiff(read("s3_cal_delete.json"), del.json, "delete")).toEqual([]);
  });

  it("an orthotropic study with fatigue, and the same study calibrated on its printer", async () => {
    core.reset(scenario());
    const settings = { load_cases: [CASE], material: { name: "PLA", anisotropy: { print_direction: "+x" } }, fatigue: { cycles: 1e6, cycle: "zero_to_max" } };
    const sid = await runStatic(settings);
    expect(shapeDiff(read("s3_static.json"), await get(`/sims/${sid}`), "s3 static")).toEqual([]);
    const plain = await runStatic({ load_cases: [CASE], material: { name: "PLA" } });
    await call("POST", "/sims/calibrations", body(plain));
    const cal = await runStatic({ ...settings, material: { ...settings.material, printer: "MK4" } });
    const rec = await get(`/sims/${cal}`) as { material: { basis: string }; card: { material_basis: string } };
    expect(shapeDiff(read("s3_static_calibrated.json"), rec, "calibrated")).toEqual([]);
    // the core's label names the calibrated properties (S3 fix round 1)
    const realBasis = (read("s3_static_calibrated.json") as { material: { basis: string } }).material.basis;
    expect(realBasis).toBe("calibrated E (3), strength (3), Z strength (1), low confidence");
    expect(rec.material.basis).toBe(realBasis);
    expect(rec.card.material_basis).toBe(realBasis);
  });

  it("an implausible fit is rejected with reasons, saved (201) but never applied", async () => {
    core.reset(scenario());
    // a decimal-point slip: 10x the strength of every bar
    const typo = { material: "PLA", printer: "MK4", tensile: [13000, 11800, 12400].map((f) => ({ force_n: f, width_mm: 10, thickness_mm: 4 })), bend: [], part: [] };
    const fit = await call("POST", "/sims/calibrations/fit", typo);
    expect(fit.status).toBe(200);
    expect(fit.json).toMatchObject({ status: "rejected", status_label: "rejected — implausible", applicable: false, basis: "rejected — implausible" });
    expect((fit.json as { outliers: unknown[] }).outliers).toHaveLength(3);
    expect((fit.json as { reasons: string[] }).reasons).toContain("every strength test was rejected as an outlier (see outliers)");
    const saved = await call("POST", "/sims/calibrations", typo);
    expect(saved.status).toBe(201);
    expect(saved.json).toMatchObject({ status: "rejected", basis: "rejected — implausible" });
    const sid = await runStatic({ load_cases: [CASE], material: { name: "PLA", printer: "MK4" } });
    const rec = await get(`/sims/${sid}`) as { material: { basis?: string; calibration?: unknown } };
    // (the plain static fixture predates S3's basis key; the real core says "datasheet / estimate")
    expect(rec.material.basis ?? "datasheet / estimate").toBe("datasheet / estimate");
    expect(rec.material.calibration ?? null).toBeNull();
  });
});

// Plan 6 S1 / S2: the fake core against the real merged core (core/scripts/dump_p6_fixtures.py → s1_*, s2_*)
describe("S1 nonlinear / contact and S2 OpenFOAM: fake core = real core", () => {
  const until = async (cond: () => Promise<boolean>) => { for (let i = 0; i < 300 && !(await cond()); i++) await new Promise((r) => setTimeout(r, 10)); };
  const run = async (type: string, part: string, settings: Record<string, unknown>) => {
    const r = await call("POST", "/sims", { type, part_id: part, settings });
    expect(r.status).toBe(202);
    const runId = (r.json as { run_id: string }).run_id;
    await until(async () => ((await get(`/sims?run_id=${runId}`)) as { status: string }[])[0]?.status === "done");
    return ((await get(`/sims?run_id=${runId}`)) as { sim_id: string }[])[0].sim_id;
  };
  const CASE = { name: "tip", supports: [{ kind: "fixed", face: "x-min" }], loads: [{ kind: "force", face: "x-max", newtons: 120, direction: "-z" }] };
  const settingsOf = (n: string) => (read(n) as { settings: Record<string, unknown> }).settings;

  it("the static schema has the real nonlinear settings; wind offers openfoam", async () => {
    core.reset(scenario());
    const types = await get("/sims/types") as { id: string; schema: { properties: Record<string, { enum?: string[] }>; $defs: Record<string, { properties: Record<string, unknown> }> } }[];
    const st = types.find((t) => t.id === "static")!;
    expect(Object.keys(st.schema.$defs.NonlinearSettings.properties)).toEqual(["large_deflection", "plasticity", "tangent_modulus_mpa", "contacts", "rigid_supports", "increments", "max_increments", "min_increment", "penalty"]);
    expect(Object.keys(st.schema.$defs.ContactPair.properties)).toEqual(["kind", "a", "b", "friction", "search_mm"]);
    expect(types.find((t) => t.id === "wind")!.schema.properties.solver.enum).toContain("openfoam");
  });

  it("a large-deflection + plasticity study and a contact study: the real records, cards and probes", async () => {
    core.reset(scenario());
    const nl = await run("static", "beam", settingsOf("s1_static_nonlinear.json"));
    const rec = await get(`/sims/${nl}`) as { card: { fidelity_note: string; metrics: { key: string }[] }; results: { nonlinear: Record<string, unknown> } };
    expect(shapeDiff(read("s1_static_nonlinear.json"), rec, "s1 nonlinear")).toEqual([]);
    const real = read("s1_static_nonlinear.json") as typeof rec;
    expect(rec.card.fidelity_note).toBe(real.card.fidelity_note);
    expect(rec.card.metrics.map((m) => m.key)).toEqual(real.card.metrics.map((m) => m.key));
    expect(rec.results.nonlinear).toMatchObject({ description: "large deflection + plasticity", effects: ["large_deflection", "plasticity"], contact_pairs: 0 });
    expect(shapeDiff(read("s1_probe.json"), await get(`/sims/${nl}/probe?x=2&y=5&z=9.5`), "probe").filter((x) => !/contact_pressure/.test(x))).toEqual([]);
    const ct = await run("static", "beam", settingsOf("s1_static_contact.json"));
    const crec = await get(`/sims/${ct}`) as typeof rec;
    expect(shapeDiff(read("s1_static_contact.json"), crec, "s1 contact")).toEqual([]);
    expect(crec.card.fidelity_note).toBe((read("s1_static_contact.json") as typeof rec).card.fidelity_note);
    const probe = await get(`/sims/${ct}/probe?x=10&y=10&z=0`) as { values: Record<string, unknown>; units: Record<string, string> };
    expect(probe.units.contact_pressure).toBe("MPa");
    // a contact pair between two faces (core ContactPair) is accepted and counted
    const pair = await run("static", "beam", { load_cases: [CASE], nonlinear: { contacts: [{ kind: "contact", a: { face: "top" }, b: { face: "bottom" }, friction: 0.3 }] } });
    const prec = await get(`/sims/${pair}`) as typeof rec;
    expect(prec.results.nonlinear).toMatchObject({ contact_pairs: 1, description: "contact" });
  });

  it("OpenFOAM: the real runtime + image rows per install state, and the real record (validated)", async () => {
    core.reset(scenario());
    const rows = read("s2_openfoam_rows.json") as Record<string, Json[]>;
    const cfd = async () => ((await get("/sims/solvers")) as { cfd: { id: string }[] }).cfd.filter((r) => r.id.startsWith("openfoam"));
    expect(await cfd()).toEqual(rows.image_missing);
    const sid = await run("wind", "cube", { solver: "openfoam" });
    const fallback = await get(`/sims/${sid}`) as { warnings: string[]; fidelity_class: string };
    expect(fallback.fidelity_class).not.toBe("cfd_openfoam");
    expect(fallback.warnings[0]).toBe("OpenFOAM was requested but is unavailable (the OpenFOAM image is not pulled (System check)); the built-in solver was used instead");
    expect((await call("POST", "/system/install/openfoam")).status).toBeLessThan(300);
    await until(async () => JSON.stringify(await cfd()) === JSON.stringify(rows.ok));
    expect(await cfd()).toEqual(rows.ok);
    const of = await run("wind", "cube", settingsOf("s2_wind_openfoam.json"));
    const rec = await get(`/sims/${of}`) as { card: { fidelity_note: string } };
    expect(shapeDiff(read("s2_wind_openfoam.json"), rec, "s2 wind")).toEqual([]);
    expect(rec.card.fidelity_note).toBe("OpenFOAM k-ω SST · ±10% (validated)");
  });
});
