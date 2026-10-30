// Fake core: Plan 6 (simulation accuracy) on top of the real Plan 5 shapes. Every shape is the
// REAL merged core's (int-678): fixtures dumped by core/scripts/dump_s3_fixtures.py (s3_*) and
// core/scripts/dump_p6_fixtures.py (s1_*, s2_*) into core/tests/fixtures/ui.
//   S3  the study types with material.anisotropy / material.printer / fatigue, the materials, a
//       static study with an orthotropic PLA and fatigue, and /sims/calibrations (GET, POST /fit
//       preview, POST save = apply unless rejected, GET /{id}, DELETE /{id}) with a port of core
//       sim/calibration.py's screened fit (outliers, plausibility, status "rejected").
//   S1  static settings.nonlinear {large_deflection, plasticity, tangent_modulus_mpa, contacts:
//       [{kind contact|bonded, a: {face, instance}, b: {face, instance}, friction, search_mm}],
//       rigid_supports: [{face, normal, friction, gap_mm}], increments, ...}; results.nonlinear;
//       channels plastic_strain / contact_pressure (from s1_static_nonlinear / s1_static_contact).
//   S2  wind settings.solver "openfoam"; the /sims/solvers cfd rows "openfoam_container" and
//       "openfoam" (install fix POST /system/install/openfoam); the record of s2_wind_openfoam
//       ("OpenFOAM k-ω SST · ±10% (target; validation pending)").
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

/** The real S1 (nonlinear / contact) and S2 (OpenFOAM) core's answers. */
export const REAL_S1 = {
  nonlinear: json<StudyRecord>("s1_static_nonlinear.json"),
  contact: json<StudyRecord>("s1_static_contact.json"),
  probe: json<Obj>("s1_probe.json"),
  contactProbe: json<Obj>("s1_contact_probe.json"),
};
export const REAL_S2 = {
  wind: json<StudyRecord>("s2_wind_openfoam.json"),
  rows: json<Record<OpenFoamState, Obj[]>>("s2_openfoam_rows.json"),
};

/** GET /sims/types: the real merged core's types (S1 nonlinear, S2 openfoam, S3 anisotropy /
 *  printer / fatigue are all in the schema already). */
