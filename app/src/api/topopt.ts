// Generative design / topology optimization (core topopt/, server/topopt_api.py, Plan 9 D14):
// the run record with its variants, the start / accept bodies, and the `topopt_progress` /
// `topopt_finished` events (stream "sim"). Records are loose objects: the fields the UI reads are
// typed, anything newer passes through. Every number on a variant card comes from CalculiX on
// the final solid (verify), never from the voxel optimizer: a guide, not a guarantee.
import { z } from "zod";

export const TOPOPT_ID_RE = /^to_[0-9a-f]{12}$/;
export type TopoptStatus = "queued" | "running" | "done" | "failed" | "cancelled" | "interrupted" | (string & {});

const num = z.number().nullable();

export const topoptVerifySchema = z.looseObject({
  compliance: z.number().optional(), max_deflection_mm: z.number().optional(),
  max_von_mises_mpa: z.number().optional(), safety_factor: num.optional(), allowable_mpa: z.number().optional(),
  /** The conservative peak (the fixture zone included): the safety factor's basis. */
  max_von_mises_incl_fixture_mpa: z.number().optional(), max_von_mises_at_fixture_mpa: z.number().nullish(),
  safety_factor_basis: z.string().optional(), safety_factor_away_from_fixture: num.optional(),
  nodes: z.number().optional(), tets: z.number().optional(), mesher: z.string().optional(),
  error: z.string().nullish(), hint: z.string().nullish(),
});
export type TopoptVerify = z.infer<typeof topoptVerifySchema>;

export const topoptUrlsSchema = z.looseObject({
  preview: z.string(), result: z.string(), stress: z.string(), render: z.string(), step: z.string(), stl: z.string(),
});

export const topoptVariantSchema = z.looseObject({
  id: z.string(), name: z.string(), status: z.string(), phase: z.string().default("queued"),
  pct: z.number().default(0), it: z.number().nullish(), max_iter: z.number().nullish(), preview: z.number().nullish(),
  volume_fraction: z.number(), resolution_mm: z.number(), filter_radius_mm: z.number(),
  mass_g: z.number().nullish(), faces: z.number().nullish(), step_bytes: z.number().nullish(),
  thin_member_pct: z.number().nullish(), overhang_area_pct: z.number().nullish(),
  compliance_error_pct: z.number().nullish(),
  verify: topoptVerifySchema.nullish(), verified: z.boolean().default(false), passes: z.boolean().nullish(),
  pareto: z.boolean().default(false), rank: z.number().nullish(),
  warnings: z.array(z.string()).default([]), error: z.string().nullish(),
  urls: topoptUrlsSchema.optional(),
});
export type TopoptVariant = z.infer<typeof topoptVariantSchema>;

export const topoptAcceptedSchema = z.looseObject({
  variant: z.string(), part_id: z.string(), mode: z.string(), version: z.number().nullish(), at: z.number().optional(),
});

export const topoptRecordSchema = z.looseObject({
  topopt_id: z.string(), tool: z.string().optional(), part_id: z.string(), project_id: z.string().nullish(),
  run_id: z.string().nullish(), status: z.string(), created_at: z.number().optional(), finished_at: z.number().nullish(),
  objective: z.string().optional(), min_safety_factor: z.number().optional(), max_deflection_mm: z.number().nullish(),
  reason: z.string().nullish(), variants: z.array(topoptVariantSchema), pareto: z.array(z.string()).default([]),
  recommended: z.string().nullish(), accepted: z.array(topoptAcceptedSchema).default([]),
  summary: z.string().nullish(), stream: z.string().optional(),
});
export type TopoptRecord = z.infer<typeof topoptRecordSchema>;

export const topoptAcceptResultSchema = z.looseObject({
  part_id: z.string(), version: z.number().nullish(), variant: z.string(), mode: z.string(), summary: z.string().nullish(),
});
export type TopoptAcceptResult = z.infer<typeof topoptAcceptResultSchema>;

/** topopt_progress / topopt_finished event data. */
export const topoptEventSchema = z.looseObject({
  topopt_id: z.string(), part_id: z.string(), variant: z.string().optional(), status: z.string().optional(),
  phase: z.string().optional(), pct: z.number().optional(), it: z.number().nullish(), max_iter: z.number().nullish(),
  compliance: z.number().nullish(), vf: z.number().nullish(), preview: z.number().nullish(),
  preview_url: z.string().nullish(), summary: z.string().optional(), recommended: z.string().nullish(),
  stream: z.string().optional(),
});
export type TopoptEvent = z.infer<typeof topoptEventSchema>;

export type Direction = "+x" | "-x" | "+y" | "-y" | "+z" | "-z";
export interface TopoptRegion {
  kind: "faces" | "part" | "box" | "cylinder";
  faces?: string[]; thickness_mm?: number; part_id?: string;
  box?: [number, number, number, number, number, number];
  cylinder?: { center: [number, number, number]; axis: [number, number, number]; radius: number; length: number };
}
export interface TopoptLoadCase { name: string; weight?: number; loads: { face: string; newtons: number; direction: Direction }[] }
export interface TopoptManufacturing {
  process: "none" | "print_3d" | "machined"; symmetry?: ("x" | "y" | "z")[]; min_member_mm?: number;
}
/** POST /topopt (topopt_run, or topopt_generate when variants / sweep are given). */
export interface TopoptStartBody {
  part_id: string; design_space?: "part" | "bbox"; keep?: TopoptRegion[]; keep_out?: TopoptRegion[];
  fixtures: { kind?: "fixed"; face: string }[]; load_cases: TopoptLoadCase[]; material?: string;
  objective?: "min_compliance" | "min_mass"; volume_fraction?: number; max_deflection_mm?: number;
  min_safety_factor?: number; resolution_mm?: number; filter_radius_mm?: number; max_iter?: number;
  manufacturing?: TopoptManufacturing; verify?: boolean; reason?: string;
  variants?: Record<string, unknown>[]; sweep?: Record<string, number[]>; max_variants?: number;
}
export interface TopoptAcceptBody { variant?: string; mode?: "new" | "replace"; new_part_id?: string; base_version?: number }
