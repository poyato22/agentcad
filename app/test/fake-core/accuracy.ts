// Fake core: Plan 6 (simulation accuracy) on top of the real Plan 5 shapes.
//   S3  REAL shapes (core/scripts/dump_s3_fixtures.py → core/tests/fixtures/ui/s3_*): the study
//       types with material.anisotropy / material.printer / fatigue, the materials, a static
//       study with an orthotropic PLA and fatigue, and /sims/calibrations (GET, POST /fit preview,
//       POST save = apply, GET /{id}, DELETE /{id}) with a port of core sim/calibration.py's fit.
//   S1  ASSUMED: static settings.nonlinear {large_deflection, plasticity, steps, contact: [faces |
//       rigid | instances]}; results.nonlinear; channels plastic_strain / contact_pressure.
//   S2  ASSUMED: wind settings.solver "openfoam"; a /sims/solvers cfd row "openfoam" whose install
//       fix is POST /system/install/openfoam; the result "OpenFOAM k-ω SST · ±10% (validated)".
import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Calibration, CalibrationFit } from "../../src/api/accuracy";
import type { StudyMaterial, StudyRecord } from "../../src/api/studies";
import { asBody, HttpError, mises, RAW, type Route } from "./server";

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const hex = (n: number) => randomBytes(n).toString("hex");
const now = () => Date.now() / 1000;
const DIR = resolve(import.meta.dirname, "../../../core/tests/fixtures/ui");
const json = <T>(n: string): T => JSON.parse(readFileSync(resolve(DIR, n), "utf8")) as T;

/** The real S3 core's answers. */
export const REAL_S3 = {
  types: json<{ id: string; schema: unknown; channels?: string[] }[]>("s3_types.json"),
  materials: json<{ builtin: StudyMaterial[]; custom: StudyMaterial[] }>("s3_materials.json"),
  static: json<StudyRecord>("s3_static.json"),
  calibrated: json<StudyRecord>("s3_static_calibrated.json"),
  probe: json<Obj>("s3_probe.json"),
};

// ---- S1 / S2 schema additions (assumed), on top of the real S3 types

const FACE = { description: "a preset (bottom, top, x-min, x-max, y-min, y-max, z-min, z-max) or a face id like 'f3' (mirror_faces)", type: "string" };
const FRICTION = { default: 0.2, minimum: 0, maximum: 2, description: "friction coefficient μ (0 = frictionless)", type: "number" };

export const P6_DEFS: Obj = {
  ContactFaces: {
    additionalProperties: false, title: "ContactFaces", type: "object", required: ["master", "slave"],
    properties: { kind: { const: "faces", default: "faces", type: "string" }, master: { ...FACE, title: "Master" }, slave: { ...FACE, title: "Slave" }, friction: FRICTION },
  },
  ContactRigid: {
    additionalProperties: false, title: "ContactRigid", type: "object", required: ["kind", "face"],
    properties: { kind: { const: "rigid", type: "string" }, face: { ...FACE, title: "Face", description: "the face resting on a rigid support" }, friction: FRICTION },
  },
  ContactInstances: {
    additionalProperties: false, title: "ContactInstances", type: "object", required: ["kind", "a", "b"],
    properties: {
      kind: { const: "instances", type: "string" },
      a: { type: "string", minLength: 1, maxLength: 60, description: "assembly instance id (assembly studies)" },
      b: { type: "string", minLength: 1, maxLength: 60, description: "the other instance" }, friction: FRICTION,
    },
  },
  NonlinearSettings: {
    additionalProperties: false, title: "NonlinearSettings", type: "object",
    properties: {
      large_deflection: { default: true, type: "boolean", description: "geometric nonlinearity (NLGEOM): follows large deflections" },
      plasticity: { default: false, type: "boolean", description: "bilinear plasticity from the material's yield and tangent modulus" },
      steps: { default: 10, minimum: 1, maximum: 200, type: "integer", description: "load increments (more = slower, steadier)" },
      contact: {
        default: [], maxItems: 12, type: "array", description: "surface-to-surface contact pairs",
        items: {
          oneOf: [{ $ref: "#/$defs/ContactFaces" }, { $ref: "#/$defs/ContactRigid" }, { $ref: "#/$defs/ContactInstances" }],
          discriminator: { propertyName: "kind", mapping: { faces: "#/$defs/ContactFaces", rigid: "#/$defs/ContactRigid", instances: "#/$defs/ContactInstances" } },
        },
      },
    },
  },
};

