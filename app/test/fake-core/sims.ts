// Fake core: static stress study routes (core server/sim_api.py + sim/tools.py), with the real
// core's shapes. Records, solver status and the heatmap GLB come from the fixtures the real core
// wrote with CalculiX installed (core/scripts/dump_ui_fixtures.py → core/tests/fixtures/ui); the
// contract test checks this module against them.
//
// POST /sims creates a "studies" run like the core and plays the same events the core sends
// (run_started, tool_call, sim_progress × 5, tool_result, run_finished), one stage every
// `step_ms`. The scenario's `sims.outcome` picks how it ends; cancelling the run cancels the study.
import { randomBytes } from "node:crypto";
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { FACE_ID_RE, SIM_DIRECTIONS, SIM_FACE_PRESETS, type SimEnvironment, type SimMaterial, type SimRecord, type SimSolvers } from "../../src/api/sims";
import type { AgentRun, PartSummary } from "../../src/api/types";
import { asBody, HttpError, mises, RAW, type EmitExtras, type EventIn, type Route } from "./server";
import {
  cancelStudiesOfRun, clearStudyTimers, REAL_STUDY, startStudy, studyGlbPath, studyRoutes, studyStateFrom, STUDY_TYPE_IDS, type StudyState,
} from "./studies";

const HERE = dirname(fileURLToPath(import.meta.url));
export const SIM_FIXTURES = resolve(HERE, "../../../core/tests/fixtures/ui");
export const SIM_GLB = resolve(SIM_FIXTURES, "sim_result.glb");

const fixture = <T>(name: string): T => JSON.parse(readFileSync(resolve(SIM_FIXTURES, name), "utf8")) as T;

/** The real core's answers (fixtures). */
export const REAL = {
  done: fixture<SimRecord>("sim_done.json"),
  failed: fixture<SimRecord>("sim_failed.json"),
  solvers: fixture<SimSolvers>("sims_solvers.json"),
  solversMissing: fixture<SimSolvers>("sims_solvers_missing.json"),
  env: fixture<{ materials: SimMaterial[]; environments: SimEnvironment[] }>("sim_env.json"),
};

const SIM_ID_RE = /^sim_[0-9a-f]{12}$/;
const PART_ID_RE = /^[a-z][a-z0-9_]{1,40}$/;

export type SimOutcome = "done" | "solver_missing" | "solve_failed" | "hang";

export interface SimScenario {
  /** Is CalculiX installed? Default true. */
  ccx?: boolean;
  /** Stored studies. "fixtures": the real core's done + failed study of mount_plate. */
  records?: SimRecord[] | "fixtures";
  /** How a study started with POST /sims ends. Default "done" (or "solver_missing" without ccx). */
  outcome?: SimOutcome;
  /** Delay between stages, ms. Default 150. */
  step_ms?: number;
  /** Plan 5 C7: also store the real core's study of every type (beam / cube parts) and its templates. */
  studies?: boolean;
}

export interface FakeSims {
  ccx: boolean;
  records: Map<string, SimRecord>;
  outcome: SimOutcome;
  stepMs: number;
  /** Pending stage timers per studies run (cleared on cancel / reset). */
  timers: Map<string, ReturnType<typeof setTimeout>[]>;
  /** Templates, custom materials and running C7 studies. */
  study: StudyState;
  /** The scenario stores the C7 studies (sims.studies). */
  studiesOn: boolean;
}

export function simsFrom(s: SimScenario | undefined): FakeSims {
  const ccx = s?.ccx ?? true;
  const list = s?.records === "fixtures" ? [REAL.done, REAL.failed] : (s?.records ?? []);
  if (s?.studies) {
    list.push(...STUDY_TYPE_IDS.map((t) => REAL_STUDY.records[t] as unknown as SimRecord));
    list.push(...REAL_STUDY.reviewRecords.map((r) => r as unknown as SimRecord));
  }
  return {
    ccx,
    records: new Map(list.map((r) => [r.sim_id, structuredClone(r)])),
    outcome: s?.outcome ?? (ccx ? "done" : "solver_missing"),
    stepMs: s?.step_ms ?? 150,
    timers: new Map(),
    study: studyStateFrom(s?.studies === true),
    studiesOn: s?.studies === true,
  };
}

