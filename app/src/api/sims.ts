// Static stress studies (core server/sim_api.py, sim/tools.py): types + zod schemas for the
// /sims routes. Kept apart from types.ts/schemas.ts so the Studies page's additions stay in one
// place. Every result is a guide, not a guarantee: the record always carries `guide`, `label`,
// solver, mesh, material model and the error band (see core sim/tools.py result_summary).
import { z } from "zod";
import type { Vec3 } from "./types";

export const SIM_STATUSES = ["running", "done", "failed", "cancelled", "interrupted"] as const;
export type SimStatus = (typeof SIM_STATUSES)[number];

export const SIM_STAGES = ["queued", "mesh_coarse", "solve_coarse", "mesh_fine", "solve_fine", "post", "done", "failed", "cancelled"] as const;
export type SimStage = (typeof SIM_STAGES)[number];

/** Same as core sim/environments.py DIRECTIONS. */
export const SIM_DIRECTIONS = ["+x", "-x", "+y", "-y", "+z", "-z"] as const;
export type SimDirection = (typeof SIM_DIRECTIONS)[number];

/** Same as core sim/environments.py FACE_PRESETS (a face can also be a face id like "f3"). */
export const SIM_FACE_PRESETS = ["bottom", "top", "x-min", "x-max", "y-min", "y-max", "z-min", "z-max"] as const;
export const FACE_ID_RE = /^f\d{1,5}$/;

export interface SimLoad { face: string; newtons: number; direction: SimDirection }
export interface SimInputs { fix: string; load: SimLoad; material: string; environment: string | null }

export interface SimResults {
  max_von_mises_mpa: number; max_von_mises_at: Vec3;
  /** Peak on the fixed face (an ideal clamp exaggerates it); null when nothing was left out. */
  max_von_mises_at_fixture_mpa: number | null;
  max_displacement_mm: number; max_displacement_at: Vec3;
  /** allowable / peak (fixed face excluded). */
  safety_factor: number | null;
  /** allowable / max(peak, fixture peak). */
  safety_factor_incl_fixture: number | null;
  allowable_mpa: number;
  coarse_max_von_mises_mpa: number; coarse_max_displacement_mm: number;
  error_band: number | null; error_band_pct: number | null;
  /** "nodes on the fixed face" when they were actually left out of the peak, else null. */
  peak_excludes: string | null;
}

/** GET /sims item (newest first). */
export interface SimSummary {
  sim_id: string; part_id: string; run_id: string | null; status: SimStatus; stage: SimStage;
  created_at: number; finished_at: number | null; error: string | null; hint: string | null;
  inputs: SimInputs; results: SimResults | null; guide: boolean; label: string; fidelity_class: string; stale: boolean;
}

export interface SimMaterial {
  name: string; e_mpa: number; nu: number; yield_mpa: number; density_g_cm3: number; hdt_c: number;
  source: string; knockdown: number; allowable_mpa: number; model: string;
}

export interface SimEnvironment {
  name: string; temperature_c: number; flags: string[];
  suggested: { fix: string; load: SimLoad }; notes: string;
}

export interface SimMeshStage {
  size_mm: number; nodes: number; elements: number; fixed_nodes: number; loaded_nodes: number;
  dropped_load_n?: number | undefined;
}

export interface SimSolver { name: string; version: string; path: string; element: string }
export interface SimMesh { mesher: string; mesher_version: string; coarse: SimMeshStage; fine: SimMeshStage }

/** GET /sims/{sim_id}. solver / mesh / inputs_hash / glb / color_range_mpa only when done. */
export interface SimRecord extends Omit<SimSummary, "results"> {
  /** Absent (not null) until the study is done. */
  results?: SimResults | null | undefined;
  analysis: string; validated_against: unknown; geometry_hash: string | null;
  material: SimMaterial; environment: SimEnvironment | null;
  warnings: string[]; caveats: string[];
  solver?: SimSolver | undefined; mesh?: SimMesh | undefined; inputs_hash?: string | undefined;
  glb?: string | undefined; color_range_mpa?: [number, number] | undefined;
}

export interface SolverInfo { name: string; found: boolean; path?: string | null | undefined; version: string | null; install: string }
/** GET /sims/solvers. */
export interface SimSolvers { ccx: SolverInfo; gmsh: SolverInfo }

/** POST /sims body (core sim/tools.py StaticArgs). */
export interface SimStartBody { part_id: string; fix: string; load: SimLoad; material?: string | null; environment?: string | null }
/** POST /sims → 202. */
export interface SimStarted { run_id: string; status: "running" }