/** GET /sims/types: the real S3 types plus the assumed S1 (nonlinear) and S2 (openfoam) settings. */
export function p6Types<T extends { id: string; schema: unknown; channels?: string[] }>(types: readonly T[]): T[] {
  return types.map((t0) => {
    const t = structuredClone(t0) as T & { schema: Obj; channels: string[] };
    const props = (t.schema.properties ?? {}) as Obj;
    if (t.id === "wind") {
      const solver = props.solver as Obj | undefined;
      if (solver && Array.isArray(solver.enum) && !solver.enum.includes("openfoam")) {
        solver.enum = [...solver.enum, "openfoam"];
        solver.description = `${String(solver.description ?? "")}; openfoam: OpenFOAM k-ω SST in a container (Docker / OrbStack, optional)`;
      }
      return t;
    }
    if (t.id === "static") {
      t.schema.$defs = { ...(t.schema.$defs as Obj), ...P6_DEFS };
      props.nonlinear = { anyOf: [{ $ref: "#/$defs/NonlinearSettings" }, { type: "null" }], default: null, description: "large deflection, plasticity and contact (CalculiX NLGEOM); null = linear" };
      t.channels = [...(t.channels ?? []), "plastic_strain", "contact_pressure"];
    }
    return t;
  });
}

// ---- S3 calibration: a port of core sim/calibration.py

const KEY_RE = /^[A-Za-z0-9][A-Za-z0-9 ._+()-]{0,39}$/;
const KEYS = {
  top: ["material", "printer", "tensile", "bend", "part", "note"],
  tensile: ["force_n", "width_mm", "thickness_mm", "area_mm2", "elongation_pct", "orientation", "note"],
  bend: ["span_mm", "width_mm", "thickness_mm", "load_n", "deflection_mm", "break_load_n", "orientation", "note"],
  part: ["sim_id", "point", "load_n", "deflection_mm", "direction", "study_load_n", "note"],
} as const;

const err422 = (loc: (string | number)[], msg: string, input: unknown) =>
  new HttpError(422, { detail: [{ type: "value_error", loc: ["body", ...loc], msg, input, ctx: { error: {} } }] });

function stats(v: number[]) {
  const n = v.length;
  const mean = v.reduce((a, b) => a + b, 0) / n;
  const sd = n > 1 ? Math.sqrt(v.reduce((a, b) => a + (b - mean) ** 2, 0) / (n - 1)) : null;
  return { n, mean, sd, cv: sd !== null && mean > 0 ? sd / mean : null, min: Math.min(...v), max: Math.max(...v) };
}
const confidence = (n: number, cv: number | null) => (n >= 5 && cv !== null && cv <= 0.05 ? "high" : n >= 3 && cv !== null && cv <= 0.15 ? "medium" : "low") as "low" | "medium" | "high";
function fitModulus(e: number[]) {
  const fit = e.reduce((a, x) => a + x * x, 0) / e.reduce((a, x) => a + x, 0);
  const st = stats(e);
  return { ...st, value_mpa: fit, residuals: e.map((x) => 1 - fit / x), confidence: confidence(st.n, st.cv) };
}
function fitStrength(s: number[]) {
  const st = stats(s);
  let kd = st.n >= 3 && st.sd !== null ? (st.mean - 2 * st.sd) / st.mean : (0.85 * st.min) / st.mean;
  kd = Math.min(1, Math.max(0.4, kd));
  return { ...st, value_mpa: st.mean, knockdown: kd, knockdown_rule: st.n >= 3 ? "(mean - 2 sd) / mean" : "0.85 x weakest / mean (n < 3)", confidence: confidence(st.n, st.cv) };
}

