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
import { P6_MATERIAL_KEYS, p6Types } from "./fake-core/accuracy";
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

describe("C7 study routes: fake core = real core", () => {
  it("types, solvers, templates, materials", async () => {
    core.reset(scenario());
    // the schemas are data the form reads: served exactly as the real core wrote them, plus the
    // Plan 6 settings (assumed until S5 re-dumps the fixtures from the S1-S3 core: fake-core/accuracy.ts)
    expect(await get("/sims/types")).toEqual(p6Types(read("study_types.json") as unknown as { id: string; schema: unknown }[]));
    expect(shapeDiff(read("study_solvers.json"), await get("/sims/solvers"), "solvers")).toEqual([]);
    const realT = read("study_templates.json") as { type: string }[];
    for (const t of await get("/sims/templates") as { type: string }[]) expect(shapeDiff(realT.find((x) => x.type === t.type), t, "template")).toEqual([]);
    const add = await call("POST", "/sims/templates", { name: "Tip load 2", type: "static", settings: {} });
    expect(add.status).toBe(201);
    expect(shapeDiff(read("study_template_add.json"), add.json, "template")).toEqual([]);
    const dup = await call("POST", "/sims/templates", { name: "tip load 2", type: "static", settings: {} });
    expect(dup.status).toBe(409);
    expect(shapeDiff(read("study_template_conflict.json"), dup.json, "conflict")).toEqual([]);
    const del = await call("DELETE", `/sims/templates/${(add.json as { id: string }).id}`);
    expect(shapeDiff(read("study_template_delete.json"), del.json, "del")).toEqual([]);
    const mat = await call("POST", "/sims/materials", { name: "Spool PETG-CF", e_mpa: 3800, nu: 0.35, yield_mpa: 45, density_g_cm3: 1.3 });
    expect(mat.status).toBe(201);
    expect(shapeDiff(read("study_material_add.json"), mat.json, "material")).toEqual([]);
    // Plan 6 S3 (assumed): the materials' extra sn / orthotropic / calibration blocks
    const p6Extra = new RegExp(`\\.(${P6_MATERIAL_KEYS.join("|")}): extra`);
    expect(shapeDiff(read("study_materials.json"), await get("/sims/materials"), "materials").filter((x) => !p6Extra.test(x))).toEqual([]);
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
    expect(shapeDiff(read("study_wind.json"), await get(`/sims/${list[0].sim_id}`), "wind")).toEqual([]);
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
    expect(shapeDiff(read("study_cancelled.json"), rec, "cancelled")).toEqual([]);
  });
});