// ---- schemas

const vec3 = z.tuple([z.number(), z.number(), z.number()]);

export const simLoadSchema = z.object({ face: z.string(), newtons: z.number(), direction: z.enum(SIM_DIRECTIONS) });
export const simInputsSchema = z.object({
  fix: z.string(), load: simLoadSchema, material: z.string(), environment: z.string().nullable(),
});

export const simResultsSchema = z.object({
  max_von_mises_mpa: z.number(), max_von_mises_at: vec3, max_von_mises_at_fixture_mpa: z.number().nullable(),
  max_displacement_mm: z.number(), max_displacement_at: vec3,
  safety_factor: z.number().nullable(), safety_factor_incl_fixture: z.number().nullable(),
  allowable_mpa: z.number(), coarse_max_von_mises_mpa: z.number(), coarse_max_displacement_mm: z.number(),
  error_band: z.number().nullable(), error_band_pct: z.number().nullable(), peak_excludes: z.string().nullable(),
});

export const simSummarySchema = z.object({
  sim_id: z.string(), part_id: z.string(), run_id: z.string().nullable(), status: z.enum(SIM_STATUSES), stage: z.enum(SIM_STAGES),
  created_at: z.number(), finished_at: z.number().nullable(), error: z.string().nullable(), hint: z.string().nullable(),
  inputs: simInputsSchema, results: simResultsSchema.nullable(), guide: z.boolean(), label: z.string(),
  fidelity_class: z.string(), stale: z.boolean(),
});

export const simMaterialSchema = z.object({
  name: z.string(), e_mpa: z.number(), nu: z.number(), yield_mpa: z.number(), density_g_cm3: z.number(), hdt_c: z.number(),
  source: z.string(), knockdown: z.number(), allowable_mpa: z.number(), model: z.string(),
});

export const simEnvironmentSchema = z.object({
  name: z.string(), temperature_c: z.number(), flags: z.array(z.string()),
  suggested: z.object({ fix: z.string(), load: simLoadSchema }), notes: z.string(),
});

const simMeshStageSchema = z.object({
  size_mm: z.number(), nodes: z.number(), elements: z.number(), fixed_nodes: z.number(), loaded_nodes: z.number(),
  dropped_load_n: z.number().optional(),
});

export const simRecordSchema = simSummarySchema.extend({
  results: simResultsSchema.nullish(),
  analysis: z.string(), validated_against: z.unknown(), geometry_hash: z.string().nullable(),
  material: simMaterialSchema, environment: simEnvironmentSchema.nullable(),
  warnings: z.array(z.string()), caveats: z.array(z.string()),
  solver: z.object({ name: z.string(), version: z.string(), path: z.string(), element: z.string() }).optional(),
  mesh: z.object({ mesher: z.string(), mesher_version: z.string(), coarse: simMeshStageSchema, fine: simMeshStageSchema }).optional(),
  inputs_hash: z.string().optional(), glb: z.string().optional(),
  color_range_mpa: z.tuple([z.number(), z.number()]).optional(),
});

const solverInfoSchema = z.object({
  name: z.string(), found: z.boolean(), path: z.string().nullish(), version: z.string().nullable(), install: z.string(),
});
export const simSolversSchema = z.object({ ccx: solverInfoSchema, gmsh: solverInfoSchema });

export const simStartedSchema = z.object({ run_id: z.string(), status: z.literal("running") });

// ---- compile-time drift checks between the schemas and the types above
type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;
type Assert<T extends true> = T;
export type SimSchemaChecks = [
  Assert<Same<z.infer<typeof simLoadSchema>, SimLoad>>,
  Assert<Same<z.infer<typeof simInputsSchema>, SimInputs>>,
  Assert<Same<z.infer<typeof simResultsSchema>, SimResults>>,
  Assert<Same<z.infer<typeof simSummarySchema>, SimSummary>>,
  Assert<Same<z.infer<typeof simMaterialSchema>, SimMaterial>>,
  Assert<Same<z.infer<typeof simEnvironmentSchema>, SimEnvironment>>,
  Assert<Same<z.infer<typeof simRecordSchema>, SimRecord>>,
  Assert<Same<z.infer<typeof simSolversSchema>, SimSolvers>>,
  Assert<Same<z.infer<typeof simStartedSchema>, SimStarted>>,
];