export function clearSimTimers(sims: FakeSims): void {
  for (const ts of sims.timers.values()) ts.forEach(clearTimeout);
  sims.timers.clear();
  clearStudyTimers(sims.study);
}

/** Same as the core: running → cancelled when its run is cancelled (the solver is killed). */
export function cancelSimsOfRun(sims: FakeSims, runId: string): void {
  sims.timers.get(runId)?.forEach(clearTimeout);
  sims.timers.delete(runId);
  cancelStudiesOfRun(sims.study, runId);
  for (const r of sims.records.values()) {
    if (r.run_id === runId && r.status === "running") {
      Object.assign(r, { status: "cancelled", stage: "cancelled", error: "cancelled", finished_at: Date.now() / 1000 });
    }
  }
}

export interface SimDeps {
  sims(): FakeSims;
  parts(): readonly PartSummary[];
  defaultMaterial(): string;
  newRun(agent: string, provider: string, model: string, task: string, external: boolean): AgentRun;
  emit(ev: EventIn, extras?: EmitExtras): unknown;
}

const now = () => Date.now() / 1000;
const hex = (n: number) => randomBytes(n).toString("hex");

function summary(r: SimRecord) {
  const extra = r as unknown as Record<string, unknown>;
  // C7 records also carry these (core studies.SUMMARY_KEYS)
  const c7 = Object.fromEntries(["type", "phase", "pct", "step_id", "reason"].filter((k) => k in extra).map((k) => [k, extra[k]]));
  return {
    ...c7,
    sim_id: r.sim_id, part_id: r.part_id, run_id: r.run_id, status: r.status, stage: r.stage, created_at: r.created_at,
    finished_at: r.finished_at, error: r.error, hint: r.hint, inputs: r.inputs, results: r.results ?? null, guide: r.guide,
    label: r.label, fidelity_class: r.fidelity_class, stale: r.stale,
  };
}

/** pydantic 422 for StaticArgs. */
function invalid(loc: string[], msg: string, input: unknown): HttpError {
  return new HttpError(422, { detail: [{ type: "value_error", loc: ["body", ...loc], msg, input }] });
}

function face(v: unknown, loc: string[]): string {
  if (v === undefined) throw new HttpError(422, { detail: [{ type: "missing", loc: ["body", ...loc], msg: "Field required", input: null }] });
  if (typeof v !== "string" || !((SIM_FACE_PRESETS as readonly string[]).includes(v) || FACE_ID_RE.test(v))) {
    throw invalid(loc, `Value error, face must be one of ${JSON.stringify(SIM_FACE_PRESETS)} or a face id like 'f3'`, v);
  }
  return v;
}

