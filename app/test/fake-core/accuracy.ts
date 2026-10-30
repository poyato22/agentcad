// Fake core: Plan 6 (simulation accuracy) on top of the real Plan 5 shapes. S1-S3 ran in parallel
// with the app (S4), so nothing here was dumped from a real core: every shape is an ASSUMPTION,
// mirrored in src/api/accuracy.ts and listed in the S4 report for integration to check.
//   S1  static settings.nonlinear {large_deflection, plasticity, steps, contact: [faces | rigid |
//       instances]}; results.nonlinear; channels plastic_strain / contact_pressure (probe values).
//   S2  wind settings.solver "openfoam"; a /sims/solvers cfd row "openfoam" whose install fix is
//       POST /system/install/openfoam; the result's fidelity "OpenFOAM k-ω SST · ±10% (validated)".
//   S3  settings.print_direction (every FEM type), static settings.fatigue {cycles, cycle,
//       min_ratio, mean_correction}; results.fatigue; materials carry sn / orthotropic /
//       calibration; /sims/calibrations (GET, POST fit, POST {id}/apply, DELETE {id}).
import { randomBytes } from "node:crypto";
import { calibrationTestSchema, type Calibration, type CalibrationTest } from "../../src/api/accuracy";
import type { StudyMaterial, StudyRecord } from "../../src/api/studies";
import { ampMean, confidenceOf, goodman, implied, meanCv, snCycles, snStress } from "../../src/ui/pages/studies/accuracy/model";
import { asBody, HttpError, mises, RAW, type Route } from "./server";

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const hex = (n: number) => randomBytes(n).toString("hex");
const now = () => Date.now() / 1000;

// ---- schemas (pydantic-style JSON schema, like core sim/settings.py emits)

const DIR_NAMES = ["+x", "-x", "+y", "-y", "+z", "-z"];
const coord = { maximum: 1e6, minimum: -1e6, type: "number" };
const FACE = { description: "a preset (bottom, top, x-min, x-max, y-min, y-max, z-min, z-max) or a face id like 'f3' (mirror_faces)", type: "string" };
const FRICTION = { default: 0.2, minimum: 0, maximum: 2, description: "friction coefficient μ (0 = frictionless)", type: "number" };

export const P6_DEFS: Obj = {
  ContactFaces: {
    additionalProperties: false, title: "ContactFaces", type: "object", required: ["master", "slave"],
    properties: {
      kind: { const: "faces", default: "faces", type: "string" },
      master: { ...FACE, title: "Master" }, slave: { ...FACE, title: "Slave" }, friction: FRICTION,
    },
  },
  ContactRigid: {
    additionalProperties: false, title: "ContactRigid", type: "object", required: ["kind", "face"],
    properties: {
      kind: { const: "rigid", type: "string" }, face: { ...FACE, title: "Face", description: "the face resting on a rigid support" }, friction: FRICTION,
    },
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
  FatigueSettings: {
    additionalProperties: false, title: "FatigueSettings", type: "object",
    properties: {
      cycles: { default: 1e6, minimum: 1, maximum: 1e12, type: "number", description: "required life", unit: "cycles" },
      cycle: { default: "fully_reversed", enum: ["fully_reversed", "zero_to_max", "min_max"], type: "string", description: "how the load swings each cycle" },
      min_ratio: { default: 0, minimum: -1, maximum: 1, type: "number", description: "R = min load / max load (min_max only)" },
      mean_correction: { default: "goodman", enum: ["goodman", "none"], type: "string", description: "mean-stress correction" },
    },
  },
};

const PRINT_DIRECTION = {
  anyOf: [{ enum: DIR_NAMES, type: "string" }, { maxItems: 3, minItems: 3, prefixItems: [coord, coord, coord], type: "array" }, { type: "null" }],
  default: null, title: "Print Direction",
  description: "build direction (the axis the layers stack along) in part coordinates; null = isotropic",
};

/** The real GET /sims/types plus the Plan 6 settings. */
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
    t.schema.$defs = { ...(t.schema.$defs as Obj), ...P6_DEFS };
    props.print_direction = PRINT_DIRECTION;
    if (t.id === "static") {
      props.nonlinear = { anyOf: [{ $ref: "#/$defs/NonlinearSettings" }, { type: "null" }], default: null, description: "large deflection, plasticity and contact (CalculiX NLGEOM); null = linear" };
      props.fatigue = { anyOf: [{ $ref: "#/$defs/FatigueSettings" }, { type: "null" }], default: null, description: "cyclic loading: life at the hotspot (S-N, estimate)" };
      t.channels = [...(t.channels ?? []), "plastic_strain", "contact_pressure"];
    }
    return t;
  });
}

