// The engineering loop (core engineering/*.py, server/spec_api.py): the build's spec, its design
// studies, spec_check rows and study_apply, plus the batched interview question of ui_ask_user.
// Kept apart from types.ts/schemas.ts (like sims.ts) so the spec panel's additions stay in one
// place. Shapes are the real core's (core/tests/fixtures/ui/spec*.json, studies.json, ...).
import { z } from "zod";

export const REQUIREMENT_KINDS = [
  "max_mass_g", "min_safety_factor", "max_deflection_mm", "envelope_mm", "mount_pattern", "printable", "custom",
] as const;
export type RequirementKind = (typeof REQUIREMENT_KINDS)[number];
/** Kinds measured against a load case's stress study (core spec.LOAD_CASE_KINDS). */
export const LOAD_CASE_KINDS: ReadonlySet<RequirementKind> = new Set(["min_safety_factor", "max_deflection_mm"]);
/** Kinds whose target is one positive number. */
export const NUMERIC_KINDS: ReadonlySet<RequirementKind> = new Set(["max_mass_g", "min_safety_factor", "max_deflection_mm"]);

export const SPEC_DIRECTIONS = ["+x", "-x", "+y", "-y", "+z", "-z"] as const;
export type SpecDirection = (typeof SPEC_DIRECTIONS)[number];

export interface Requirement {
  id: string; kind: RequirementKind; target: unknown; load_case_id: string | null;
  priority: number; source: "user" | "agent";
}
export interface LoadCase {
  id: string; name: string; fix: string; load: string; newtons: number; direction: SpecDirection;
  note: string; estimate: boolean;
}
export interface Spec { goal: string; requirements: Requirement[]; load_cases: LoadCase[]; answers: Record<string, unknown> }
/** GET/PUT /projects/{id}/spec answer. */
export interface SpecDoc { project_id: string; spec: Spec }

export type ReqStatus = "pass" | "warn" | "fail" | "not_checked";

/** One spec_check row (core spec.evaluate_requirement). */
export interface CheckRow {
  id: string; kind: RequirementKind; target: unknown; load_case_id: string | null; priority: number;
  measured: unknown; status: ReqStatus; detail: string; guide: boolean; estimate: boolean;
}
export interface SpecCheck {
  project_id: string; part_id: string; requirements: CheckRow[]; overall: ReqStatus; checked: number; total: number;
}

export const STUDY_STATUSES = ["running", "done", "failed", "cancelled", "budget_exhausted", "interrupted"] as const;
export type StudyStatus = (typeof STUDY_STATUSES)[number];

/** Every iteration has mass_g, bbox, sf_by_case, max_disp_mm and min_sf (core C3 I-6); a failed
 *  build or solve leaves some null. */
export interface StudyMetrics {
  mass_g: number | null; min_sf: number | null; max_disp_mm: number | null;
  sf_by_case: Record<string, number | null>; disp_by_case: Record<string, number | null>;
  bbox: { size: number[] } | null;
  sim_ids_by_case: Record<string, string>; sim_failed_by_case: Record<string, unknown>;
  build_error: string | null; variant: string | null;
}
export interface StudyIteration { n: number; vars: Record<string, number>; metrics: StudyMetrics; feasible: boolean; pareto: boolean }
export interface Study {
  id: string; project_id: string; part_id: string; run_id: string | null; created_at: number;
  variables: { name: string; lo: number; hi: number }[]; objective: string; status: StudyStatus;
  iterations: StudyIteration[]; best_id: number | null; solver: string | null; geometry_only: boolean;
  confirmation: { confirmed: boolean; n: number; metrics: Partial<StudyMetrics> } | null;
}

export interface StudyApplyBody { iteration: number; base_version?: number; allow_infeasible?: boolean }
export interface StudyApplied { study_id: string; part_id: string; iteration: number; summary: string; warnings: string[] }

/** One question of a ui_ask_user batch (core ui_tools.AskItem). */
export interface AskItem { id: string; prompt: string; options: string[]; multi: boolean; rank: boolean; allow_text: boolean }

// ---- schemas (lenient where the core may add fields; strict where the UI depends on a value)

