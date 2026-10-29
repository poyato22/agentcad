// Every study type (core sim/studies.py, sim/settings.py, sim/charts.py, sim/fields.py and
// server/sim_api.py, Plan 5 C7): the study-type registry with JSON schemas of each type's
// settings, the generic record / card / chart / compare / template / material / probe / section
// shapes, and `sim_progress {sim_id, phase, pct, detail}`. The records differ per type, so the
// schemas are loose objects: the fields the UI reads are typed, the rest passes through as data.
// Kept apart from sims.ts (the original static-only shapes) so the two can merge without clashes.
// Everything here comes from the core and may carry solver or file text: render it as text.
import { z } from "zod";

export const STUDY_TYPES = ["static", "modal", "buckling", "thermal", "drop", "wind"] as const;
export type StudyType = (typeof STUDY_TYPES)[number];
export const isStudyType = (v: unknown): v is StudyType => typeof v === "string" && (STUDY_TYPES as readonly string[]).includes(v);

export type Verdict = "pass" | "warn" | "fail";
/** A study's status; any other string the core may add later passes through. */
export type StudyStatus = "running" | "done" | "failed" | "cancelled" | "interrupted" | (string & {});
export type StudyPhase = "queued" | "mesh" | "solve" | "post" | "done" | (string & {});

const num = z.number().nullable();
const strN = z.string().nullable();
const vec3 = z.tuple([z.number(), z.number(), z.number()]);

/** A (pydantic) JSON schema node: kept as data, read by ui/pages/studies/schemaForm.ts. */
export type JsonSchema = { [k: string]: unknown };
const jsonSchema = z.record(z.string(), z.unknown());

export const studyTypeInfoSchema = z.looseObject({
  id: z.enum(STUDY_TYPES), name: z.string(), solver: z.string(), fidelity_class: z.string(), doc: z.string(),
  channels: z.array(z.string()), available: z.boolean(), detail: strN, schema: jsonSchema,
});
export type StudyTypeInfo = z.infer<typeof studyTypeInfoSchema>;

export const studyMetricSchema = z.looseObject({ key: z.string(), label: z.string(), unit: z.string(), value: z.unknown() });
export type StudyMetric = z.infer<typeof studyMetricSchema>;

export const studyCheckSchema = z.looseObject({
  requirement_id: z.string().nullish(), kind: z.string().nullish(), target: z.unknown(), measured: z.unknown(),
  unit: z.string().nullish(), status: z.enum(["pass", "warn", "fail"]), sim_id: z.string().nullish(), note: z.string().nullish(),
});
export type StudyCheck = z.infer<typeof studyCheckSchema>;

export const studyUrlsSchema = z.looseObject({
  record: z.string(), glb: z.string(), charts: z.string(), probe: z.string(), section: z.string(),
});

/** studies.card(): what a thread / Studies card renders. */
export const studyCardSchema = z.looseObject({
  sim_id: z.string().nullable(), type: z.string(), status: z.string().nullable(), verdict: z.enum(["pass", "warn", "fail"]).nullable(),
  phase: z.string().nullish(), pct: num.optional(), part_id: z.string().nullable(), label: z.string(),
  fidelity_class: z.string().nullish(), fidelity_note: z.string().nullish(), notes: z.array(z.string()).default([]),
  metrics: z.array(studyMetricSchema).default([]), checks: z.array(studyCheckSchema).default([]),
  warnings: z.array(z.string()).default([]), reason: z.string().nullish(), run_id: z.string().nullish(),
  step_id: z.number().nullish(), urls: studyUrlsSchema.optional(), charts_url: z.string().optional(),
  /** Set when compact() kept the checks once, at the top level. */
  checks_at: z.string().optional(),
});
export type StudyCard = z.infer<typeof studyCardSchema>;