export function simRoutes(d: SimDeps): Route[] {
  const record = (id: string): SimRecord => {
    if (!SIM_ID_RE.test(id)) throw mises("invalid_input", `bad sim id: '${id}'`, `sim ids match ${SIM_ID_RE.source}`);
    const r = d.sims().records.get(id);
    if (!r) throw mises("not_found", `no study ${id}`);
    return r;
  };
  /** Like the core's geometry-hash check: stale once the part was rebuilt after the study (or is gone). */
  const withStale = (r: SimRecord): SimRecord => {
    const p = d.parts().find((x) => x.part_id === r.part_id);
    const stale = r.stale || !p || p.mtime > (r.finished_at ?? r.created_at);
    return { ...structuredClone(r), stale };
  };
  const sorted = () => [...d.sims().records.values()].sort((a, b) => b.created_at - a.created_at).map(withStale);

  const studyDeps = {
    records: () => d.sims().records as unknown as Map<string, import("../../src/api/studies").StudyRecord>,
    state: () => d.sims().study, parts: d.parts, stepMs: () => d.sims().stepMs, hang: () => d.sims().outcome === "hang",
    newRun: d.newRun, emit: d.emit,
  };
  return [
    ...studyRoutes(studyDeps),
    { method: "GET", re: /^\/sims\/solvers$/, h: () => {
      // the C7 core adds the CFD rows; the older sims fixtures predate them
      const base = structuredClone(d.sims().ccx ? REAL.solvers : REAL.solversMissing);
      return d.sims().studiesOn ? { ...base, cfd: structuredClone(REAL_STUDY.solvers.cfd) } : base;
    } },
    {
      method: "GET", re: /^\/sims$/, h: (_m, q) => {
        const part = q.get("part_id");
        const run = q.get("run_id");
        if (part !== null && !PART_ID_RE.test(part)) throw mises("invalid_input", `bad part id: '${part}'`, `part ids match ${PART_ID_RE.source}`);
        return sorted().filter((r) => (part === null || r.part_id === part) && (run === null || r.run_id === run)).map(summary);
      },
    },
    { method: "GET", re: /^\/sims\/([^/]+)$/, h: (m) => withStale(record(decodeURIComponent(m[1]))) },
    {
      method: "GET", re: /^\/sims\/([^/]+)\/result\.glb$/, h: (m, _q, _b, r, res) => {
        const rec = record(decodeURIComponent(m[1]));
        const glbPath = studyGlbPath((rec as unknown as { type?: string }).type && (rec as unknown as { card?: unknown }).card ? (rec as unknown as { type: string }).type : null) ?? SIM_GLB;
        if (rec.status !== "done" || !existsSync(glbPath)) throw mises("not_found", `study ${rec.sim_id} has no result model`, "only finished studies have one");
        const size = statSync(glbPath).size;
        const etag = `"${Math.round((rec.finished_at ?? 0) * 1e9)}-${size}"`;
        if (r.headers["if-none-match"] === etag) {
          res.writeHead(304, { ETag: etag });
          res.end();
          return RAW;
        }
        res.writeHead(200, { "Content-Type": "model/gltf-binary", "Content-Length": size, ETag: etag });
        res.end(readFileSync(glbPath));
        return RAW;
      },
    },
    {
      method: "POST", re: /^\/sims$/, h: (_m, _q, raw, _r, res) => {
        const b = asBody(raw);
        const text = JSON.stringify("type" in b ? startStudy(studyDeps, b) : start(d, b));
        res.writeHead(202, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(text) });
        res.end(text);
        return RAW;
      },
    },
  ];
}