// ---- materials

/** The keys p6Material adds to a material row. */
export const P6_MATERIAL_KEYS = ["sn", "orthotropic", "calibration"] as const;

/** S-N (Basquin) and orthotropic data the fake gives the built-in materials. */
const SN: Record<string, { sf_mpa: number; b: number }> = {
  PLA: { sf_mpa: 76, b: -0.085 }, PETG: { sf_mpa: 62, b: -0.08 }, ABS: { sf_mpa: 52, b: -0.09 }, ASA: { sf_mpa: 58, b: -0.09 }, "PA-CF": { sf_mpa: 105, b: -0.075 },
};

export function p6Material(m: StudyMaterial, cal: Calibration | undefined): StudyMaterial {
  const sn = SN[m.name] ?? { sf_mpa: m.yield_mpa * 1.4, b: -0.09 };
  const out: Obj = {
    ...m,
    sn: { ...sn, source: SN[m.name] ? "estimate" : "estimate", label: "S-N estimate (Basquin, from the datasheet strength)" },
    orthotropic: m.custom ? null : { e_xy_mpa: m.e_mpa, e_z_mpa: Math.round(m.e_mpa * 0.8), strength_xy_mpa: m.yield_mpa, strength_z_mpa: Math.round(m.yield_mpa * 0.6), nu_xy: m.nu, nu_xz: m.nu },
    calibration: null,
  };
  if (cal?.applied && cal.fit) {
    out.calibration = { id: cal.id, n_tests: cal.fit.n, confidence: cal.fit.confidence, printer_profile: cal.printer_profile, fitted_at: cal.updated_at };
    if (cal.fit.e_mpa !== null) out.e_mpa = Math.round(cal.fit.e_mpa);
    if (cal.fit.yield_mpa !== null) out.yield_mpa = Math.round(cal.fit.yield_mpa * 10) / 10;
    out.source = `calibrated: ${cal.fit.n} tests on ${cal.printer_profile}`;
  }
  return out as StudyMaterial;
}

// ---- calibration

export interface CalState { list: Calibration[] }
export const calStateFrom = (): CalState => ({ list: [] });

export interface CalDeps {
  cal(): CalState;
  material(name: string): StudyMaterial | undefined;
  record(simId: string): StudyRecord | undefined;
}

/** The least-squares fit the core makes, simplified: means, scatter (CV) and a confidence. */
export function fakeFit(tests: readonly CalibrationTest[], m: StudyMaterial | undefined, rec: (id: string) => StudyRecord | undefined): Calibration["fit"] {
  const per = tests.map((t, index) => {
    let { e_mpa, strength_mpa } = implied(t);
    if (t.kind === "part_load" && m) {
      // inverse comparison: the study's deflection at this load over the measured one scales E
      const r = rec(t.sim_id);
      const res = (r?.results ?? {}) as Obj;
      const settings = (r?.settings ?? {}) as Obj;
      const cases = Array.isArray(settings.load_cases) ? settings.load_cases as { loads?: { newtons?: number }[] }[] : [];
      const studyLoad = cases[0]?.loads?.reduce((s, l) => s + Math.abs(l.newtons ?? 0), 0) ?? 0;
      const disp = typeof res.max_displacement_mm === "number" ? res.max_displacement_mm : null;
      if (disp && studyLoad > 0 && t.deflection_mm > 0) e_mpa = m.e_mpa * ((disp * t.load_n) / studyLoad) / t.deflection_mm;
      strength_mpa = null;
    }
    return { index, kind: t.kind, e_mpa, strength_mpa };
  });
  const e = meanCv(per.map((p) => p.e_mpa));
  const s = meanCv(per.map((p) => p.strength_mpa));
  const scatter = e?.cvPct ?? s?.cvPct ?? null;
  const warnings: string[] = [];
  if (!e) warnings.push("no stiffness test (bend, part load or elongation): E stays the datasheet value");
  if (!s) warnings.push("no tensile test: the strength stays the datasheet value");
  if (tests.length < 3) warnings.push("fewer than 3 tests: the scatter is not known well");
  return {
    e_mpa: e?.mean ?? null, yield_mpa: s?.mean ?? null, uts_mpa: s?.mean ?? null,
    knockdown: s && m ? s.mean / m.yield_mpa : null,
    n: tests.length, scatter_pct: scatter, confidence: confidenceOf(tests.length, scatter),
    per_test: per.map((p) => {
      const ref = p.e_mpa !== null ? e?.mean : p.strength_mpa !== null ? s?.mean : null;
      const v = p.e_mpa ?? p.strength_mpa;
      return { ...p, residual_pct: ref && v !== null ? ((v - ref) / ref) * 100 : null, used: v !== null };
    }),
    datasheet: m ? { e_mpa: m.e_mpa, yield_mpa: m.yield_mpa } : null,
    warnings,
  };
}