/** GET /sims item (newest first). */
export const studySummarySchema = z.looseObject({
  sim_id: z.string(), part_id: z.string(), run_id: z.string().nullable(), step_id: z.number().nullish(),
  type: z.string().nullish(), status: z.string(), stage: z.string().nullish(), phase: z.string().nullish(), pct: num.optional(),
  created_at: z.number(), finished_at: num, error: strN, hint: strN, inputs: z.unknown(),
  results: z.record(z.string(), z.unknown()).nullable(), guide: z.boolean(), label: z.string(), fidelity_class: z.string().nullish(),
  reason: z.string().nullish(), stale: z.boolean(),
});
export type StudySummary = z.infer<typeof studySummarySchema>;

/** GET /sims/{sim_id}: the full record (results differ per type) plus its card. */
export const studyRecordSchema = studySummarySchema.extend({
  call_id: z.string().nullish(), settings: z.record(z.string(), z.unknown()).nullish(),
  results: z.record(z.string(), z.unknown()).nullish(),
  material: z.looseObject({ name: z.string() }).nullish(), environment: z.unknown().optional(),
  warnings: z.array(z.string()).default([]), caveats: z.array(z.string()).default([]),
  solver: z.record(z.string(), z.unknown()).nullish(), mesh: z.record(z.string(), z.unknown()).nullish(),
  glb: z.string().nullish(), outputs: z.array(z.string()).optional(), card: studyCardSchema.optional(),
  urls: studyUrlsSchema.optional(),
});
export type StudyRecord = z.infer<typeof studyRecordSchema>;

// ---- charts (sim/charts.py): plain series, no styling

const axisSchema = z.object({ label: z.string(), unit: z.string() });
export const studyChartSchema = z.looseObject({
  id: z.string(), kind: z.enum(["line", "bar", "histogram"]), title: z.string(), x: axisSchema, y: axisSchema,
  series: z.array(z.looseObject({
    name: z.string(), points: z.array(z.tuple([z.number(), z.number().nullable()])), flags: z.array(z.boolean()).optional(),
  })).optional(),
  bands: z.array(z.looseObject({ label: z.string(), axis: z.enum(["x", "y"]), from: z.number(), to: z.number() })).optional(),
  edges: z.array(z.number().nullable()).optional(), counts: z.array(z.number()).optional(),
  markers: z.array(z.looseObject({ label: z.string(), x: z.number().nullable() })).optional(),
  table: z.array(z.record(z.string(), z.unknown())).optional(),
});
export type StudyChart = z.infer<typeof studyChartSchema>;

export const studyChartsSchema = z.looseObject({
  sim_id: z.string().nullable(), type: z.string(), status: z.string().nullable(), label: z.string().nullish(),
  fidelity_class: z.string().nullish(), charts: z.array(studyChartSchema),
});
export type StudyCharts = z.infer<typeof studyChartsSchema>;

// ---- compare, templates, materials, probe, section, cancel, start

export const studyCompareSchema = z.looseObject({
  columns: z.array(z.looseObject({
    sim_id: z.string(), type: z.string(), part_id: z.string().nullable(), status: z.string().nullable(), stale: z.boolean().nullable(),
    fidelity_class: z.string().nullish(), material: z.string().nullable(), created_at: z.number().nullable(),
  })),
  rows: z.array(z.looseObject({
    key: z.string(), label: z.string(), unit: z.string(), values: z.array(z.number().nullable()), best: z.number().nullable(),
  })),
  label: z.string(),
});
export type StudyCompare = z.infer<typeof studyCompareSchema>;

export const studyTemplateSchema = z.looseObject({
  id: z.string(), name: z.string(), type: z.enum(STUDY_TYPES), settings: z.record(z.string(), z.unknown()), created_at: z.number(),
});
export type StudyTemplate = z.infer<typeof studyTemplateSchema>;

export const studyMaterialSchema = z.looseObject({
  name: z.string(), e_mpa: z.number(), nu: z.number(), yield_mpa: z.number(), density_g_cm3: z.number(),
  hdt_c: z.number(), source: z.string(), k_w_mk: z.number(), alpha_per_k: z.number(), custom: z.boolean(),
  knockdown: z.number().optional(), allowable_mpa: z.number().optional(), model: z.string().optional(),
});
export type StudyMaterial = z.infer<typeof studyMaterialSchema>;
export const studyMaterialsSchema = z.object({ builtin: z.array(studyMaterialSchema), custom: z.array(studyMaterialSchema) });
export type StudyMaterials = z.infer<typeof studyMaterialsSchema>;
/** POST /sims/materials (core settings.CustomMaterial). */
export interface CustomMaterialBody {
  name: string; e_mpa: number; nu: number; yield_mpa: number; density_g_cm3: number;
  k_w_mk?: number; alpha_per_k?: number; hdt_c?: number; source?: string;
}