export function p6Types<T extends { id: string; schema: unknown; channels?: string[] }>(types: readonly T[]): T[] {
  return types.map((t) => structuredClone(t) as T);
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
const g = (x: number) => Number(x.toPrecision(4)).toString(); // Python's :.4g / :.3g, close enough for messages
const g3 = (x: number) => Number(x.toPrecision(3)).toString();

// core calibration.py screening constants
const PLAUSIBLE: Record<string, Record<"e" | "strength" | "e_z" | "z_strength", [number, number]>> = {
  unfilled: { e: [0.3, 2.0], strength: [0.3, 1.5], e_z: [0.1, 1.2], z_strength: [0.05, 1.5] },
  "fibre-filled": { e: [0.2, 2.0], strength: [0.2, 1.5], e_z: [0.1, 1.2], z_strength: [0.05, 1.5] },
};
const TEST_RATIO = [0.1, 3.0] as const;
const E_Z_MAX_RATIO = 1.2;
const OUTLIER_REL = 0.5;
const OUTLIER_MZ = 3.5;
const OUTLIER_MZ_MIN_REL = 0.15;
const HIGH_CV = 0.25;
const MIN_SPAN_DEPTH = 10;
const MAX_DEFLECTION_SPAN = 0.1;
export const REJECTED = "rejected";
export const REJECTED_LABEL = "rejected — implausible";
const BASIS_ESTIMATE = "datasheet / estimate";
const PROPERTIES = ["e", "e_z", "strength", "z_strength"] as const;
const PROPERTY_NAMES: Record<string, string> = { e: "E", e_z: "E_z", strength: "strength", z_strength: "Z strength" };

const materialClass = (name: string) => (/(^|[-_ ])(CF|GF)\b|CARBON|GLASS/.test(name.toUpperCase()) ? "fibre-filled" : "unfilled");
const median = (v: number[]) => { const a = [...v].sort((x, y) => x - y); const m = a.length >> 1; return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2; };

interface Outlier { test: string; index: number; property: string; value_mpa: number; reason: string }
type Pt = [kind: string, index: number, value: number, why: string | null];

/** core calibration.screen: drop (and list) bad-geometry tests, gross typos and tests far from the median. */
function screen(points: Pt[], ref: number, what: string, outliers: Outlier[]): number[] {
  const kept: [string, number, number][] = [];
  for (const [kind, i, x, why0] of points) {
    let why = why0;
    const r = ref > 0 ? x / ref : Number.POSITIVE_INFINITY;
    if (why === null && !(TEST_RATIO[0] <= r && r <= TEST_RATIO[1])) {
      why = `${g(x)} MPa is ${g3(r)}x the data sheet (${g(ref)} MPa): outside ${TEST_RATIO[0]}-${TEST_RATIO[1]}x, likely a typo`;
    }
    if (why !== null) outliers.push({ test: kind, index: i, property: what, value_mpa: x, reason: why });
    else kept.push([kind, i, x]);
  }
  if (kept.length >= 2) {
    const vals = kept.map(([, , x]) => x);
    const med = median(vals);
    const mad = median(vals.map((x) => Math.abs(x - med)));
    const good: number[] = [];
    for (const [kind, i, x] of kept) {
      const rel = Math.abs(x / med - 1);
      const mz = mad > 0 ? (0.6745 * Math.abs(x - med)) / mad : 0;
      if (kept.length >= 3 && (rel > OUTLIER_REL || (mz > OUTLIER_MZ && rel > OUTLIER_MZ_MIN_REL))) {
        outliers.push({ test: kind, index: i, property: what, value_mpa: x, reason: `${g(x)} MPa is ${Math.round(100 * rel)}% from the median ${g(med)} MPa of the ${kept.length} tests` });
      } else good.push(x);
    }
    return good;
  }
  return kept.map(([, , x]) => x);
}

function bendReason(t: Obj): string | null {
  const sd = Number(t.span_mm) / Number(t.thickness_mm);
  if (sd < MIN_SPAN_DEPTH) return `span / depth ${g3(sd)} < ${MIN_SPAN_DEPTH}: shear deflection spoils P L^3 / 48 E I (ASTM D790 uses 16)`;
  const ds = Number(t.deflection_mm) / Number(t.span_mm);
  if (ds > MAX_DEFLECTION_SPAN) return `deflection / span ${g3(ds)} > ${MAX_DEFLECTION_SPAN}: outside the small-deflection range`;
  return null;
}

function fitModulus(e: number[]) {
  const fit = e.reduce((a, x) => a + x * x, 0) / e.reduce((a, x) => a + x, 0);
  const st = stats(e);
  // (delta_fit - delta_i) / delta_i = E_i / E - 1
  return { ...st, value_mpa: fit, residuals: e.map((x) => x / fit - 1), confidence: confidence(st.n, st.cv) };
}
function fitStrength(s: number[], warnings: string[], what = "strength") {
  const st = stats(s);
  let kd = st.n >= 3 && st.sd !== null ? (st.mean - 2 * st.sd) / st.mean : (0.85 * st.min) / st.mean;
  if (kd < 0.4) warnings.push(`${what}: the scatter gives a knock-down of ${kd.toFixed(2)}, raised to the floor 0.4; the allowable is not conservative; test more bars`);
  kd = Math.min(1, Math.max(0.4, kd));
  return { ...st, value_mpa: st.mean, knockdown: kd, knockdown_rule: st.n >= 3 && st.sd !== null ? "(mean - 2 sd) / mean" : "0.85 x weakest / mean (n < 3)", confidence: confidence(st.n, st.cv) };
}

/** core calibration.fit (screened; status "ok" or "rejected" with reasons). */
export function fakeFit(b: Body, base: StudyMaterial, record: (id: string) => StudyRecord | undefined): CalibrationFit {
  const parts = b.part.map((t) => {
    const r = record(String(t.sim_id));
    if (!r || r.status !== "done") throw mises("invalid_input", `no study ${String(t.sim_id)}`, "run a static study of the tested part first");
    if (r.fidelity_class === "fem_nonlinear" || isObj((r.settings as Obj | undefined)?.nonlinear)) {
      throw mises("invalid_input", `${String(t.sim_id)} is a nonlinear study (large deflection, plasticity or contact): its deflection does not scale with 1 / E`, "compare the load test with a linear static study of the part");
    }
    const res = (r.results ?? {}) as Obj;
    const cases = ((r.settings ?? {}) as Obj).load_cases as { loads?: { newtons?: number }[] }[] | undefined;
    const load = Number(t.study_load_n ?? 0) || (cases?.[0]?.loads ?? []).reduce((s, l) => s + Math.abs(l.newtons ?? 0), 0);
    const eStudy = Number((r.material as Obj | undefined)?.e_mpa ?? base.e_mpa);
    const pred = Number(res.max_displacement_mm ?? 1) * Number(t.load_n) / (load || 1);
    const out: Obj = { sim_id: String(t.sim_id), e_mpa: eStudy * pred / Number(t.deflection_mm), e_study_mpa: eStudy, predicted_deflection_mm: pred, measured_deflection_mm: Number(t.deflection_mm), study_load_n: load };
    if ((r.material as Obj | undefined)?.build_dir) out.note = "approximate: an orthotropic study; only the in-plane E is scaled by the deflection ratio (E_z keeps its ratio)";
    return out as { sim_id: string; e_mpa: number; e_study_mpa: number; predicted_deflection_mm: number; measured_deflection_mm: number; study_load_n: number };
  });
  const outliers: Outlier[] = [];
  const warnings: string[] = [];
  const reasons: string[] = [];
  const cls = materialClass(base.name);
  const bounds = PLAUSIBLE[cls];
  const bends = b.bend.map((t, i) => ({ t, i }));
  const tens = b.tensile.map((t, i) => ({ t, i }));
  const ePts: Pt[] = [...bends.filter(({ t }) => xy(t)).map(({ t, i }): Pt => ["bend", i, bendE(t), bendReason(t)]), ...parts.map((p, i): Pt => ["part", i, p.e_mpa, null])];
  const ezPts: Pt[] = bends.filter(({ t }) => !xy(t)).map(({ t, i }): Pt => ["bend", i, bendE(t), bendReason(t)]);
  const sPts: Pt[] = tens.filter(({ t }) => xy(t)).map(({ t, i }): Pt => ["tensile", i, Number(t.force_n) / area(t), null]);
  const szPts: Pt[] = tens.filter(({ t }) => !xy(t)).map(({ t, i }): Pt => ["tensile", i, Number(t.force_n) / area(t), null]);
  const eXy = screen(ePts, base.e_mpa, "e", outliers);
  const eZ = screen(ezPts, base.e_mpa, "e_z", outliers);
  const sXy = screen(sPts, base.yield_mpa, "strength", outliers);
  const sZ = screen(szPts, base.yield_mpa, "z_strength", outliers);
  for (const [what, pts, kept] of [["E", ePts, eXy], ["E_z", ezPts, eZ], ["strength", sPts, sXy], ["Z strength", szPts, sZ]] as const) {
    if (pts.length && !kept.length) reasons.push(`every ${what} test was rejected as an outlier (see outliers)`);
  }
  const out: Obj = { material_class: cls, outliers, warnings };
  if (eXy.length) out.e = { ...fitModulus(eXy), method: "least squares: three-point bend / part load tests" };
  const secant = b.tensile.filter((t) => xy(t) && Number(t.elongation_pct) > 0).map((t) => Number(t.force_n) / area(t) / (Number(t.elongation_pct) / 100));
  if (secant.length) out.e_secant = { ...stats(secant), method: "secant modulus at break (a lower bound of E; not applied)" };
  if (eZ.length) out.e_z = { ...fitModulus(eZ), method: "least squares: three-point bend (z)" };
  if (sXy.length) { out.strength = fitStrength(sXy, warnings); out.datasheet_ratio = (out.strength as { mean: number }).mean / base.yield_mpa; }
  if (sZ.length) out.z_strength = fitStrength(sZ, warnings, "Z strength");
  const flex = b.bend.filter((t) => t.break_load_n != null).map((t) => (3 * Number(t.break_load_n) * Number(t.span_mm)) / (2 * Number(t.width_mm) * Number(t.thickness_mm) ** 2));
  if (flex.length) out.flexural_strength = stats(flex);
  const eRef = out.e ? (out.e as { value_mpa: number }).value_mpa : base.e_mpa;
  for (const [k, label, ref] of [["e", "E", base.e_mpa], ["strength", "strength", base.yield_mpa], ["e_z", "E_z", base.e_mpa], ["z_strength", "Z strength", base.yield_mpa]] as const) {
    const f = out[k] as { value_mpa: number; datasheet_ratio?: number } | undefined;
    if (!f) continue;
    const r = f.value_mpa / ref;
    f.datasheet_ratio = r;
    const [lo, hi] = bounds[k];
    if (!(lo <= r && r <= hi)) reasons.push(`fitted ${label} ${g(f.value_mpa)} MPa is ${g3(r)}x the ${base.name} data sheet (${g(ref)} MPa); plausible for ${cls} filaments: ${lo}-${hi}x`);
  }
  if (out.e_z && (out.e_z as { value_mpa: number }).value_mpa / eRef > E_Z_MAX_RATIO) {
    reasons.push(`E_z / E ${g3((out.e_z as { value_mpa: number }).value_mpa / eRef)} > ${E_Z_MAX_RATIO}: printed parts are not stiffer across the layers`);
  }
  const kds = (["strength", "z_strength"] as const).filter((k) => out[k]).map((k) => (out[k] as { knockdown: number }).knockdown);
  if (kds.length) out.knockdown = Math.min(...kds);
  const props = PROPERTIES.filter((k) => out[k]);
  for (const k of props) {
    const f = out[k] as { cv: number | null; n: number; confidence: string };
    if (f.cv !== null && f.cv > HIGH_CV) { f.confidence = "low"; warnings.push(`${k}: scatter (CV) ${Math.round(100 * f.cv)}% > ${100 * HIGH_CV}%: low confidence`); }
    if (f.n < 3) warnings.push(`${k}: only ${f.n} test(s): low confidence`);
  }
  const order = { low: 0, medium: 1, high: 2 };
  const confs = props.map((k) => (out[k] as { confidence: "low" | "medium" | "high" }).confidence);
  out.confidence = confs.length ? confs.reduce((a, c) => (order[c] < order[a] ? c : a)) : "low";
  if (!props.length) reasons.push("no property could be fitted");
  out.n_tests = props.reduce((a, k) => a + (out[k] as { n: number }).n, 0);
  out.n_tests_given = b.tensile.length + b.bend.length + b.part.length;
  out.calibrated = props;
  out.status = reasons.length ? REJECTED : "ok";
  out.status_label = reasons.length ? REJECTED_LABEL : "ok";
  out.reasons = reasons;
  if (parts.length) out.part_tests = parts;
  return out as unknown as CalibrationFit;
}

type FitObj = Record<string, { n: number; value_mpa: number; method?: string } | undefined> & { confidence?: string; status?: string; reasons?: string[]; n_tests?: number; knockdown?: number; e_secant?: { mean?: number } };

/** core calibration.basis_label: which properties were calibrated, on how many tests. */
export function basisLabel(fit: CalibrationFit): string {
  const f = fit as unknown as FitObj;
  const props = PROPERTIES.filter((k) => f[k] && !(k === "e" && (f[k]?.method ?? "").startsWith("secant")));
  if (!props.length) return BASIS_ESTIMATE;
  return `calibrated ${props.map((k) => `${PROPERTY_NAMES[k]} (${f[k]!.n})`).join(", ")}${f.confidence === "low" ? ", low confidence" : ""}`;
}
export const fitLabel = (fit: CalibrationFit): string => ((fit as unknown as FitObj).status === REJECTED ? REJECTED_LABEL : basisLabel(fit));

function basisByProperty(fit: CalibrationFit): Record<string, string> {
  const f = fit as unknown as FitObj;
  return Object.fromEntries(PROPERTIES.map((k) => [k, f[k] ? `calibrated (${f[k]!.n} test${f[k]!.n !== 1 ? "s" : ""})` : BASIS_ESTIMATE]));
}

/** core calibration.plausibility: why a record must not be applied ([] = it may). */
export function plausibility(rec: { fit: CalibrationFit }, base: StudyMaterial): string[] {
  const f = rec.fit as unknown as FitObj;
  if (f.status === REJECTED) return [...(f.reasons ?? ["rejected"])];
  const bounds = PLAUSIBLE[materialClass(base.name)];
  const out: string[] = [];
  for (const [k, ref] of [["e", base.e_mpa], ["strength", base.yield_mpa], ["e_z", base.e_mpa], ["z_strength", base.yield_mpa]] as const) {
    const v = f[k]?.value_mpa;
    if (v !== undefined && !(bounds[k][0] <= v / ref && v / ref <= bounds[k][1])) out.push(`fitted ${k} ${g(v)} MPa is ${g3(v / ref)}x the data sheet`);
  }
  const e = f.e?.value_mpa || base.e_mpa;
  const ez = f.e_z?.value_mpa;
  if (ez !== undefined && ez / e > E_Z_MAX_RATIO) out.push(`E_z / E ${g3(ez / e)} > ${E_Z_MAX_RATIO}`);
  return out;
}

/** core calibration.apply: the material with the fitted values. */
export function applyFit(m: StudyMaterial, rec: { id: string; printer: string; fit: CalibrationFit; updated_at?: number | null }): StudyMaterial {
  const f = rec.fit as unknown as FitObj;
  const kw: Obj = {};
  if (f.e && !(f.e.method ?? "").startsWith("secant")) kw.e_mpa = f.e.value_mpa;
  if (f.e_z) kw.e_z_mpa = f.e_z.value_mpa;
  if (f.strength) kw.yield_mpa = f.strength.value_mpa;
  if (f.z_strength) kw.z_strength_mpa = f.z_strength.value_mpa;
  if (typeof f.knockdown === "number") kw.knockdown = f.knockdown;
  const label = basisLabel(rec.fit);
  const by = basisByProperty(rec.fit);
  const notes: string[] = [];
  if (!("e_mpa" in kw)) {
    const sec = f.e_secant?.mean;
    if (sec) notes.push(`E stays the data sheet value; the tensile secant modulus ${g(sec)} MPa is a lower bound only and is not applied`);
    by.e = BASIS_ESTIMATE;
  }
  const out = { ...m, ...kw, basis: label, basis_by_property: by, source: `${m.source}; ${label} on ${rec.printer}`,
    calibration: { id: rec.id, printer: rec.printer, n_tests: f.n_tests ?? 0, confidence: f.confidence, calibrated: PROPERTIES.filter((k) => f[k] && (k !== "e" || "e_mpa" in kw)), notes, updated_at: rec.updated_at ?? null } } as Obj;
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

/** The saved record of `material` on `printer` (a study uses it only when plausible: see plausibility). */
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
        const f = fit as unknown as { status: string; status_label: string; reasons: string[]; outliers: unknown[] };
        return {
          fit, material: applyFit(m, { id: "cal_preview", printer: b.printer, fit, updated_at: null }), datasheet: m, basis: fitLabel(fit),
          status: f.status, status_label: f.status_label, reasons: f.reasons, outliers: f.outliers, applicable: f.status === "ok", saved: false,
        };
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
          note: b.note, basis: fitLabel(fit), status: (fit as { status?: string }).status ?? "ok",
          status_label: (fit as { status_label?: string }).status_label ?? "ok", reasons: (fit as { reasons?: string[] }).reasons ?? [], tests: dumpTests(b), fit,
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

// ---- solvers (S2: core sim/cfd/__init__.py openfoam_rows, dumped for each install state)

/** "missing": a container runtime, no image (the install button); "daemon_down": Docker / OrbStack
 *  not running; "no_runtime": neither (a link to OrbStack on the runtime row). */
export type OpenFoamState = "ok" | "missing" | "daemon_down" | "no_runtime";

/** The two OpenFOAM rows of /sims/solvers cfd: the container runtime, then the pinned image. */
export function openFoamRows(state: OpenFoamState): Obj[] {
  return structuredClone(REAL_S2.rows[state === "missing" ? "image_missing" as OpenFoamState : state]);
}

export function withOpenFoam(cfd: Obj[] | undefined, state: OpenFoamState): Obj[] {
  return [...(cfd ?? []).filter((r) => !/^openfoam/.test(String(r.id))), ...openFoamRows(state)];
}

const OPENFOAM_WHY: Record<OpenFoamState, string | null> = {
  ok: null, missing: "the OpenFOAM image is not pulled (System check)", daemon_down: "Docker / OrbStack is not running", no_runtime: "no Docker or OrbStack",
};

// ---- results

const nlOf = (settings: Obj): Obj | null => (isObj(settings.nonlinear) ? settings.nonlinear : null);
const list = (v: unknown): Obj[] => (Array.isArray(v) ? v.filter(isObj) : []);

/** core nonlinear.effects / model_error_band / describe for these settings. */
function nlEffects(nl: Obj): { effects: string[]; band: number; description: string } {
  const contacts = list(nl.contacts);
  const out: string[] = [];
  if (nl.large_deflection === true) out.push("large_deflection");
  if (nl.plasticity === true) out.push("plasticity");
  const nodeToFace = contacts.some((c) => (c.kind ?? "contact") === "contact");
  if (list(nl.rigid_supports).length) out.push(nodeToFace ? "contact_pairs" : "contact");
  if (nodeToFace) out.push("contact_pairs");
  if (contacts.some((c) => c.kind === "bonded")) out.push("bonded");
  const effects = [...new Set(out)];
  const BAND: Record<string, number> = { large_deflection: 0.05, plasticity: 0.1, contact: 0.15, contact_pairs: 0.15, bonded: 0.1 };
  const NAME: Record<string, string> = { large_deflection: "large deflection", plasticity: "plasticity", contact: "contact", contact_pairs: "contact", bonded: "bonded faces" };
  return { effects, band: Math.max(0, ...effects.map((e) => BAND[e])), description: [...new Set(effects.map((e) => NAME[e]))].join(" + ") };
}

/** The real record a study's finish starts from: S1's nonlinear studies (large deflection /
 *  plasticity, or contact only), S3's static study when it asks for anisotropy or fatigue, S2's
 *  OpenFOAM wind study when OpenFOAM is ready (else the built-in record, as the core falls back). */
export function p6Base(type: string, settings: Obj, openfoam: OpenFoamState = "ok"): StudyRecord | null {
  const mat = isObj(settings.material) ? settings.material : {};
  const nl = nlOf(settings);
  if (type === "static" && nl) return nl.plasticity === true || nl.large_deflection === true ? REAL_S1.nonlinear : REAL_S1.contact;
  if (type === "static" && (isObj(settings.fatigue) || isObj(mat.anisotropy))) return REAL_S3.static;
  if (type === "wind" && settings.solver === "openfoam" && openfoam === "ok") return REAL_S2.wind;
  return null;
}

const S3_RESULT_KEYS = ["fatigue", "fatigue_safety_factor", "cycles_to_failure", "safety_factor_von_mises", "max_layer_tension_mpa", "max_layer_tension_at", "allowable_z_mpa", "layer_safety_factor", "governing_failure"];
const S3_METRICS = new Set(["layer_safety_factor", "fatigue_safety_factor", "cycles_to_failure"]);

/** A finished study, with what the Plan 6 core adds for these settings. */
export function p6Finish(rec: StudyRecord, type: string, settings: Obj, cal: CalState, openfoam: OpenFoamState = "ok"): void {
  const card = rec.card;
  if (!card) return;
  let res = { ...((rec.results ?? {}) as Obj) };
  const notes = [...card.notes];
  let metrics = [...card.metrics];
  const mat = isObj(settings.material) ? settings.material : {};
  const nl = type === "static" ? nlOf(settings) : null;
  // a nonlinear study that also asks for S3's anisotropy / fatigue: S3's blocks from its real record
  if (nl && (isObj(settings.fatigue) || isObj(mat.anisotropy))) {
    const s3 = (REAL_S3.static.results ?? {}) as Obj;
    res = { ...res, ...Object.fromEntries(S3_RESULT_KEYS.filter((k) => k in s3).map((k) => [k, structuredClone(s3[k])])) };
    metrics = [...metrics, ...structuredClone(REAL_S3.static.card!.metrics.filter((m) => S3_METRICS.has(m.key)))];
    if (isObj(mat.anisotropy) && isObj(REAL_S3.static.material)) rec.material = structuredClone(REAL_S3.static.material);
  }
  // S3: drop what these settings did not ask for
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
  // S3: a plausible saved calibration of this material and printer applies (core studies.resolve_material)
  const name = typeof mat.name === "string" ? mat.name : "PLA";
  const c = mat.calibrated === false ? undefined : calibrationOf(cal, name, typeof mat.printer === "string" ? mat.printer : null);
  if (c && isObj(rec.material) && plausibility(c, rec.material as unknown as StudyMaterial).length === 0) {
    const m = applyFit(rec.material as unknown as StudyMaterial, c) as unknown as Obj;
    rec.material = m as unknown as StudyRecord["material"];
    const by = (m.basis_by_property ?? {}) as Record<string, string>;
    Object.assign(card as Obj, { material_basis: c.basis, material_basis_by_property: by });
    const rest = Object.entries(by).filter(([, v]) => !v.startsWith("calibrated")).map(([k]) => k);
    rec.caveats = [...rec.caveats, `Material ${c.basis} on printer ${c.printer} (confidence ${c.fit.confidence}): fitted to the user's own tests${rest.length ? `; ${rest.join(", ")}: datasheet / estimate` : ""}.`];
  }
  // S2: OpenFOAM requested but not ready: the built-in solver ran, and says so (core studies._run_wind)
  if (type === "wind" && settings.solver === "openfoam" && openfoam !== "ok") {
    rec.warnings = [`OpenFOAM was requested but is unavailable (${OPENFOAM_WHY[openfoam]}); the built-in solver was used instead`, ...rec.warnings];
    card.warnings = [...rec.warnings];
  }
  // S1: the real nonlinear block, shaped to these settings (core sim/static.py results.nonlinear)
  if (nl && isObj(res.nonlinear)) {
    const { effects, band, description } = nlEffects(nl);
    const contacts = list(nl.contacts).length + list(nl.rigid_supports).length;
    const plastic = nl.plasticity === true;
    const cr = (REAL_S1.contact.results ?? {}) as Obj;
    const pr = (REAL_S1.nonlinear.results ?? {}) as Obj;
    const peStrain = plastic ? (pr.max_plastic_strain as number | null) : null;
    const pressure = contacts ? (cr.max_contact_pressure_mpa as number | null) : null;
    res.nonlinear = {
      ...(res.nonlinear as Obj), effects, description, large_deflection: nl.large_deflection === true, plasticity: plastic,
      contact_pairs: contacts, increments: typeof nl.increments === "number" ? nl.increments : 10,
      max_plastic_strain: peStrain, max_contact_pressure_mpa: pressure,
      tangent_estimated: plastic && nl.tangent_modulus_mpa == null,
    };
    Object.assign(res, { max_plastic_strain: peStrain, max_contact_pressure_mpa: pressure, model_error_band: band });
    metrics = metrics.filter((m) => m.key !== "max_plastic_strain" && m.key !== "max_contact_pressure_mpa");
    const at = metrics.findIndex((m) => m.key === "load_fraction");
    const extra = [
      ...(peStrain !== null ? [{ key: "max_plastic_strain", label: "max plastic strain", unit: "", value: peStrain }] : []),
      ...(pressure !== null ? [{ key: "max_contact_pressure_mpa", label: "max contact pressure", unit: "MPa", value: pressure }] : []),
    ];
    metrics.splice(at >= 0 ? at + 1 : metrics.length, 0, ...extra);
    Object.assign(card, { fidelity_class: "fem_nonlinear", fidelity_note: `nonlinear (${description}) · ±${Math.round(100 * band)}% (validated)` });
    rec.fidelity_class = "fem_nonlinear";
  }
  rec.results = res;
  card.notes = notes;
  card.metrics = metrics;
}

/** Probe values: the real S3 channels on an S3 record, the real S1 channels on a nonlinear one. */
export function p6Probe(rec: StudyRecord, probe: Obj): Obj {
  const r = isObj(rec.results) ? rec.results : {};
  let out = probe;
  const add = (src: Obj, only?: string) => {
    const v = (src.values ?? {}) as Obj; const u = (src.units ?? {}) as Obj;
    const pick = (o: Obj) => (only ? (only in o ? { [only]: o[only] } : {}) : o);
    out = { ...out, values: { ...(out.values as Obj), ...pick(v) }, units: { ...(out.units as Obj), ...pick(u) } };
  };
  if (isObj(r.fatigue) || "layer_safety_factor" in r) add(REAL_S3.probe);
  const nl = r.nonlinear;
  if (isObj(nl) && isObj(out.values)) {
    if (nl.plasticity) add(REAL_S1.probe, "plastic_strain");
    if (typeof nl.contact_pairs === "number" && nl.contact_pairs > 0) add(REAL_S1.contactProbe, "contact_pressure");
  }
  return out;
}