const num = z.number().nullable().catch(null);
const numMap = z.record(z.string(), z.number().nullable()).catch({});

export const requirementSchema = z.object({
  id: z.string(), kind: z.enum(REQUIREMENT_KINDS), target: z.unknown(), load_case_id: z.string().nullable().default(null),
  priority: z.number().int(), source: z.enum(["user", "agent"]),
}).transform((r): Requirement => ({ ...r, target: r.target ?? null }));

export const loadCaseSchema = z.object({
  id: z.string(), name: z.string(), fix: z.string(), load: z.string(), newtons: z.number(),
  direction: z.enum(SPEC_DIRECTIONS), note: z.string().default(""), estimate: z.boolean().default(false),
});

export const specSchema = z.object({
  goal: z.string().default(""), requirements: z.array(requirementSchema).default([]),
  load_cases: z.array(loadCaseSchema).default([]), answers: z.record(z.string(), z.unknown()).default({}),
});
export const specDocSchema = z.object({ project_id: z.string(), spec: specSchema });

const reqStatus = z.enum(["pass", "warn", "fail", "not_checked"]);
export const checkRowSchema = z.object({
  id: z.string(), kind: z.enum(REQUIREMENT_KINDS), target: z.unknown(), load_case_id: z.string().nullable().default(null),
  priority: z.number(), measured: z.unknown(), status: reqStatus, detail: z.string().default(""),
  guide: z.boolean().default(false), estimate: z.boolean().default(false),
}).transform((r): CheckRow => ({ ...r, target: r.target ?? null, measured: r.measured ?? null }));
export const specCheckSchema = z.object({
  project_id: z.string(), part_id: z.string(), requirements: z.array(checkRowSchema), overall: reqStatus,
  checked: z.number(), total: z.number(),
});

const metricsSchema = z.object({
  mass_g: num.default(null), min_sf: num.default(null), max_disp_mm: num.default(null),
  sf_by_case: numMap.default({}), disp_by_case: numMap.default({}),
  bbox: z.object({ size: z.array(z.number()) }).nullable().catch(null).default(null),
  sim_ids_by_case: z.record(z.string(), z.string()).catch({}).default({}),
  sim_failed_by_case: z.record(z.string(), z.unknown()).catch({}).default({}),
  build_error: z.string().nullable().catch(null).default(null), variant: z.string().nullable().catch(null).default(null),
});
export const iterationSchema = z.object({
  n: z.number().int(), vars: z.record(z.string(), z.number()), metrics: metricsSchema,
  feasible: z.boolean(), pareto: z.boolean().default(false),
});
export const studySchema = z.object({
  id: z.string(), project_id: z.string(), part_id: z.string(), run_id: z.string().nullable().default(null),
  created_at: z.number(), variables: z.array(z.object({ name: z.string(), lo: z.number(), hi: z.number() })),
  objective: z.string(), status: z.enum(STUDY_STATUSES).catch("failed"),
  iterations: z.array(iterationSchema).default([]), best_id: z.number().int().nullable().default(null),
  solver: z.string().nullable().default(null), geometry_only: z.boolean().default(false),
  confirmation: z.object({ confirmed: z.boolean(), n: z.number(), metrics: metricsSchema.partial() }).nullable().catch(null).default(null),
});
export const studyAppliedSchema = z.object({
  study_id: z.string(), part_id: z.string(), iteration: z.number(), summary: z.string(), warnings: z.array(z.string()).default([]),
});

export const askItemSchema = z.object({
  id: z.string(), prompt: z.string(), options: z.array(z.string()).default([]),
  multi: z.boolean().default(false), rank: z.boolean().default(false), allow_text: z.boolean().default(true),
});

/** For resolving the active build until the app has a build signal of its own (A3). */
export const projectsLiteSchema = z.array(z.object({ id: z.string(), trashed_at: z.number().nullable().default(null) }));
export const profileActiveSchema = z.object({ active_project_id: z.string().nullable().default(null) });

/** `study_progress {study_id, n, total}` and `spec_changed {project_id}` event data. */
export const studyProgressSchema = z.object({ study_id: z.string(), n: z.number(), total: z.number() });
export const specChangedSchema = z.object({ project_id: z.string() });
