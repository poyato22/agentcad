// Fake core: the Plan 5 C7 study routes (core server/sim_api.py, sim/studies.py), with the real
// core's shapes from core/scripts/dump_study_fixtures.py (study_*.json / .glb / .png):
// GET /sims/types, templates and custom materials (CRUD, kept in the fake state), charts, probe,
// section, velocity slices, compare, POST /sims/{id}/cancel, and POST /sims {type, part_id,
// settings}, which plays what the core sends: run_started, tool_call sim_run, sim_progress
// {sim_id, phase, pct, detail} (the real events of that study type), tool_result with the compact
// card, run_finished. The stored record is the real record of that type under a new id.
import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { StudyMaterial, StudyRecord, StudyTemplate, StudyType } from "../../src/api/studies";
import type { AgentRun, MisesEvent, PartSummary } from "../../src/api/types";
import { defaultFor, deref, discriminated, nullableInner, properties, withDefaults } from "../../src/ui/pages/studies/formSchema";
import { calibrationRoutes, calStateFrom, p6Base, p6Finish, p6Probe, p6Types, REAL_S3, type CalState, type OpenFoamState } from "./accuracy";
import { asBody, HttpError, mises, RAW, type EmitExtras, type EventIn, type Route } from "./server";

const DIR = resolve(import.meta.dirname, "../../../core/tests/fixtures/ui");
const json = <T>(n: string): T => JSON.parse(readFileSync(resolve(DIR, n), "utf8")) as T;
export const STUDY_TYPE_IDS: readonly StudyType[] = ["static", "modal", "buckling", "thermal", "drop", "wind"];