const probeValue = z.union([z.number(), z.array(z.number().nullable()), z.null()]);
export const studyProbeSchema = z.looseObject({
  sim_id: z.string(), point: vec3, units: z.record(z.string(), z.string()), inside: z.boolean(),
  solid: z.boolean().optional(), values: z.record(z.string(), probeValue),
});
export type StudyProbe = z.infer<typeof studyProbeSchema>;

/** Nested arrays: (n_u, n_v) for a scalar channel; a vector channel on a CFD grid is (3, n_u, n_v). */
type Nested = number | null | Nested[];
const nested: z.ZodType<Nested> = z.lazy(() => z.union([z.number(), z.null(), z.array(nested)]));
export const studySectionSchema = z.looseObject({
  axis: z.enum(["x", "y", "z"]), offset: z.number(), u: z.enum(["x", "y", "z"]), v: z.enum(["x", "y", "z"]),
  u_values: z.array(z.number().nullable()), v_values: z.array(z.number().nullable()), channel: z.string(), units: z.string(),
  values: z.array(nested),
});
export type StudySection = z.infer<typeof studySectionSchema>;

export const studyCancelSchema = z.looseObject({ sim_id: z.string(), status: z.literal("cancelling") });

/** POST /sims body (core server/sim_api.StartBody). */
export interface StudyStartBody {
  type: StudyType; part_id: string; settings: Record<string, unknown>;
  reason?: string | null; requirements?: Record<string, unknown>[];
}

/** Solver status (GET /sims/solvers): ccx, gmsh and the CFD rows (system-check row shape). */
export const studySolversSchema = z.looseObject({
  ccx: z.looseObject({ name: z.string(), found: z.boolean(), version: strN, install: z.string() }),
  gmsh: z.looseObject({ name: z.string(), found: z.boolean(), version: strN, install: z.string() }),
  cfd: z.array(z.looseObject({ id: z.string(), name: z.string(), status: z.string(), detail: z.string() })).optional(),
});
export type StudySolvers = z.infer<typeof studySolversSchema>;

/** `sim_progress` event data (core studies.run_study progress()). */
export const studyProgressSchema = z.looseObject({
  sim_id: z.string(), part_id: z.string(), type: z.string().optional(), stage: z.string(),
  phase: z.string().optional(), pct: z.number().optional(), detail: z.string().optional(),
  /** The tool call that runs the study (absent on cores before A8 fix round 1). */
  call_id: z.string().nullish(), step_id: z.number().nullish(),
});
export type StudyProgress = z.infer<typeof studyProgressSchema>;

// ---- design_review (core sim/tools.py design_review: the tool_result data)

export const reviewRowSchema = z.looseObject({
  requirement_id: z.string().nullish(), kind: z.string().nullish(), target: z.unknown(), measured: z.unknown(),
  unit: z.string().nullish(), status: z.enum(["pass", "warn", "fail"]), sim_id: z.string().nullish(),
  study: z.string().nullish(), case: z.string().nullish(), url: z.string().nullish(), note: z.string().nullish(),
});
export type ReviewRow = z.infer<typeof reviewRowSchema>;

export const designReviewSchema = z.looseObject({
  verdict: z.enum(["pass", "warn", "fail"]), table: z.array(reviewRowSchema), label: z.string(), part_id: z.string(),
  studies: z.array(z.looseObject({
    type: z.string(), case: z.string().nullish(), sim_id: z.string().nullish(), ok: z.boolean(),
    error: z.string().nullish(), url: z.string().nullish(), card: studyCardSchema.nullish(),
  })),
});
export type DesignReview = z.infer<typeof designReviewSchema>;
