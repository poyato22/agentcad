// Plan 13 H4: whole-assembly studies (core sim/system_study.py, server/system_study_api.py).
// GET /assembly_studies/kinds?project_id= (which kind fits the build, the recommended one first,
// each with its settings schema), POST /assembly_studies {project_id, kind, settings} → 202
// {run_id}, GET /assembly_studies?project_id= (the build's records, newest first), GET
// /assembly_studies/{id} (one record: checks, metrics, charts, playback, fem), POST
// /assembly_studies/{id}/fem → 202 {run_id}. Progress: `assembly_study_progress` events. Names,
// reasons and notes are core data written from user and agent text: render as text.
import { z } from "zod";
import { studyChartSchema } from "./studies";

export const ASSEMBLY_STUDY_KINDS = ["walk", "launch", "drop", "mechanism"] as const;
export type AssemblyStudyKind = (typeof ASSEMBLY_STUDY_KINDS)[number];

const verdict = z.enum(["pass", "warn", "fail", "not_checked"]).catch("not_checked");
export type AssemblyVerdict = z.infer<typeof verdict>;

export const assemblyKindSchema = z.looseObject({
  kind: z.enum(ASSEMBLY_STUDY_KINDS), name: z.string(), applicable: z.boolean(), recommended: z.boolean(),
  why: z.string().catch(""), doc: z.string().catch(""), schema: z.record(z.string(), z.unknown()).catch({}),
});
export type AssemblyKind = z.infer<typeof assemblyKindSchema>;
export const assemblyKindsSchema = z.looseObject({
  project_id: z.string(),
  kinds: z.array(z.unknown()).transform((xs) => xs.flatMap((x) => { const r = assemblyKindSchema.safeParse(x); return r.success ? [r.data] : []; })),
});

export const assemblyStudySummarySchema = z.looseObject({
  study_id: z.string(), project_id: z.string(), kind: z.enum(ASSEMBLY_STUDY_KINDS), name: z.string(),
  status: z.string(), verdict: verdict.nullish(), summary: z.string().nullish(),
  created_at: z.number(), finished_at: z.number().nullish(), run_id: z.string().nullish(),
  phase: z.string().nullish(), pct: z.number().nullish(), error: z.string().nullish(), hint: z.string().nullish(),
});
export type AssemblyStudySummary = z.infer<typeof assemblyStudySummarySchema>;

const num = z.union([z.number(), z.array(z.number()).min(1).max(3)]);
export const playbackStepSchema = z.looseObject({
  joint_id: z.string(), from: num, to: num, duration_s: z.number(),
  follow: z.array(z.looseObject({ joint_id: z.string(), values: z.array(num) })).optional(),
});
const checkSchema = z.looseObject({
  name: z.string(), status: verdict, reason: z.string().catch(""),
  value: z.union([z.number(), z.string()]).nullish(), unit: z.string().catch(""), limit: z.number().nullish(),
});
export type AssemblyCheck = z.infer<typeof checkSchema>;
const metricSchema = z.looseObject({
  label: z.string(), value: z.union([z.number(), z.string()]).nullable(), unit: z.string().catch(""), status: verdict.optional(),
});
export type AssemblyMetric = z.infer<typeof metricSchema>;
const femRowSchema = z.looseObject({
  part_id: z.string().nullish(), name: z.string().nullish(), sim_id: z.string().nullish(),
  safety_factor: z.number().nullish(), status: verdict, reason: z.string().nullish(),
});
const jointRowSchema = z.looseObject({
  joint_id: z.string(), label: z.string().nullish(), actuator: z.string().nullish(),
  peak_nm: z.number().nullish(), use_of_stall: z.number().nullish(), status: verdict,
});
export type AssemblyJointRow = z.infer<typeof jointRowSchema>;
const list = <T>(s: z.ZodType<T>) => z.array(z.unknown()).catch([]).transform((xs) => xs.flatMap((x) => { const r = s.safeParse(x); return r.success ? [r.data] : []; }));

export const assemblyStudyRecordSchema = assemblyStudySummarySchema.extend({
  settings: z.record(z.string(), z.unknown()).catch({}),
  checks: list(checkSchema), metrics: list(metricSchema), charts: list(studyChartSchema),
  joints: list(jointRowSchema),
  playback: z.looseObject({ steps: list(playbackStepSchema), loop: z.boolean().catch(false), caption: z.string().catch("") }).nullish().catch(null),
  notes: z.array(z.string()).catch([]), accuracy: z.array(z.string()).catch([]),
  fem: z.looseObject({
    available: z.boolean().catch(false), via: z.string().nullish(), status: z.string().nullish(),
    summary: z.string().nullish(), note: z.string().nullish(), run_id: z.string().nullish(), rows: list(femRowSchema),
  }).nullish().catch(null),
});
export type AssemblyStudyRecord = z.infer<typeof assemblyStudyRecordSchema>;

export const assemblyStartedSchema = z.looseObject({ run_id: z.string(), status: z.string() });

export const assemblyProgressSchema = z.looseObject({
  study_id: z.string(), project_id: z.string(), kind: z.string(), phase: z.string(),
  pct: z.number().nullish(), detail: z.string().nullish(),
});
export type AssemblyProgress = z.infer<typeof assemblyProgressSchema>;
