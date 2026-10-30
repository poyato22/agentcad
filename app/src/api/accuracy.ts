// Plan 6 (simulation accuracy) shapes the app reads: nonlinear / contact stress (S1), OpenFOAM
// airflow (S2), material calibration, anisotropy and fatigue (S3). The core tasks ran in parallel
// with the app (S4), so every field here is an ASSUMED shape until integration checks it against
// the real core: each one is listed in the S4 report. The schemas are loose (unknown fields pass
// through) and every field the UI reads is optional or nullable, so a core that differs degrades
// to "not shown", never a crash. Everything here is core data: render it as text.
import { z } from "zod";
import type { StudyMaterial } from "./studies";

const num = z.number().nullable();
const vec3 = z.tuple([z.number(), z.number(), z.number()]);

// ---- S3: calibration (GET/POST /sims/calibrations, POST /sims/calibrations/{id}/apply, DELETE)

/** A tensile bar pulled to break: break force over the cross-section gives the strength. */
export const tensileTestSchema = z.looseObject({
  kind: z.literal("tensile"), break_n: z.number(), width_mm: z.number(), thickness_mm: z.number(),
  elongation_pct: num.optional(), note: z.string().optional(),
});
/** A three-point bend: load and mid-span deflection give the modulus (E = F L³ / (4 b h³ δ)). */
export const bendTestSchema = z.looseObject({
  kind: z.literal("bend"), span_mm: z.number(), width_mm: z.number(), thickness_mm: z.number(),
  load_n: z.number(), deflection_mm: z.number(), note: z.string().optional(),
});
/** A known load on a real part, its deflection measured at a probe point of a finished study. */
export const partLoadTestSchema = z.looseObject({
  kind: z.literal("part_load"), sim_id: z.string(), probe: vec3.nullable().optional(), load_n: z.number(),
  deflection_mm: z.number(), note: z.string().optional(),
});
export const calibrationTestSchema = z.discriminatedUnion("kind", [tensileTestSchema, bendTestSchema, partLoadTestSchema]);
export type TensileTest = z.infer<typeof tensileTestSchema>;
export type BendTest = z.infer<typeof bendTestSchema>;
export type PartLoadTest = z.infer<typeof partLoadTestSchema>;
export type CalibrationTest = z.infer<typeof calibrationTestSchema>;
export type CalibrationTestKind = CalibrationTest["kind"];

export const CONFIDENCE = ["low", "medium", "high"] as const;
export type Confidence = (typeof CONFIDENCE)[number];

/** One test's own reading: the modulus and / or strength it implies, and how far it is from the fit. */
export const perTestSchema = z.looseObject({
  index: z.number(), kind: z.string(), e_mpa: num.optional(), strength_mpa: num.optional(),
  residual_pct: num.optional(), used: z.boolean().optional(),
});
export type PerTest = z.infer<typeof perTestSchema>;

export const calibrationFitSchema = z.looseObject({
  e_mpa: num, yield_mpa: num, uts_mpa: num.optional(), knockdown: num.optional(),
  n: z.number(), scatter_pct: num, confidence: z.enum(CONFIDENCE),
  per_test: z.array(perTestSchema).default([]),
  /** The material's values before calibration, for the comparison. */
  datasheet: z.looseObject({ e_mpa: num.optional(), yield_mpa: num.optional() }).nullish(),
  warnings: z.array(z.string()).default([]),
});
export type CalibrationFit = z.infer<typeof calibrationFitSchema>;

export const calibrationSchema = z.looseObject({
  id: z.string(), material: z.string(), printer_profile: z.string(),
  tests: z.array(calibrationTestSchema), fit: calibrationFitSchema.nullable(),
  applied: z.boolean(), created_at: z.number(), updated_at: z.number(),
});
export type Calibration = z.infer<typeof calibrationSchema>;

/** POST /sims/calibrations: the fit is computed by the core and returned with the record. */
export interface CalibrationBody { material: string; printer_profile: string; tests: CalibrationTest[] }

export const calibrationApplySchema = z.looseObject({ calibration: calibrationSchema, material: z.looseObject({ name: z.string() }) });

// ---- S3: a material's provenance, anisotropy and S-N curve (extra fields on GET /sims/materials rows)