interface Body { material: string; printer: string; tensile: Obj[]; bend: Obj[]; part: Obj[]; note: string }

/** pydantic-like checks of CalibrationInput (extra keys are refused, like extra="forbid"). */
function parseBody(raw: unknown, allowAppend = false): Body {
  const b = asBody(raw);
  for (const k of Object.keys(b)) if (!(KEYS.top as readonly string[]).includes(k) && !(allowAppend && k === "append")) throw new HttpError(422, { detail: [{ type: "extra_forbidden", loc: ["body", k], msg: "Extra inputs are not permitted", input: b[k] }] });
  const asList = (k: string): Obj[] => {
    const v = b[k] ?? [];
    if (!Array.isArray(v) || v.length > 100) throw err422([k], "a list of at most 100 tests", v);
    return v as Obj[];
  };
  const lists = { tensile: asList("tensile"), bend: asList("bend"), part: asList("part") };
  for (const [kind, list] of Object.entries(lists)) {
    list.forEach((t, i) => {
      if (!isObj(t)) throw err422([kind, i], "a test object", t);
      for (const k of Object.keys(t)) if (!(KEYS[kind as "tensile"] as readonly string[]).includes(k)) throw new HttpError(422, { detail: [{ type: "extra_forbidden", loc: ["body", kind, i, k], msg: "Extra inputs are not permitted", input: t[k] }] });
    });
  }
  lists.tensile.forEach((t: Obj, i: number) => {
    if (!(Number(t.force_n) > 0)) throw err422(["tensile", i, "force_n"], "Input should be greater than 0", t.force_n);
    if (t.area_mm2 == null && (t.width_mm == null || t.thickness_mm == null)) throw err422(["tensile", i], "Value error, give area_mm2 or width_mm and thickness_mm", t);
  });
  lists.part.forEach((t: Obj, i: number) => {
    if (typeof t.sim_id !== "string" || !/^sim_[0-9a-f]{12}$/.test(t.sim_id)) throw err422(["part", i, "sim_id"], "String should match pattern '^sim_[0-9a-f]{12}$'", t.sim_id);
    if (!Array.isArray(t.point) || t.point.length !== 3) throw err422(["part", i, "point"], "Field required", t.point);
  });
  const material = String(b.material ?? "").trim();
  const printer = String(b.printer ?? "default").trim();
  for (const k of [material, printer]) if (!KEY_RE.test(k)) throw err422([], `Value error, bad name '${k}': 1-40 letters, digits, spaces or ._+()-`, b);
  if (!lists.tensile.length && !lists.bend.length && !lists.part.length) throw err422([], "Value error, give at least one tensile, bend or part test", b);
  return { material, printer, tensile: lists.tensile, bend: lists.bend, part: lists.part, note: typeof b.note === "string" ? b.note : "" };
}

const area = (t: Obj) => (t.area_mm2 != null ? Number(t.area_mm2) : Number(t.width_mm) * Number(t.thickness_mm));
const bendE = (t: Obj) => (Number(t.load_n) * Number(t.span_mm) ** 3) / (48 * Number(t.deflection_mm) * ((Number(t.width_mm) * Number(t.thickness_mm) ** 3) / 12));
const xy = (t: Obj) => (t.orientation ?? "xy") === "xy";

