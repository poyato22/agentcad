// zod schemas mirroring types.ts. Every server response is parsed with one of these at the
// client boundary, so schema drift fails loudly (CoreError "validation") instead of leaking
// half-shaped data into the store. Unknown extra keys are stripped, not rejected.
// The `Same<...>` checks at the bottom fail the type check if a schema and its type drift apart.
import { z } from "zod";
import {
  ERROR_CODES, STEP_KINDS,
  type AgentInfo, type AgentRun, type Approval, type CancelAllResult, type CoreErrorBody, type CoreStatusInfo, type MisesEvent,
  type ModelsInfo, type PartHole, type PartMetrics, type PartSummary, type Step, type ToolOutput,
} from "./types";

const record = z.record(z.string(), z.unknown());
const vec3 = z.tuple([z.number(), z.number(), z.number()]);

export const errorCodeSchema = z.enum(ERROR_CODES);
export const stepKindSchema = z.enum(STEP_KINDS);

export const agentRunSchema = z.object({
  id: z.string(), parent_id: z.string().nullable(), agent: z.string(), provider: z.string(), model: z.string(),
  task: z.string(), status: z.enum(["running", "done", "failed", "cancelled"]), branch: z.string().nullable(),
  onshape_calls: z.number(), input_tokens: z.number(), output_tokens: z.number(), usd: z.number(),
  created_at: z.number(), updated_at: z.number(), owner_pid: z.number().nullish(),
  external: z.boolean().optional(),
});

export const stepSchema = z.object({
  id: z.number().int(), run_id: z.string(), ts: z.number(), kind: stepKindSchema, payload: record,
  evidence_hash: z.string(), prev_hash: z.string().nullable(), collapsed: z.boolean(),
});

export const toolOutputSchema = z.object({
  ok: z.boolean(), summary: z.string(), data: record, handles: z.array(z.string()),
  warnings: z.array(z.string()), step_kind: stepKindSchema,
});

export const approvalSchema = z.object({
  id: z.string(), run_id: z.string(), action: z.string(), payload: record, payload_hash: z.string(),
  microversion: z.string().nullable(), deltas: record,
  status: z.enum(["pending", "approved", "rejected", "expired", "executed"]),
  expires_at: z.number(), decided_by: z.string().nullable(), decided_at: z.number().nullable(),
  edited_payload: record.nullable(),
});

export const eventSchema = z.object({
  type: z.string(), run_id: z.string().nullable(), data: record, ts: z.number(),
});

export const agentInfoSchema = z.object({ name: z.string(), description: z.string(), model: z.string() });

export const modelsInfoSchema = z.object({
  default: z.string(),
  vendors: z.array(z.object({ vendor: z.string(), key: z.enum(["env", "keychain", "none", "not_needed"]) })),
});

export const coreStatusSchema = z.object({
  core_version: z.string(), onshape: z.enum(["connected", "not_configured"]), onshape_calls_year: z.number(),
  onshape_cap_year: z.number(), runs_running: z.number(), approvals_pending: z.number(),
});

export const partHoleSchema = z.object({ diameter_mm: z.number(), axis: vec3, center: vec3, depth_mm: z.number() });

export const partMetricsSchema = z.object({
  valid: z.boolean(), volume_mm3: z.number(), area_mm2: z.number(), mass_g: z.number(),
  bbox: z.object({ min: vec3, max: vec3, size: vec3 }),
  center_of_mass: vec3, faces: z.number(), edges: z.number(), holes: z.array(partHoleSchema), solids: z.number(),
});

export const partSummarySchema = z.object({
  part_id: z.string(), vars: z.record(z.string(), z.number()), metrics: partMetricsSchema.nullable(), mtime: z.number(),
});

export const coreErrorBodySchema = z.object({
  error: errorCodeSchema, message: z.string(),
  hint: z.string().nullable().default(null), retryable: z.boolean().default(false),
});

export const okSchema = z.object({ ok: z.literal(true) });
export const runIdSchema = z.object({ run_id: z.string() });
/** `failed` defaults to [] so a core that predates it still parses. */
export const cancelAllSchema = z.object({
  cancelled: z.array(z.string()),
  failed: z.array(z.object({ run_id: z.string(), error: z.string() })).default([]),
});

/** Data of a `question_asked` event (the run id comes from the event itself). */
export const questionAskedSchema = z.object({
  question_id: z.string(), question: z.string(), options: z.array(z.string()).default([]),
});

// ---- compile-time drift checks between schemas and types.ts
type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;
type Assert<T extends true> = T;
export type SchemaChecks = [
  Assert<Same<z.infer<typeof agentRunSchema>, AgentRun>>,
  Assert<Same<z.infer<typeof stepSchema>, Step>>,
  Assert<Same<z.infer<typeof toolOutputSchema>, ToolOutput>>,
  Assert<Same<z.infer<typeof approvalSchema>, Approval>>,
  Assert<Same<z.infer<typeof eventSchema>, MisesEvent>>,
  Assert<Same<z.infer<typeof agentInfoSchema>, AgentInfo>>,
  Assert<Same<z.infer<typeof modelsInfoSchema>, ModelsInfo>>,
  Assert<Same<z.infer<typeof coreStatusSchema>, CoreStatusInfo>>,
  Assert<Same<z.infer<typeof partHoleSchema>, PartHole>>,
  Assert<Same<z.infer<typeof partMetricsSchema>, PartMetrics>>,
  Assert<Same<z.infer<typeof partSummarySchema>, PartSummary>>,
  Assert<Same<z.infer<typeof coreErrorBodySchema>, CoreErrorBody>>,
  Assert<Same<z.infer<typeof cancelAllSchema>, CancelAllResult>>,
];