export const materialCalibrationSchema = z.looseObject({
  id: z.string().optional(), n_tests: z.number(), confidence: z.enum(CONFIDENCE).optional(),
  printer_profile: z.string().nullish(), fitted_at: z.number().nullish(),
});
export const orthotropicSchema = z.looseObject({
  e_xy_mpa: z.number(), e_z_mpa: z.number(), strength_xy_mpa: num.optional(), strength_z_mpa: num.optional(),
  g_xz_mpa: num.optional(), nu_xy: num.optional(), nu_xz: num.optional(),
});
export const snCurveSchema = z.looseObject({
  /** Basquin: σa = sf · (2N)^b. */
  sf_mpa: z.number(), b: z.number(), source: z.string().optional(), label: z.string().optional(),
  endurance_cycles: num.optional(),
});
export type SnCurve = z.infer<typeof snCurveSchema>;

export interface Provenance {
  calibrated: boolean;
  /** "calibrated (3 tests)" or "datasheet / estimate": shown next to the material everywhere. */
  label: string;
  n: number;
  confidence: Confidence | null;
}

/** A material's calibration badge, from the core's label or its calibration block. */
export function provenance(m: Pick<StudyMaterial, "name"> & Record<string, unknown>): Provenance {
  const cal = materialCalibrationSchema.safeParse(m.calibration);
  if (cal.success && cal.data.n_tests > 0) {
    const n = cal.data.n_tests;
    return { calibrated: true, label: `calibrated (${n} test${n === 1 ? "" : "s"})`, n, confidence: cal.data.confidence ?? null };
  }
  return { calibrated: false, label: "datasheet / estimate", n: 0, confidence: null };
}

export function orthotropic(m: Record<string, unknown>): z.infer<typeof orthotropicSchema> | null {
  const o = orthotropicSchema.safeParse(m.orthotropic);
  return o.success ? o.data : null;
}

export function snCurve(m: Record<string, unknown>): SnCurve | null {
  const s = snCurveSchema.safeParse(m.sn);
  return s.success ? s.data : null;
}

// ---- S3: fatigue results (results.fatigue on a static study with settings.fatigue)

export const fatigueResultSchema = z.looseObject({
  /** null: below the curve's endurance (no failure within the curve). */
  cycles_to_failure: num,
  required_cycles: z.number(),
  safety_factor: num,
  sigma_a_mpa: num.optional(), sigma_m_mpa: num.optional(),
  /** Goodman-equivalent fully reversed amplitude at the hotspot. */
  sigma_ar_mpa: num.optional(),
  hotspot: vec3.nullish(),
  sn: snCurveSchema.nullish(),
  mean_correction: z.string().nullish(),
  label: z.string().default("fatigue estimate"),
});
export type FatigueResult = z.infer<typeof fatigueResultSchema>;

export function fatigueOf(results: unknown): FatigueResult | null {
  const r = typeof results === "object" && results !== null ? (results as Record<string, unknown>).fatigue : null;
  const f = fatigueResultSchema.safeParse(r);
  return f.success ? f.data : null;
}

// ---- S1: nonlinear results (results.nonlinear) and the new channels

export const nonlinearResultSchema = z.looseObject({
  converged: z.boolean(), increments: z.number().optional(), load_reached_pct: num.optional(),
  large_deflection: z.boolean().optional(), plasticity: z.boolean().optional(), contact_pairs: z.number().optional(),
  max_plastic_strain: num.optional(), max_contact_pressure_mpa: num.optional(),
});
export type NonlinearResult = z.infer<typeof nonlinearResultSchema>;

export function nonlinearOf(results: unknown): NonlinearResult | null {
  const r = typeof results === "object" && results !== null ? (results as Record<string, unknown>).nonlinear : null;
  const n = nonlinearResultSchema.safeParse(r);
  return n.success ? n.data : null;
}

/** The new result channels (GLB extras, probe values) and their words. */
export const NEW_CHANNELS: Record<string, string> = {
  plastic_strain: "plastic strain (PEEQ)",
  contact_pressure: "contact pressure",
  fatigue_life: "fatigue life (log10 cycles)",
};

// ---- S2: the OpenFOAM row of GET /sims/solvers cfd (system-check row shape)

export const OPENFOAM_ID_RE = /^openfoam/;