export function fakeFit(b: Body, base: StudyMaterial, record: (id: string) => StudyRecord | undefined): CalibrationFit {
  const parts = b.part.map((t) => {
    const r = record(String(t.sim_id));
    if (!r || r.status !== "done") throw mises("invalid_input", `no study ${String(t.sim_id)}`, "run a static study of the tested part first");
    const res = (r.results ?? {}) as Obj;
    const cases = ((r.settings ?? {}) as Obj).load_cases as { loads?: { newtons?: number }[] }[] | undefined;
    const load = Number(t.study_load_n ?? 0) || (cases?.[0]?.loads ?? []).reduce((s, l) => s + Math.abs(l.newtons ?? 0), 0);
    const eStudy = Number((r.material as Obj | undefined)?.e_mpa ?? base.e_mpa);
    const pred = Number(res.max_displacement_mm ?? 1) * Number(t.load_n) / (load || 1);
    return { sim_id: String(t.sim_id), e_mpa: eStudy * pred / Number(t.deflection_mm), e_study_mpa: eStudy, predicted_deflection_mm: pred, measured_deflection_mm: Number(t.deflection_mm), study_load_n: load };
  });
  const out: Obj = { n_tests: b.tensile.length + b.bend.length + b.part.length };
  const eXy = [...b.bend.filter(xy).map(bendE), ...parts.map((p) => p.e_mpa)];
  const eZ = b.bend.filter((t) => !xy(t)).map(bendE);
  const secant = b.tensile.filter((t) => xy(t) && Number(t.elongation_pct) > 0).map((t) => Number(t.force_n) / area(t) / (Number(t.elongation_pct) / 100));
  if (eXy.length) out.e = { ...fitModulus(eXy), method: "least squares: three-point bend / part load tests" };
  else if (secant.length) out.e = { ...fitModulus(secant), method: "secant modulus at break (a lower bound)" };
  if (eZ.length) out.e_z = { ...fitModulus(eZ), method: "least squares: three-point bend (z)" };
  const sXy = b.tensile.filter(xy).map((t) => Number(t.force_n) / area(t));
  const sZ = b.tensile.filter((t) => !xy(t)).map((t) => Number(t.force_n) / area(t));
  if (sXy.length) { out.strength = fitStrength(sXy); out.datasheet_ratio = (out.strength as { mean: number }).mean / base.yield_mpa; }
  if (sZ.length) out.z_strength = fitStrength(sZ);
  const kds = (["strength", "z_strength"] as const).filter((k) => out[k]).map((k) => (out[k] as { knockdown: number }).knockdown);
  if (kds.length) out.knockdown = Math.min(...kds);
  const order = { low: 0, medium: 1, high: 2 };
  const confs = (["e", "e_z", "strength", "z_strength"] as const).filter((k) => out[k]).map((k) => (out[k] as { confidence: "low" | "medium" | "high" }).confidence);
  out.confidence = confs.length ? confs.reduce((a, c) => (order[c] < order[a] ? c : a)) : "low";
  if (parts.length) out.part_tests = parts;
  return out as unknown as CalibrationFit;
}

const basisLabel = (n: number) => `calibrated (${n} test${n === 1 ? "" : "s"})`;

/** core calibration.apply: the material with the fitted values. */
export function applyFit(m: StudyMaterial, rec: { id: string; printer: string; fit: CalibrationFit; updated_at?: number | null }): StudyMaterial {
  const f = rec.fit;
  const kw: Obj = {};
  if (f.e) kw.e_mpa = f.e.value_mpa;
  if (f.e_z) kw.e_z_mpa = f.e_z.value_mpa;
  if (f.strength) kw.yield_mpa = f.strength.value_mpa;
  if (f.z_strength) kw.z_strength_mpa = f.z_strength.value_mpa;
  if (typeof f.knockdown === "number") kw.knockdown = f.knockdown;
  const label = basisLabel(f.n_tests);
  const out = { ...m, ...kw, basis: label, source: `${m.source}; ${label} on ${rec.printer}`,
    calibration: { id: rec.id, printer: rec.printer, n_tests: f.n_tests, confidence: f.confidence, updated_at: rec.updated_at ?? null } } as Obj;
  out.allowable_mpa = Number(out.yield_mpa) * Number(out.knockdown);
  out.e_eff_mpa = Number(out.e_mpa) * Number(out.stiffness_factor ?? 1);
  return out as unknown as StudyMaterial;
}