function start(d: SimDeps, b: Record<string, unknown>): unknown {
  if (typeof b.part_id !== "string") throw new HttpError(422, { detail: [{ type: "missing", loc: ["body", "part_id"], msg: "Field required", input: b }] });
  const fix = face(b.fix, ["fix"]);
  const loadIn = b.load;
  if (typeof loadIn !== "object" || loadIn === null) throw new HttpError(422, { detail: [{ type: "missing", loc: ["body", "load"], msg: "Field required", input: b }] });
  const l = loadIn as Record<string, unknown>;
  const loadFace = face(l.face, ["load", "face"]);
  if (typeof l.newtons !== "number" || !(l.newtons > 0) || l.newtons > 1e6) throw invalid(["load", "newtons"], "Input should be greater than 0", l.newtons);
  if (!(SIM_DIRECTIONS as readonly unknown[]).includes(l.direction)) throw invalid(["load", "direction"], `Input should be ${SIM_DIRECTIONS.join(", ")}`, l.direction);
  const direction = l.direction as (typeof SIM_DIRECTIONS)[number];
  const partId = b.part_id;
  if (!PART_ID_RE.test(partId)) throw mises("invalid_input", `bad part id: '${partId}'`, `part ids match ${PART_ID_RE.source}`);
  if (!d.parts().some((p) => p.part_id === partId && p.metrics !== null)) throw mises("not_found", `no built part '${partId}'`);

  const run = d.newRun("studies", "ui", "studies-page", `Studies page: static study of ${partId}`, true);
  const input = { part_id: partId, fix, load: { face: loadFace, newtons: l.newtons, direction }, material: b.material ?? null, environment: b.environment ?? null, fine: b.fine ?? true };
  const callId = `ui-${hex(6)}`;
  d.emit({ type: "run_started", run_id: run.id, data: { agent: "studies", provider: "ui", model: "studies-page", parent_id: null, external: true } });
  const emitted = d.emit({ type: "tool_call", run_id: run.id, data: { call_id: callId, name: "sim_static_run", input } },
    { steps: [{ kind: "tool_call", payload: { call_id: callId, name: "sim_static_run", input } }] }) as { step_ids?: number[] } | undefined;
  const stepId = emitted?.step_ids?.[0] ?? null;

  const sims = d.sims();
  const timers: ReturnType<typeof setTimeout>[] = [];
  sims.timers.set(run.id, timers);
  const at = (i: number, fn: () => void) => timers.push(setTimeout(fn, sims.stepMs * i));
  const finish = (status: "done" | "failed", ok: boolean, summaryText: string, data: Record<string, unknown>) => {
    d.emit({ type: "tool_result", run_id: run.id, data: { call_id: callId, name: "sim_static_run", ok, summary: summaryText } },
      { steps: [{ kind: ok ? "sim_run" : "error", payload: ok
        ? { call_id: callId, name: "sim_static_run", output: { ok, summary: summaryText, data, handles: [], warnings: [], step_kind: "sim_run" } }
        : { call_id: callId, name: "sim_static_run", error: String(data.error ?? "tool_failed"), message: summaryText, hint: data.hint ?? null, retryable: false } }] });
    d.emit({ type: "run_finished", run_id: run.id, data: { status, stop_reason: status, error: null, final_text: "", external: true } });
    sims.timers.delete(run.id);
  };

  // Bad setup the core only finds inside the tool (the run fails, no study is stored).
  const matName = typeof b.material === "string" && b.material ? b.material : d.defaultMaterial();
  const material = REAL.env.materials.find((m) => m.name.toLowerCase() === matName.trim().toLowerCase());
  const env = typeof b.environment === "string" && b.environment
    ? REAL.env.environments.find((e) => e.name.toLowerCase() === (b.environment as string).trim().toLowerCase()) : null;
  let bad: [string, string] | null = null;
  if (fix === loadFace) bad = ["fix and load are the same face", "hold one face and load another"];
  else if (!material) bad = [`unknown material '${matName}'`, `use one of: ${REAL.env.materials.map((m) => m.name).join(", ")}`];
  else if (env === undefined) bad = [`unknown environment '${String(b.environment)}'`, `use one of: ${REAL.env.environments.map((e) => e.name).join(", ")}`];
  if (bad || !material) {
    const [message, hint] = bad ?? ["bad setup", ""];
    at(1, () => {
      d.emit({ type: "tool_result", run_id: run.id, data: { call_id: callId, name: "sim_static_run", ok: false, error: "invalid_input", message, hint, retryable: false } },
        { steps: [{ kind: "error", payload: { call_id: callId, name: "sim_static_run", error: "invalid_input", message, hint, retryable: false } }] });
      d.emit({ type: "run_finished", run_id: run.id, data: { status: "failed", stop_reason: "failed", error: null, final_text: "", external: true } });
      sims.timers.delete(run.id);
    });
    return { run_id: run.id, status: "running" };
  }

  const simId = `sim_${hex(6)}`;
  // the real core runs the legacy body as a C7 static study (one load case): its record carries
  // the study fields (type, call_id, step_id, phase, pct, settings, urls, card) too
  const realDone = REAL.done as unknown as Record<string, unknown>;
  const realSettings = realDone.settings as Record<string, unknown>;
  const settings = {
    material: { name: material.name, custom: null, fdm: null }, mesh: structuredClone(realSettings.mesh), solver: structuredClone(realSettings.solver),
    load_cases: [{ name: "case 1", supports: [{ kind: "fixed", face: fix, axis: null }], loads: [{ kind: "force", face: loadFace, newtons: l.newtons, direction }], note: "" }],
    convergence: structuredClone(realSettings.convergence), environment: env ? env.name : null,
  };
  const urls = { record: `/sims/${simId}`, glb: `/sims/${simId}/result.glb`, charts: `/sims/${simId}/charts`, probe: `/sims/${simId}/probe`, section: `/sims/${simId}/section` };
  const cardOf = (real: SimRecord, over: Record<string, unknown>) => ({
    ...structuredClone((real as unknown as { card: Record<string, unknown> }).card), sim_id: simId, part_id: partId, run_id: run.id, step_id: stepId, urls, charts_url: urls.charts, ...over,
  });
  const rec: SimRecord = {
    sim_id: simId, part_id: partId, run_id: run.id, status: "running", stage: "queued", created_at: now(), finished_at: null,
    analysis: "static_linear_elastic", guide: true, label: REAL.done.label, fidelity_class: "fem", validated_against: null,
    inputs: { type: "static", settings, fix, load: { face: loadFace, newtons: l.newtons, direction }, material: material.name, environment: env ? env.name : null } as SimRecord["inputs"],
    geometry_hash: REAL.done.geometry_hash, material: structuredClone(material), environment: env ? structuredClone(env) : null,
    error: null, hint: null, warnings: [], caveats: [...REAL.done.caveats], stale: false,
  };
  Object.assign(rec, {
    type: "static", call_id: callId, step_id: stepId, reason: null, phase: "queued", pct: 0, settings, urls,
    card: cardOf(REAL.failed, { status: "running", verdict: null, phase: "queued", pct: 0, metrics: [], checks: [], warnings: [], notes: [] }),
  });
  sims.records.set(simId, rec);
  const progress = (stage: SimRecord["stage"], phase: string, pct: number, detail: string, data: Record<string, unknown> = {}) => {
    Object.assign(rec, { stage, phase, pct });
    d.emit({ type: "sim_progress", run_id: run.id, data: { sim_id: simId, part_id: partId, type: "static", call_id: callId, step_id: stepId, stage, phase, pct, detail, ...data } });
  };
  const mesh = REAL.done.mesh;
  if (sims.outcome === "solver_missing") {
    at(1, () => {
      progress("failed", "failed", 0, REAL.failed.error ?? "CalculiX (ccx) is not installed");
      Object.assign(rec, { status: "failed", stage: "failed", error: REAL.failed.error, hint: REAL.failed.hint, finished_at: now(), card: cardOf(REAL.failed, {}) });
      finish("failed", false, `study failed: ${REAL.failed.error} (${REAL.failed.hint})`,
        { sim_id: simId, error: "solver_missing", missing: ["ccx"], install: REAL.solvers.ccx.install, hint: REAL.failed.hint });
    });
    return { run_id: run.id, status: "running" };
  }
  at(1, () => progress("mesh_coarse", "mesh", 0, `meshing coarse (${mesh?.coarse.size_mm} mm)`, { size_mm: mesh?.coarse.size_mm }));
  at(2, () => progress("solve_coarse", "solve", 22.5, "solving coarse", { nodes: mesh?.coarse.nodes, elements: mesh?.coarse.elements, case: "case 1" }));
  at(3, () => progress("mesh_fine", "mesh", 45, `meshing fine (${mesh?.fine.size_mm} mm)`, { size_mm: mesh?.fine.size_mm }));
  at(4, () => progress("solve_fine", "solve", 67.5, "solving fine", { nodes: mesh?.fine.nodes, elements: mesh?.fine.elements, case: "case 1" }));
  if (sims.outcome === "hang") return { run_id: run.id, status: "running" };
  if (sims.outcome === "solve_failed") {
    at(5, () => {
      progress("failed", "failed", 67.5, "CalculiX failed: see the solver log");
      Object.assign(rec, { status: "failed", stage: "failed", error: "CalculiX failed: see the solver log", hint: "check the setup (is the part held?)", finished_at: now(), card: cardOf(REAL.failed, {}) });
      finish("failed", false, `study failed: ${rec.error} (${rec.hint})`, { sim_id: simId, error: "solve_failed", hint: rec.hint });
    });
    return { run_id: run.id, status: "running" };
  }
  at(5, () => progress("post", "post", 92, "post-processing"));
  at(6, () => {
    // the real core's numbers for the same part, scaled to this material's allowable
    const res = structuredClone(REAL.done.results);
    if (res) {
      res.allowable_mpa = material.allowable_mpa;
      res.safety_factor = material.allowable_mpa / res.max_von_mises_mpa;
      const worst = Math.max(res.max_von_mises_mpa, res.max_von_mises_at_fixture_mpa ?? 0);
      res.safety_factor_incl_fixture = material.allowable_mpa / worst;
    }
    progress("done", "done", 100, "done");
    Object.assign(rec, {
      status: "done", stage: "done", finished_at: now(), solver: structuredClone(REAL.done.solver), mesh: structuredClone(REAL.done.mesh),
      results: res, warnings: [], inputs_hash: hex(32), glb: "result.glb", color_range_mpa: [0, res?.max_von_mises_mpa ?? 0],
      outputs: structuredClone(realDone.outputs), card: cardOf(REAL.done, {}),
    });
    finish("done", true, `GUIDE ONLY - CalculiX: ${partId} max von Mises ${res?.max_von_mises_mpa.toPrecision(3)} MPa`, rec as unknown as Record<string, unknown>);
  });
  return { run_id: run.id, status: "running" };
}