export function calibrationRoutes(d: CalDeps): Route[] {
  const find = (id: string) => {
    const c = d.cal().list.find((x) => x.id === id);
    if (!c) throw mises("not_found", `no calibration ${id}`);
    return c;
  };
  return [
    { method: "GET", re: /^\/sims\/calibrations$/, h: () => structuredClone(d.cal().list) },
    {
      method: "POST", re: /^\/sims\/calibrations$/, h: (_m, _q, raw, _r, res) => {
        const b = asBody(raw);
        const material = typeof b.material === "string" ? b.material.trim() : "";
        const printer = typeof b.printer_profile === "string" ? b.printer_profile.trim() : "";
        const m = d.material(material);
        if (!m) throw mises("invalid_input", `unknown material '${material}'`, "calibrate a built-in or saved material");
        if (!printer || printer.length > 60) throw mises("invalid_input", "a printer profile of 1-60 characters", "the printer the coupons were printed on");
        if (!Array.isArray(b.tests) || b.tests.length === 0 || b.tests.length > 50) throw mises("invalid_input", "1-50 tests");
        const tests = b.tests.map((t, i) => {
          const p = calibrationTestSchema.safeParse(t);
          if (!p.success) throw new HttpError(422, { detail: [{ type: "value_error", loc: ["body", "tests", i], msg: p.error.issues[0]?.message ?? "bad test", input: t }] });
          if (p.data.kind === "part_load" && !d.record(p.data.sim_id)) throw mises("invalid_input", `test ${i + 1}: no study ${p.data.sim_id}`, "pick a finished study of the loaded part");
          return p.data;
        });
        const st = d.cal();
        const old = st.list.find((c) => c.material.toLowerCase() === material.toLowerCase() && c.printer_profile === printer);
        const c: Calibration = {
          id: old?.id ?? `cal_${hex(5)}`, material: m.name, printer_profile: printer, tests, applied: false,
          fit: fakeFit(tests, m, (id) => d.record(id)), created_at: old?.created_at ?? now(), updated_at: now(),
        };
        st.list = [...st.list.filter((x) => x.id !== c.id), c];
        const text = JSON.stringify(c);
        res.writeHead(201, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(text) });
        res.end(text);
        return RAW;
      },
    },
    {
      method: "POST", re: /^\/sims\/calibrations\/([^/]+)\/apply$/, h: (m) => {
        const c = find(decodeURIComponent(m[1]));
        if (!c.fit || (c.fit.e_mpa === null && c.fit.yield_mpa === null)) throw mises("invalid_input", "nothing fitted to apply", "add a tensile, bend or part load test");
        // one applied calibration per material
        for (const x of d.cal().list) if (x.material === c.material) x.applied = false;
        c.applied = true;
        c.updated_at = now();
        const mat = d.material(c.material);
        return { calibration: structuredClone(c), material: mat ? p6Material(mat, c) : { name: c.material } };
      },
    },
    {
      method: "DELETE", re: /^\/sims\/calibrations\/([^/]+)$/, h: (m) => {
        const c = find(decodeURIComponent(m[1]));
        d.cal().list = d.cal().list.filter((x) => x.id !== c.id);
        return { deleted: c.id };
      },
    },
  ];
}