/** The real core's answers. */
export const REAL_STUDY = {
  types: json<{ id: StudyType; schema: unknown }[]>("study_types.json"),
  solvers: json<Record<string, unknown>>("study_solvers.json"),
  templates: json<StudyTemplate[]>("study_templates.json"),
  materials: json<{ builtin: StudyMaterial[]; custom: StudyMaterial[] }>("study_materials.json"),
  compare: json<{ columns: Record<string, unknown>[]; rows: { key: string; values: unknown[] }[]; label: string }>("study_compare.json"),
  probe: json<Record<string, unknown>>("study_probe.json"),
  probeWind: json<Record<string, unknown>>("study_probe_wind.json"),
  section: json<Record<string, unknown>>("study_section.json"),
  sectionWind: json<Record<string, unknown>>("study_section_wind.json"),
  sectionVelocity: json<Record<string, unknown>>("study_section_wind_velocity.json"),
  parts: json<PartSummary[]>("study_parts.json"),
  /** The design review's own studies (its table rows link to them). */
  reviewRecords: json<StudyRecord[]>("study_review_records.json"),
  records: Object.fromEntries(STUDY_TYPE_IDS.map((t) => [t, json<StudyRecord>(`study_${t}.json`)])) as Record<StudyType, StudyRecord>,
  charts: Object.fromEntries(STUDY_TYPE_IDS.map((t) => [t, json<Record<string, unknown>>(`study_${t}_charts.json`)])) as Record<StudyType, Record<string, unknown>>,
  events: readFileSync(resolve(DIR, "study_events.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l) as MisesEvent),
  steps: json<{ kind: string; payload: Record<string, unknown> }[]>("study_agent_steps.json"),
};
/** Result GLBs the core dumped (the other types fall back to the static one). */
const GLB: Partial<Record<StudyType, string>> = {
  static: "study_static.glb", modal: "study_modal.glb", thermal: "study_thermal.glb", wind: "study_wind.glb",
  drop: "study_static.glb", buckling: "study_modal.glb",
};
export const studyGlbPath = (type: string | null | undefined): string | null => (type && type in GLB ? resolve(DIR, GLB[type as StudyType]!) : null);
export const studyPartGlb = (partId: string): string => resolve(DIR, `study_part_${partId}.glb`);

export interface StudyState {
  templates: StudyTemplate[];
  custom: StudyMaterial[];
  /** Plan 6 S3 (assumed): material calibrations. */
  cal: CalState;
  /** sim_id → the run's pending timers (cancel clears them). */
  running: Map<string, { runId: string; timers: ReturnType<typeof setTimeout>[]; finish: (status: "cancelled") => void }>;
}

export function studyStateFrom(on: boolean): StudyState {
  return { templates: on ? structuredClone(REAL_STUDY.templates) : [], custom: [], running: new Map(), cal: calStateFrom() };
}

export interface StudyDeps {
  records(): Map<string, StudyRecord>;
  state(): StudyState;
  parts(): readonly PartSummary[];
  stepMs(): number;
  /** The scenario's sims.outcome is "hang": a study never finishes (cancel tests). */
  hang(): boolean;
  newRun(agent: string, provider: string, model: string, task: string, external: boolean): AgentRun;
  emit(ev: EventIn, extras?: EmitExtras): unknown;
  /** Plan 6 S2: the OpenFOAM install state (a wind study on "openfoam" falls back when not "ok"). */
  openfoam?(): OpenFoamState;
}

const SIM_ID_RE = /^sim_[0-9a-f]{12}$/;
const hex = (n: number) => randomBytes(n).toString("hex");
const now = () => Date.now() / 1000;
/** Like the core's pydantic models: stored settings carry every default. */
const fullSettings = (type: StudyType, s: unknown): Record<string, unknown> => {
  const t = REAL_S3.types.find((x) => x.id === type);
  if (t) {
    // pydantic fills defaults at every depth (list items, objects the user gave in part)
    const root = t.schema as Record<string, unknown>;
    return fill(withDefaults(root, (typeof s === "object" && s !== null ? s : {}) as Record<string, unknown>), root, root) as Record<string, unknown>;
  }
  return (typeof s === "object" && s !== null ? s : {}) as Record<string, unknown>;
};

/** Every default the schema has, filled in below what `v` already gives (a given value wins). */
function fill(v: unknown, node: Record<string, unknown>, root: Record<string, unknown>): unknown {
  const n = deref(node, root);
  if (v === null || v === undefined) return v;
  const inner = nullableInner(n, root);
  if (inner) return fill(v, inner, root);
  const d = discriminated(n, root);
  if (d && typeof v === "object" && !Array.isArray(v)) {
    const opt = d.options.find((o) => o.value === (v as Record<string, unknown>)[d.prop]) ?? d.options[0];
    return opt ? fill(v, opt.schema, root) : v;
  }
  if (Array.isArray(v)) return Array.isArray(n.prefixItems) ? v : v.map((x) => fill(x, (n.items ?? {}) as Record<string, unknown>, root));
  if (typeof v === "object" && n.type === "object") {
    const o = { ...(v as Record<string, unknown>) };
    for (const p of properties(n)) {
      if (!(p.key in o)) { const dv = defaultFor(p.schema, root, p.key); if (dv !== undefined && "default" in deref(p.schema, root)) o[p.key] = dv; }
      o[p.key] = fill(o[p.key], p.schema, root);
    }
    return o;
  }
  return v;
}
const isType = (v: unknown): v is StudyType => typeof v === "string" && (STUDY_TYPE_IDS as readonly string[]).includes(v);

function invalid(message: string, errors: unknown[] = []): HttpError {
  return new HttpError(400, { error: "invalid_input", message, errors });
}

export function studyRoutes(d: StudyDeps): Route[] {
  const rec = (id: string): StudyRecord => {
    if (!SIM_ID_RE.test(id)) throw mises("invalid_input", `bad sim id: '${id}'`);
    const r = d.records().get(id);
    if (!r) throw mises("not_found", `no study ${id}`);
    return r;
  };
  const typeOf = (r: StudyRecord): StudyType => (isType(r.type) ? r.type : "static");
  const png = (name: string) => readFileSync(resolve(DIR, `study_${name}.png`));
  // Plan 6 (accuracy.ts): S3 real shapes; the calibration routes look materials up by name
  const material = (name: string) => [...REAL_S3.materials.builtin, ...d.state().custom].find((x) => x.name.toLowerCase() === name.toLowerCase());
  return [
    ...calibrationRoutes({ cal: () => d.state().cal, material, record: (id) => d.records().get(id) }),
    { method: "GET", re: /^\/sims\/types$/, h: () => p6Types(REAL_S3.types) },
    { method: "GET", re: /^\/sims\/templates$/, h: () => structuredClone(d.state().templates) },
    {
      method: "POST", re: /^\/sims\/templates$/, h: (_m, _q, raw, _r, res) => {
        const b = asBody(raw);
        const name = typeof b.name === "string" ? b.name.trim() : "";
        if (!name || name.length > 60) throw invalid("a template needs a name of 1-60 characters");
        if (!isType(b.type)) throw invalid(`unknown study type '${String(b.type)}'`);
        const st = d.state();
        if (st.templates.some((t) => t.name.toLowerCase() === name.toLowerCase())) {
          throw new HttpError(409, { error: "conflict", message: `a template named '${name}' already exists`, hint: "pick another name or delete the old one" });
        }
        const t: StudyTemplate = { id: `tpl_${hex(5)}`, name, type: b.type, settings: fullSettings(b.type, b.settings), created_at: now() };
        st.templates.push(t);
        const text = JSON.stringify(t);
        res.writeHead(201, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(text) });
        res.end(text);
        return RAW;
      },
    },
    {
      method: "DELETE", re: /^\/sims\/templates\/([^/]+)$/, h: (m) => {
        const st = d.state();
        const id = decodeURIComponent(m[1]);
        if (!st.templates.some((t) => t.id === id)) throw mises("not_found", `no template ${id}`);
        st.templates = st.templates.filter((t) => t.id !== id);
        return { deleted: id };
      },
    },
    {
      method: "GET", re: /^\/sims\/materials$/, h: () => ({
        // like the S3 core: the raw materials (a calibration applies inside a study, per printer)
        builtin: structuredClone(REAL_S3.materials.builtin),
        custom: structuredClone(d.state().custom),
      }),
    },
    {
      method: "POST", re: /^\/sims\/materials$/, h: (_m, _q, raw, _r, res) => {
        const b = asBody(raw);
        const name = typeof b.name === "string" ? b.name.trim() : "";
        if (!name) throw new HttpError(422, { detail: [{ type: "missing", loc: ["body", "name"], msg: "Field required", input: b }] });
        if (REAL_STUDY.materials.builtin.some((x) => x.name.toLowerCase() === name.toLowerCase())) throw invalid(`'${name}' is a built-in material`);
        const m = { ...REAL_STUDY.materials.custom[0] ?? REAL_STUDY.materials.builtin[0], ...b, name, custom: true } as StudyMaterial;
        const st = d.state();
        st.custom = [...st.custom.filter((x) => x.name.toLowerCase() !== name.toLowerCase()), m];
        const text = JSON.stringify(m);
        res.writeHead(201, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(text) });
        res.end(text);
        return RAW;
      },
    },
    {
      method: "DELETE", re: /^\/sims\/materials\/([^/]+)$/, h: (m) => {
        const st = d.state();
        const name = decodeURIComponent(m[1]);
        if (!st.custom.some((x) => x.name === name)) throw mises("not_found", `no custom material '${name}'`);
        st.custom = st.custom.filter((x) => x.name !== name);
        return { deleted: name };
      },
    },
    {
      method: "POST", re: /^\/sims\/compare$/, h: (_m, _q, raw) => {
        const ids = asBody(raw).ids;
        if (!Array.isArray(ids) || ids.length < 2 || ids.length > 8) throw new HttpError(422, { detail: [{ type: "too_short", loc: ["body", "ids"], msg: "List should have at least 2 items", input: ids }] });
        const recs = ids.map((id) => rec(String(id)));
        const real = REAL_STUDY.compare;
        // the real table's rows, one column per asked study, values by key from each record
        return {
          label: real.label,
          columns: recs.map((r) => ({ sim_id: r.sim_id, type: typeOf(r), part_id: r.part_id, status: r.status, stale: r.stale, fidelity_class: r.fidelity_class, material: r.material?.name ?? null, created_at: r.created_at })),
          rows: real.rows.map((row) => {
            const values = recs.map((r) => { const v = (r.results ?? {})[row.key]; return typeof v === "number" ? v : null; });
            const nums = values.map((v, i) => [v, i] as const).filter((x): x is readonly [number, number] => x[0] !== null);
            const better = row.key === "safety_factor" ? "max" : "min";
            const best = nums.length > 1 ? nums.reduce((a, b) => ((better === "max" ? b[0] > a[0] : b[0] < a[0]) ? b : a))[1] : null;
            return { ...row, values, best };
          }),
        };
      },
    },
    { method: "GET", re: /^\/sims\/([^/]+)\/charts$/, h: (m) => { const r = rec(decodeURIComponent(m[1])); return { ...structuredClone(REAL_STUDY.charts[typeOf(r)]), sim_id: r.sim_id, status: r.status }; } },
    {
      method: "GET", re: /^\/sims\/([^/]+)\/probe$/, h: (m, q) => {
        const r = rec(decodeURIComponent(m[1]));
        const p = ["x", "y", "z"].map((k) => Number(q.get(k)));
        if (p.some((x) => !Number.isFinite(x))) throw new HttpError(422, { detail: [{ type: "float_parsing", loc: ["query", "x"], msg: "Input should be a valid number", input: q.get("x") }] });
        return p6Probe(r, { ...structuredClone(typeOf(r) === "wind" ? REAL_STUDY.probeWind : REAL_STUDY.probe), sim_id: r.sim_id, point: p });
      },
    },
    {
      method: "GET", re: /^\/sims\/([^/]+)\/section$/, h: (m, q) => {
        const r = rec(decodeURIComponent(m[1]));
        const ch = q.get("channel") ?? "";
        if (typeOf(r) === "wind") return structuredClone(ch === "velocity" ? REAL_STUDY.sectionVelocity : REAL_STUDY.sectionWind);
        return { ...structuredClone(REAL_STUDY.section), axis: q.get("axis") ?? "y", offset: Number(q.get("offset") ?? 0), channel: ch };
      },
    },
    {
      method: "GET", re: /^\/sims\/([^/]+)\/slices\/(slice_xz|slice_xy)\.png$/, h: (m, _q, _b, _r, res) => {
        const r = rec(decodeURIComponent(m[1]));
        if (typeOf(r) !== "wind") throw mises("not_found", `study ${r.sim_id} has no velocity slice`, "only finished studies have one");
        const bytes = png(m[2]);
        res.writeHead(200, { "Content-Type": "image/png", "Content-Length": bytes.length });
        res.end(bytes);
        return RAW;
      },
    },
    {
      method: "POST", re: /^\/sims\/([^/]+)\/cancel$/, h: (m, _q, _b, _r, res) => {
        const r = rec(decodeURIComponent(m[1]));
        const run = d.state().running.get(r.sim_id);
        if (!run) {
          const text = JSON.stringify({ error: "not_running", status: r.status, message: `${r.sim_id} is not running` });
          res.writeHead(409, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(text) });
          res.end(text);
          return RAW;
        }
        run.finish("cancelled");
        return { sim_id: r.sim_id, status: "cancelling" };
      },
    },
  ];
}

