// Plan 6 (simulation accuracy) shapes the app reads.
// - S3 (material calibration, anisotropy, fatigue): the REAL core's shapes (core sim/calibration.py,
//   sim/fatigue.py, sim/materials.py, sim/settings.py, server/sim_api.py; fixtures dumped by
//   core/scripts/dump_s3_fixtures.py → core/tests/fixtures/ui/s3_*).
// - S1 (nonlinear / contact) and S2 (OpenFOAM): still ASSUMED (those branches were not available
//   to S4); listed in the S4 report for S5 to check.
// The schemas are loose (unknown fields pass through) and every field the UI reads that the core
// may leave out is optional or nullable, so a differing core degrades to "not shown". Everything
// here is core data: render it as text.
import { z } from "zod";
import type { StudyMaterial } from "./studies";

const num = z.number().nullable();
const vec3 = z.tuple([z.number(), z.number(), z.number()]);

// ---- S3: calibration tests (core sim/calibration.py TensileTest / BendTest / PartTest)

export const ORIENTATIONS = ["xy", "z"] as const;
export type Orientation = (typeof ORIENTATIONS)[number];

export const tensileTestSchema = z.looseObject({
  force_n: z.number(), width_mm: num.optional(), thickness_mm: num.optional(), area_mm2: num.optional(),
  elongation_pct: num.optional(), orientation: z.enum(ORIENTATIONS).default("xy"), note: z.string().optional(),
});
export const bendTestSchema = z.looseObject({
  span_mm: z.number(), width_mm: z.number(), thickness_mm: z.number(), load_n: z.number(), deflection_mm: z.number(),
  break_load_n: num.optional(), orientation: z.enum(ORIENTATIONS).default("xy"), note: z.string().optional(),
});
export const partTestSchema = z.looseObject({
  sim_id: z.string(), point: vec3, load_n: z.number(), deflection_mm: z.number(),
  direction: vec3.nullable().optional(), study_load_n: num.optional(), note: z.string().optional(),
});
export type TensileTest = z.infer<typeof tensileTestSchema>;
export type BendTest = z.infer<typeof bendTestSchema>;
export type PartTest = z.infer<typeof partTestSchema>;
export type CalibrationTestKind = "tensile" | "bend" | "part";

/** POST /sims/calibrations/fit and POST /sims/calibrations (core CalibrationInput; extra keys are refused). */
export interface CalibrationBody {
  material: string; printer: string;
  tensile: TensileTest[]; bend: BendTest[]; part: PartTest[];
  note?: string;
}

/** Same as core calibration.KEY_RE (material and printer keys) and MAX_TESTS (per kind). */
export const CAL_KEY_RE = /^[A-Za-z0-9][A-Za-z0-9 ._+()-]{0,39}$/;
export const CAL_MAX_TESTS = 100;
export const DEFAULT_PRINTER = "default";

// ---- S3: the fit

export const CONFIDENCE = ["low", "medium", "high"] as const;
export type Confidence = (typeof CONFIDENCE)[number];

const statsShape = {
  n: z.number(), mean: z.number(), sd: num, cv: num, min: z.number(), max: z.number(),
};
/** A fitted modulus (E or E_z): least squares over the E tests; residuals in their order. */
export const modulusFitSchema = z.looseObject({
  ...statsShape, value_mpa: z.number(), residuals: z.array(z.number()).default([]), confidence: z.enum(CONFIDENCE),
  method: z.string().optional(),
});
/** A fitted strength: the mean, and the knock-down fitted to the scatter. */
export const strengthFitSchema = z.looseObject({
  ...statsShape, value_mpa: z.number(), knockdown: z.number(), knockdown_rule: z.string().optional(), confidence: z.enum(CONFIDENCE),
});
export const partEstimateSchema = z.looseObject({
  sim_id: z.string(), e_mpa: z.number(), e_study_mpa: z.number().optional(), predicted_deflection_mm: z.number().optional(),
  measured_deflection_mm: z.number().optional(), study_load_n: z.number().optional(),
});
export const calibrationFitSchema = z.looseObject({
  n_tests: z.number(),
  e: modulusFitSchema.optional(), e_z: modulusFitSchema.optional(),
  strength: strengthFitSchema.optional(), z_strength: strengthFitSchema.optional(),
  flexural_strength: z.looseObject(statsShape).optional(),
  knockdown: z.number().optional(), confidence: z.enum(CONFIDENCE), datasheet_ratio: z.number().optional(),
  part_tests: z.array(partEstimateSchema).optional(),
});
export type CalibrationFit = z.infer<typeof calibrationFitSchema>;

/** The material rows the calibration routes return (Material.to_dict()). */
const materialDict = z.looseObject({
  name: z.string(), e_mpa: z.number(), yield_mpa: z.number(), knockdown: z.number(),
  allowable_mpa: z.number().optional(), e_z_mpa: num.optional(), z_strength_mpa: num.optional(), basis: z.string().optional(),
});
export type MaterialDict = z.infer<typeof materialDict>;

/** POST /sims/calibrations/fit: a preview, nothing saved. */
export const calibrationPreviewSchema = z.looseObject({
  fit: calibrationFitSchema, material: materialDict, datasheet: materialDict, basis: z.string(), saved: z.boolean(),
});
export type CalibrationPreview = z.infer<typeof calibrationPreviewSchema>;

/** A saved calibration record (GET /sims/calibrations items; POST adds material_preview). Saving IS applying:
 *  a study uses the record of its material and printer (settings.material.printer, null = "default"). */