// ---- solvers

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

const sig = (v: number) => Number(v.toPrecision(4));

/** A finished study, with what the Plan 6 core adds for these settings. */
export function p6Finish(rec: StudyRecord, type: string, settings: Obj): void {
  const card = rec.card;
  if (!card) return;
  const res = { ...((rec.results ?? {}) as Obj) };
  const notes = [...card.notes];
  const metrics = [...card.metrics];
  if (type === "wind" && settings.solver === "openfoam") {
    Object.assign(rec, { fidelity_class: "cfd_openfoam", solver: { name: "OpenFOAM", version: "v2406", procedure: "simpleFoam, k-ω SST", method: "snappyHexMesh around the STL" } });
    if (isObj(rec.mesh)) delete (rec.mesh as Obj).lattice;
    Object.assign(card, { fidelity_class: "cfd_openfoam", fidelity_note: "OpenFOAM k-ω SST · ±10% (validated)" });
    for (const mt of metrics) if (mt.key === "error_band_pct") mt.value = 10;
    res.model_error_band = 0.1;
    res.solver_used = "openfoam";
  }
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
  const fat = isObj(settings.fatigue) ? settings.fatigue : null;
  if (type === "static" && fat && typeof res.max_von_mises_mpa === "number") {
    const mat = (rec.material ?? { name: "PLA" }) as Obj;
    const name = String(mat.name ?? "PLA");
    const sn = { ...(SN[name] ?? SN.PLA), source: "estimate", label: "S-N estimate (Basquin, from the datasheet strength)" };
    const uts = typeof mat.yield_mpa === "number" ? mat.yield_mpa : 51;
    const mode = String(fat.cycle ?? "fully_reversed");
    const { amp, mean } = ampMean(res.max_von_mises_mpa, mode, typeof fat.min_ratio === "number" ? fat.min_ratio : 0);
    const ar = fat.mean_correction === "none" ? amp : goodman(amp, mean, uts) ?? amp;
    const required = typeof fat.cycles === "number" ? fat.cycles : 1e6;
    const nf = snCycles(sn, ar);
    const sf = ar > 0 ? snStress(sn, required) / ar : null;
    res.fatigue = {
      cycles_to_failure: nf !== null && nf > 1e12 ? null : nf, required_cycles: required, safety_factor: sf,
      sigma_a_mpa: sig(amp), sigma_m_mpa: sig(mean), sigma_ar_mpa: sig(ar), hotspot: res.max_von_mises_at ?? null,
      sn, mean_correction: fat.mean_correction ?? "goodman", label: "fatigue estimate",
    };
    metrics.push({ key: "fatigue_cycles", label: "cycles to failure", unit: "cycles", value: nf });
    metrics.push({ key: "fatigue_sf", label: "fatigue safety factor", unit: "", value: sf });
    notes.push("fatigue estimate: S-N estimate with Goodman mean-stress correction, at the hotspot");
    if (sf !== null && sf < 1) card.verdict = "fail";
  }
  if (settings.print_direction !== null && settings.print_direction !== undefined && isObj(rec.material)) {
    const dir = Array.isArray(settings.print_direction) ? `[${settings.print_direction.join(", ")}]` : String(settings.print_direction);
    (rec.material as Obj).model = `orthotropic FDM (engineering constants), layers along ${dir}; Z strength across layers`;
    notes.push(`anisotropic: layers stack along ${dir}; tension across layers uses the Z strength`);
  }
  rec.results = res;
  card.notes = notes;
  card.metrics = metrics;
}

/** Probe values for the new channels on a nonlinear result. */
export function p6Probe(rec: StudyRecord, probe: Obj): Obj {
  const nl = isObj(rec.results) ? (rec.results as Obj).nonlinear : null;
  if (!isObj(nl) || !isObj(probe.values)) return probe;
  const values = { ...probe.values } as Obj;
  const units = { ...(isObj(probe.units) ? probe.units : {}) } as Obj;
  if (nl.plasticity) { values.plastic_strain = 0.0011; units.plastic_strain = ""; }
  if (typeof nl.contact_pairs === "number" && nl.contact_pairs > 0) { values.contact_pressure = 6.2; units.contact_pressure = "MPa"; }
  return { ...probe, values, units };
}