export interface CalState { list: Calibration[] }
export const calStateFrom = (): CalState => ({ list: [] });

export interface CalDeps {
  cal(): CalState;
  material(name: string): StudyMaterial | undefined;
  record(simId: string): StudyRecord | undefined;
}

const same = (c: Calibration, material: string, printer: string) => c.material.toLowerCase() === material.toLowerCase() && c.printer.toLowerCase() === printer.toLowerCase();

/** The calibration a study of `material` on `printer` uses (core studies.resolve_material). */
export function calibrationOf(st: CalState, material: string, printer: string | null | undefined): Calibration | undefined {
  return st.list.find((c) => same(c, material, printer || "default"));
}

export function calibrationRoutes(d: CalDeps): Route[] {
  const base = (name: string) => {
    const m = d.material(name);
    if (!m) throw mises("invalid_input", `unknown material '${name}'`, "use one of the built-in or saved materials");
    return m;
  };
  const dumpTests = (b: Body) => ({
    tensile: b.tensile.map((t) => ({ force_n: Number(t.force_n), width_mm: t.width_mm ?? null, thickness_mm: t.thickness_mm ?? null, area_mm2: t.area_mm2 ?? null, elongation_pct: t.elongation_pct ?? null, orientation: t.orientation ?? "xy", note: t.note ?? "" })),
    bend: b.bend.map((t) => ({ span_mm: Number(t.span_mm), width_mm: Number(t.width_mm), thickness_mm: Number(t.thickness_mm), load_n: Number(t.load_n), deflection_mm: Number(t.deflection_mm), break_load_n: t.break_load_n ?? null, orientation: t.orientation ?? "xy", note: t.note ?? "" })),
    part: b.part.map((t) => ({ sim_id: t.sim_id, point: t.point, load_n: Number(t.load_n), deflection_mm: Number(t.deflection_mm), direction: t.direction ?? null, study_load_n: t.study_load_n ?? null, note: t.note ?? "" })),
  });
  return [
    {
      method: "GET", re: /^\/sims\/calibrations$/, h: (_m, q) => {
        const m = q.get("material");
        return structuredClone(d.cal().list.filter((c) => m === null || c.material.toLowerCase() === m.toLowerCase()));
      },
    },
    {
      method: "POST", re: /^\/sims\/calibrations\/fit$/, h: (_m, _q, raw) => {
        const b = parseBody(raw);
        const m = base(b.material);
        const fit = fakeFit(b, m, d.record);
        return { fit, material: applyFit(m, { id: "cal_preview", printer: b.printer, fit, updated_at: null }), datasheet: m, basis: basisLabel(fit.n_tests), saved: false };
      },
    },
    {
      method: "POST", re: /^\/sims\/calibrations$/, h: (_m, _q, raw, _r, res) => {
        const b = parseBody(raw, true);
        const m = base(b.material);
        const fit = fakeFit(b, m, d.record);
        const st = d.cal();
        const old = st.list.find((c) => same(c, b.material, b.printer));
        const rec = {
          id: old?.id ?? `cal_${hex(5)}`, material: b.material, printer: b.printer, created_at: old?.created_at ?? now(), updated_at: now(),
          note: b.note, basis: basisLabel(fit.n_tests), tests: dumpTests(b), fit,
        } as unknown as Calibration;
        st.list = [...st.list.filter((c) => c !== old), rec];
        const text = JSON.stringify({ ...rec, material_preview: applyFit(m, rec) });
        res.writeHead(201, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(text) });
        res.end(text);
        return RAW;
      },
    },
    {
      method: "GET", re: /^\/sims\/calibrations\/([^/]+)$/, h: (m) => {
        const id = decodeURIComponent(m[1]);
        if (!/^cal_[0-9a-f]{10}$/.test(id)) throw mises("invalid_input", `bad calibration id: '${id}'`);
        const c = d.cal().list.find((x) => x.id === id);
        if (!c) throw mises("not_found", `no calibration ${id}`);
        return structuredClone(c);
      },
    },
    {
      method: "DELETE", re: /^\/sims\/calibrations\/([^/]+)$/, h: (m) => {
        const id = decodeURIComponent(m[1]);
        if (!/^cal_[0-9a-f]{10}$/.test(id)) throw mises("invalid_input", `bad calibration id: '${id}'`);
        if (!d.cal().list.some((x) => x.id === id)) throw mises("not_found", `no calibration ${id}`);
        d.cal().list = d.cal().list.filter((x) => x.id !== id);
        return { deleted: id };
      },
    },
  ];
}