export const calibrationSchema = z.looseObject({
  id: z.string(), material: z.string(), printer: z.string(), created_at: z.number(), updated_at: z.number(),
  note: z.string().optional(), basis: z.string(),
  tests: z.looseObject({
    tensile: z.array(tensileTestSchema).default([]), bend: z.array(bendTestSchema).default([]), part: z.array(partTestSchema).default([]),
  }),
  fit: calibrationFitSchema, material_preview: materialDict.optional(),
});
export type Calibration = z.infer<typeof calibrationSchema>;

// ---- S3: a material's provenance and its anisotropy / S-N data (flat keys on material rows)

export interface Provenance {
  calibrated: boolean;
  /** "calibrated (3 tests)" or "datasheet / estimate": shown next to the material everywhere. */
  label: string;
  n: number;
  confidence: Confidence | null;
}

const ESTIMATE: Provenance = { calibrated: false, label: "datasheet / estimate", n: 0, confidence: null };

/** The calibration record that applies to a material on a printer (null printer = "default"), as the core picks it. */
export function calibrationFor(cals: readonly Calibration[], material: string | null | undefined, printer: string | null | undefined): Calibration | null {
  if (!material) return null;
  const p = (printer || DEFAULT_PRINTER).trim().toLowerCase();
  return cals.find((c) => c.material.toLowerCase() === material.trim().toLowerCase() && c.printer.toLowerCase() === p) ?? null;
}

/**
 * A material's badge. The core's own word wins (a study's resolved material carries `basis` and
 * `calibration`); a raw /sims/materials row is "datasheet / estimate" unless a saved calibration
 * applies to it on the chosen printer (pass `cal` from calibrationFor).
 */
export function provenance(m: Pick<StudyMaterial, "name"> & Record<string, unknown>, cal?: Calibration | null): Provenance {
  const c = m.calibration;
  if (typeof c === "object" && c !== null && typeof (c as { n_tests?: unknown }).n_tests === "number") {
    const n = (c as { n_tests: number }).n_tests;
    const conf = (c as { confidence?: unknown }).confidence;
    return { calibrated: true, label: typeof m.basis === "string" ? m.basis : `calibrated (${n} test${n === 1 ? "" : "s"})`, n, confidence: CONFIDENCE.includes(conf as Confidence) ? conf as Confidence : null };
  }
  if (cal) return { calibrated: true, label: cal.basis, n: cal.fit.n_tests, confidence: cal.fit.confidence };
  return ESTIMATE;
}

/** Between-layer data a material row carries (null: the core uses its typical ratio, an estimate). */
export function zData(m: Record<string, unknown>): { e_z_mpa: number | null; z_strength_mpa: number | null } {
  const n = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);
  return { e_z_mpa: n(m.e_z_mpa), z_strength_mpa: n(m.z_strength_mpa) };
}

// ---- S3: fatigue results (results.fatigue on a static study with settings.fatigue)

/** Basquin as the core writes it: σa = A · N^b (per cycle). */
export const snCurveSchema = z.looseObject({
  a_mpa: z.number(), b: z.number(), uts_mpa: z.number().optional(), basis: z.string().optional(), source: z.string().optional(),
});
export type SnCurve = z.infer<typeof snCurveSchema>;

export const fatigueResultSchema = z.looseObject({
  label: z.string().default("fatigue estimate"),
  cycles_required: z.number(),
  cycle: z.string().optional(), load_ratio: z.number().optional(),
  /** Capped at the core's N_CAP (1e12); `runout` says the cap was reached. */
  cycles_to_failure: z.number(),
  runout: z.boolean().default(false),
  safety_factor: num,
  hotspot_at: vec3.nullish(),
  governing: z.string().optional(),
  stress_amplitude_mpa: z.number().optional(), mean_stress_mpa: z.number().optional(),
  equivalent_amplitude_mpa: num.optional(), allowable_amplitude_mpa: z.number().optional(),
  sn_curve: snCurveSchema.nullish(),
  mean_stress_correction: z.string().optional(),
  case: z.string().optional(),
});
export type FatigueResult = z.infer<typeof fatigueResultSchema>;

export function fatigueOf(results: unknown): FatigueResult | null {
  const r = typeof results === "object" && results !== null ? (results as Record<string, unknown>).fatigue : null;
  const f = fatigueResultSchema.safeParse(r);
  return f.success ? f.data : null;
}

/** S3 orthotropic results: tension across the layers against the Z allowable. */
export const layerResultSchema = z.looseObject({
  layer_safety_factor: num, max_layer_tension_mpa: num.optional(), max_layer_tension_at: vec3.nullish(),
  allowable_z_mpa: num.optional(), safety_factor_von_mises: num.optional(), governing_failure: z.string().optional(),
});
export type LayerResult = z.infer<typeof layerResultSchema>;

export function layersOf(results: unknown): LayerResult | null {
  if (typeof results !== "object" || results === null || !("layer_safety_factor" in results)) return null;
  const r = layerResultSchema.safeParse(results);
  return r.success ? r.data : null;
}

// ---- S1 (ASSUMED until S5): nonlinear results (results.nonlinear)

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

/** The new result channels (GLB extras, probe values) and their words. S3: real; S1: assumed. */
export const NEW_CHANNELS: Record<string, string> = {
  layer_tension: "tension across layers",
  fatigue_safety_factor: "fatigue safety factor",
  plastic_strain: "plastic strain (PEEQ)",
  contact_pressure: "contact pressure",
};

// ---- S2 (ASSUMED until S5): the OpenFOAM row of GET /sims/solvers cfd (system-check row shape)

export const OPENFOAM_ID_RE = /^openfoam/;