/** POST /sims {type, part_id, settings}: 202 and the core's event sequence. */
export function startStudy(d: StudyDeps, b: Record<string, unknown>): { run_id: string; status: "running" } {
  if (!isType(b.type)) throw invalid(`invalid settings: type: Input should be ${STUDY_TYPE_IDS.join(", ")}`);
  const type = b.type;
  if (typeof b.part_id !== "string") throw invalid("assembly (project_id) studies are not supported yet");
  const partId = b.part_id;
  if (!d.parts().some((p) => p.part_id === partId && p.metrics !== null)) throw mises("not_found", `no built part '${partId}'`);
  const settings = fullSettings(type, b.settings);
  if (type === "modal" && typeof settings.n_modes === "number" && settings.n_modes > 30) {
    throw invalid("invalid modal settings: n_modes: Input should be less than or equal to 30", [{ type: "less_than_equal", loc: ["n_modes"], msg: "Input should be less than or equal to 30", input: settings.n_modes, ctx: { le: 30 }, url: "https://errors.pydantic.dev/2.14/v/less_than_equal" }]);
  }
  const ofState = d.openfoam?.() ?? "ok";
  const real = p6Base(type, settings, ofState) ?? REAL_STUDY.records[type];
  const run = d.newRun("studies", "ui", "studies-page", `Studies page: ${type} study of ${partId}`, true);
  const callId = `ui-${hex(6)}`;
  const simId = `sim_${hex(6)}`;
  const input = { type, part_id: partId, settings, reason: b.reason ?? null, requirements: b.requirements ?? [] };
  d.emit({ type: "run_started", run_id: run.id, data: { agent: "studies", provider: "ui", model: "studies-page", parent_id: null, external: true } });
  const emitted = d.emit({ type: "tool_call", run_id: run.id, data: { call_id: callId, name: "sim_run", input } },
    { steps: [{ kind: "tool_call", payload: { call_id: callId, name: "sim_run", input } }] }) as { step_ids?: number[] } | undefined;
  const stepId = emitted?.step_ids?.[0] ?? null;
  // like the core: every sim_progress names its call, and the last one is a terminal phase
  const progress = (p: Record<string, unknown>) => d.emit({ type: "sim_progress", run_id: run.id, data: { ...p, sim_id: simId, part_id: partId, type, call_id: callId, step_id: stepId } });
  const rec: StudyRecord = {
    ...structuredClone(real), sim_id: simId, part_id: partId, run_id: run.id, call_id: callId, status: "running", stage: "queued",
    phase: "queued", pct: 0, created_at: now(), finished_at: null, settings, inputs: { ...(real.inputs as object), settings },
    reason: typeof b.reason === "string" ? b.reason : null, stale: false, results: undefined, card: undefined,
  };
  // a running record has no results or outputs yet; GET /sims/{id} always adds its card
  for (const k of ["results", "solver", "mesh", "inputs_hash", "glb", "outputs", "checks", "color_range_cp", "color_range_mpa", "color_range"]) {
    delete (rec as Record<string, unknown>)[k];
  }
  rec.card = { ...structuredClone(real.card!), sim_id: simId, status: "running", verdict: null, phase: "queued", pct: 0, run_id: run.id, metrics: [], checks: [], warnings: [], notes: [], fidelity_note: null };
  d.records().set(simId, rec);
  // the real sim_progress events of this study type, in order
  const prog = REAL_STUDY.events.filter((e) => e.type === "sim_progress" && (e.data as { type?: string }).type === type)
    .map((e) => e.data as Record<string, unknown>);
  const firstSim = prog[0]?.sim_id;
  const TERMINAL = ["done", "failed", "cancelled"];
  const mine = prog.filter((p) => p.sim_id === firstSim && !TERMINAL.includes(String(p.phase)));
  const steps = mine.length ? mine : [{ stage: "solve", phase: "solve", pct: 50, detail: "solving" }];
  const st = d.state();
  const timers: ReturnType<typeof setTimeout>[] = [];
  const at = (i: number, fn: () => void) => timers.push(setTimeout(fn, d.stepMs() * i));
  let ended = false;
  const finish = (status: "done" | "cancelled") => {
    if (ended) return;
    ended = true;
    timers.forEach(clearTimeout);
    st.running.delete(simId);
    if (status === "cancelled") {
      const card = { ...structuredClone(real.card!), sim_id: simId, status: "cancelled", verdict: "fail" as const, run_id: run.id, phase: rec.phase, pct: rec.pct, metrics: [], checks: [], warnings: [], notes: [], fidelity_note: null };
      Object.assign(rec, { status: "cancelled", stage: "cancelled", error: "cancelled", finished_at: now(), card });
      progress({ stage: "cancelled", phase: "cancelled", pct: rec.pct, detail: "cancelled" });
      d.emit({ type: "tool_result", run_id: run.id, data: { call_id: callId, name: "sim_run", ok: false, summary: `study ${simId} cancelled by the user` } },
        { steps: [{ kind: "error", payload: { call_id: callId, name: "sim_run", output: { ok: false, summary: `study ${simId} cancelled by the user`, data: { sim_id: simId, error: "cancelled", message: "cancelled", card } } } }] });
      d.emit({ type: "run_finished", run_id: run.id, data: { status: "cancelled", stop_reason: "cancelled", error: null, final_text: "", external: true } });
      return;
    }
    Object.assign(rec, { ...structuredClone(real), sim_id: simId, part_id: partId, run_id: run.id, call_id: callId, settings, reason: rec.reason, created_at: rec.created_at, finished_at: now(), stale: false,
      ...("inputs" in real ? { inputs: { ...(real.inputs as object), settings } } : {}) });
    // several load cases: like core sim/static.py (QA B1-3), results.per_case
    // [{case_id, name, max_vm_mpa, max_disp_mm, sf}] + results.governing_case_id (case names are
    // the ids), each case's figures scaled from the real result by its load, the largest governing
    const cases = type === "static" && Array.isArray(settings.load_cases) ? settings.load_cases as { name?: string; loads?: { newtons?: number }[] }[] : [];
    if (cases.length > 1) {
      const res = (rec.results ?? {}) as Record<string, unknown>;
      const vm = typeof res.max_von_mises_mpa === "number" ? res.max_von_mises_mpa : 10;
      const disp = typeof res.max_displacement_mm === "number" ? res.max_displacement_mm : 1;
      const allow = typeof res.allowable_mpa === "number" ? res.allowable_mpa : 35;
      const force = (c: (typeof cases)[number]) => (c.loads ?? []).reduce((s, l) => s + Math.abs(l.newtons ?? 0), 0) || 1;
      const top = Math.max(...cases.map(force));
      const perCase = cases.map((c, i) => {
        const k = force(c) / top;
        const name = c.name ?? `case ${i + 1}`;
        return { case_id: name, name, max_vm_mpa: vm * k, max_disp_mm: disp * k, sf: allow / (vm * k) };
      });
      const gov = perCase.reduce((a, x) => (x.max_vm_mpa > a.max_vm_mpa ? x : a));
      rec.results = {
        ...res, governing_case: gov.name, governing_case_id: gov.case_id, per_case: perCase,
        cases: perCase.map((x) => ({ name: x.name, max_von_mises_mpa: x.max_vm_mpa, safety_factor: x.sf, max_displacement_mm: x.max_disp_mm, error_band: 0.042 })),
        sf_by_case: Object.fromEntries(perCase.map((x) => [x.name, x.sf])),
      };
    }
    if (cases.length === 1) {
      // one posted case (a studies-agent proposal, Plan 9 U-D): the real two-case result keeps
      // its governing case's figures under the posted case's name, so the result names it
      const res = (rec.results ?? {}) as Record<string, unknown>;
      const name = cases[0].name ?? "case 1";
      const gov = String(res.governing_case_id ?? res.governing_case ?? "");
      type Row = Record<string, unknown>;
      const pick = (xs: unknown, key: string): Row[] | undefined => Array.isArray(xs)
        ? [{ ...((xs as Row[]).find((x) => x[key] === gov) ?? (xs as Row[])[0]), [key]: name, name }] : undefined;
      const sf = res.sf_by_case && typeof res.sf_by_case === "object" ? (res.sf_by_case as Record<string, unknown>)[gov] : undefined;
      rec.results = {
        ...res, governing_case: name, governing_case_id: name, ...(pick(res.per_case, "case_id") ? { per_case: pick(res.per_case, "case_id") } : {}),
        ...(pick(res.cases, "name") ? { cases: pick(res.cases, "name") } : {}), ...(sf !== undefined ? { sf_by_case: { [name]: sf } } : {}),
      };
    }
    const resNow = (rec.results ?? {}) as Record<string, unknown>;
    // core studies.card: a static result's per-case table rides on the card too
    const caseCard = Array.isArray(resNow.per_case) ? { per_case: resNow.per_case, governing_case_id: resNow.governing_case_id ?? null } : {};
    rec.card = { ...structuredClone(real.card!), ...caseCard, sim_id: simId, run_id: run.id, reason: rec.reason, urls: { record: `/sims/${simId}`, glb: `/sims/${simId}/result.glb`, charts: `/sims/${simId}/charts`, probe: `/sims/${simId}/probe`, section: `/sims/${simId}/section` }, charts_url: `/sims/${simId}/charts` };
    // like core studies.card: with no requirements posted there are no checks, and the verdict
    // comes from the study's own warnings (the real record's checks were its agent's requirements)
    if (!Array.isArray(b.requirements) || b.requirements.length === 0) {
      if ("checks" in rec) rec.checks = [];
      rec.card = { ...rec.card, checks: [], verdict: (rec.warnings ?? []).length ? "warn" : "pass" };
    }
    p6Finish(rec, type, settings, st.cal, ofState); // Plan 6: S1 nonlinear, S2 OpenFOAM, S3 fatigue / anisotropy / calibration (real shapes)
    const data = { sim_id: simId, part_id: partId, type, status: "done", card: rec.card, checks: rec.card.checks, results: rec.results };
    const summary = `GUIDE ONLY - ${type} study of ${partId} (fake core, real ${type} numbers)`;
    progress({ stage: "done", phase: "done", pct: 100, detail: "done" });
    d.emit({ type: "tool_result", run_id: run.id, data: { call_id: callId, name: "sim_run", ok: true, summary } },
      { steps: [{ kind: "sim_run", payload: { call_id: callId, name: "sim_run", output: { ok: true, summary, data, handles: [], warnings: rec.warnings, step_kind: "sim_run", images: [] } } }] });
    d.emit({ type: "run_finished", run_id: run.id, data: { status: "done", stop_reason: "done", error: null, final_text: "", external: true } });
  };
  st.running.set(simId, { runId: run.id, timers, finish: () => finish("cancelled") });
  steps.forEach((p, i) => at(i + 1, () => {
    Object.assign(rec, { stage: p.stage, phase: p.phase, pct: p.pct });
    progress(p);
  }));
  if (!d.hang()) at(steps.length + 2, () => finish("done"));
  return { run_id: run.id, status: "running" };
}

/** The run was cancelled (POST /runs/{id}/cancel): its study is cancelled too. */
export function cancelStudiesOfRun(st: StudyState, runId: string): void {
  for (const r of [...st.running.values()]) if (r.runId === runId) r.finish("cancelled");
}

export function clearStudyTimers(st: StudyState): void {
  for (const r of st.running.values()) r.timers.forEach(clearTimeout);
  st.running.clear();
}