// ---- solvers (S2, assumed)

export type OpenFoamState = "ok" | "missing" | "no_runtime";

/** The OpenFOAM row of /sims/solvers cfd (replaces the real "openfoam_container" placeholder). */
export function openFoamRow(state: OpenFoamState): Obj {
  if (state === "ok") return { id: "openfoam", name: "OpenFOAM (Docker / OrbStack)", status: "ok", version: "v2406 (image pinned)", path: "/usr/local/bin/docker", detail: "ready: runs offline in a container, CPU and memory capped", fix: null };
  if (state === "missing") {
    return {
      id: "openfoam", name: "OpenFOAM (Docker / OrbStack)", status: "missing", version: null, path: "/usr/local/bin/docker",
      detail: "container runtime found; the pinned OpenFOAM image (~1.1 GB) is not downloaded yet",
      fix: { kind: "install", label: "Download OpenFOAM image", dep: "openfoam" },
    };
  }
  return {
    id: "openfoam", name: "OpenFOAM (Docker / OrbStack)", status: "optional_missing", version: null, path: null,
    detail: "needs Docker or OrbStack; the built-in solver is used",
    fix: { kind: "open_url", label: "Get OrbStack", url: "https://orbstack.dev/download" },
  };
}

export function withOpenFoam(cfd: Obj[] | undefined, state: OpenFoamState): Obj[] {
  return [...(cfd ?? []).filter((r) => !/^openfoam/.test(String(r.id))), openFoamRow(state)];
}

// ---- results

/** The real record a study's finish starts from: S3's static study when it asks for anisotropy or fatigue. */
export function p6Base(type: string, settings: Obj): StudyRecord | null {
  const mat = isObj(settings.material) ? settings.material : {};
  return type === "static" && (isObj(settings.fatigue) || isObj(mat.anisotropy)) ? REAL_S3.static : null;
}

/** A finished study, with what the Plan 6 core adds for these settings. */
export function p6Finish(rec: StudyRecord, type: string, settings: Obj, cal: CalState): void {
  const card = rec.card;
  if (!card) return;
  const res = { ...((rec.results ?? {}) as Obj) };
  const notes = [...card.notes];
  let metrics = [...card.metrics];
  const mat = isObj(settings.material) ? settings.material : {};
  // S3: fatigue and anisotropy come from the real S3 record; drop what these settings did not ask for
  if (!isObj(settings.fatigue)) {
    for (const k of ["fatigue", "fatigue_safety_factor", "cycles_to_failure"]) delete res[k];
    metrics = metrics.filter((m) => m.key !== "fatigue_safety_factor" && m.key !== "cycles_to_failure");
    delete (card as Obj).fatigue_note;
  } else if (isObj(res.fatigue)) {
    const f = settings.fatigue as Obj;
    res.fatigue = { ...res.fatigue, cycles_required: f.cycles, cycle: f.cycle ?? "zero_to_max" };
  }
  if (!isObj(mat.anisotropy)) {
    for (const k of ["safety_factor_von_mises", "max_layer_tension_mpa", "max_layer_tension_at", "allowable_z_mpa", "layer_safety_factor", "governing_failure"]) delete res[k];
    metrics = metrics.filter((m) => m.key !== "layer_safety_factor");
  }
  // S3: a saved calibration of this material and printer applies (core studies.resolve_material)
  const name = typeof mat.name === "string" ? mat.name : "PLA";
  const c = mat.calibrated === false ? undefined : calibrationOf(cal, name, typeof mat.printer === "string" ? mat.printer : null);
  if (c && isObj(rec.material)) {
    rec.material = applyFit(rec.material as unknown as StudyMaterial, c) as unknown as StudyRecord["material"];
    (card as Obj).material_basis = c.basis;
    rec.caveats = [...rec.caveats, `Material ${c.basis} on printer ${c.printer} (confidence ${c.fit.confidence}): fitted to the user's own tests.`];
  }
  // S2 (assumed)
  if (type === "wind" && settings.solver === "openfoam") {
    Object.assign(rec, { fidelity_class: "cfd_openfoam", solver: { name: "OpenFOAM", version: "v2406", procedure: "simpleFoam, k-ω SST", method: "snappyHexMesh around the STL" } });
    if (isObj(rec.mesh)) delete (rec.mesh as Obj).lattice;
    Object.assign(card, { fidelity_class: "cfd_openfoam", fidelity_note: "OpenFOAM k-ω SST · ±10% (validated)" });
    for (const mt of metrics) if (mt.key === "error_band_pct") mt.value = 10;
    res.model_error_band = 0.1;
  }
  // S1 (assumed)
  const nl = isObj(settings.nonlinear) ? settings.nonlinear : null;
  if (type === "static" && nl) {
    const contact = Array.isArray(nl.contact) ? nl.contact.length : 0;
    const plastic = nl.plasticity === true;
    res.nonlinear = {
      converged: true, increments: typeof nl.steps === "number" ? nl.steps : 10, load_reached_pct: 100,
      large_deflection: nl.large_deflection !== false, plasticity: plastic, contact_pairs: contact,
      max_plastic_strain: plastic ? 0.0042 : 0, max_contact_pressure_mpa: contact ? 18.4 : null,
    };
    if (plastic) metrics.push({ key: "max_plastic_strain", label: "peak plastic strain", unit: "", value: 0.0042 });
    if (contact) metrics.push({ key: "max_contact_pressure_mpa", label: "peak contact pressure", unit: "MPa", value: 18.4 });
    const what = [nl.large_deflection !== false && "large deflection", plastic && "plasticity", contact && "contact"].filter(Boolean).join(", ");
    Object.assign(card, { fidelity_class: "fem_nonlinear", fidelity_note: `nonlinear FEM (${what || "NLGEOM"}) · ±8% (validated)` });
    rec.fidelity_class = "fem_nonlinear";
    for (const mt of metrics) if (mt.key === "error_band_pct") mt.value = 8;
  }
  rec.results = res;
  card.notes = notes;
  card.metrics = metrics;
}

/** Probe values: the real S3 channels on an S3 record, plus the assumed S1 channels on a nonlinear one. */
export function p6Probe(rec: StudyRecord, probe: Obj): Obj {
  const r = isObj(rec.results) ? rec.results : {};
  let out = probe;
  if (isObj(r.fatigue) || "layer_safety_factor" in r) {
    const s3 = REAL_S3.probe;
    out = { ...out, values: { ...(out.values as Obj), ...(s3.values as Obj) }, units: { ...(out.units as Obj), ...(s3.units as Obj) } };
  }
  const nl = r.nonlinear;
  if (!isObj(nl) || !isObj(out.values)) return out;
  const values = { ...out.values } as Obj;
  const units = { ...(isObj(out.units) ? out.units : {}) } as Obj;
  if (nl.plasticity) { values.plastic_strain = 0.0011; units.plastic_strain = ""; }
  if (typeof nl.contact_pairs === "number" && nl.contact_pairs > 0) { values.contact_pressure = 6.2; units.contact_pressure = "MPa"; }
  return { ...out, values, units };
}
