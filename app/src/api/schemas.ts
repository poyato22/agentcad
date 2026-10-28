// zod schemas mirroring types.ts. Every server response is parsed with one of these at the
// client boundary, so schema drift fails loudly (CoreError "validation") instead of leaking
// half-shaped data into the store. Unknown extra keys are stripped, not rejected.
// The `Same<...>` checks at the bottom fail the type check if a schema and its type drift apart.
import { z } from "zod";
import {
  ERROR_CODES, SETUP_PIECES, STEP_KINDS,
  type AgentInfo, type AgentRun, type Approval, type CancelAllResult, type ClaudeCodeStatus, type CoreErrorBody, type CoreStatusInfo,
  type KeyStatus, type MisesEvent, type ModelsInfo, type LocalTestResult, type OnshapeTestResult, type PartHole, type PartMetrics, type PartSummary,
  type Profile, type SetupState, type Step, type ToolOutput,
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
  external: z.boolean().optional(), session_ref: z.string().nullish(),
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
  vendors: z.array(z.object({
    vendor: z.string(),
    key: z.enum(["env", "keychain", "none", "not_needed", "signed_in", "signed_out", "not_installed"]),
    models: z.array(z.string()).optional(),
  })),
});

export const coreStatusSchema = z.object({
  core_version: z.string(), onshape: z.enum(["connected", "not_configured"]), onshape_calls_year: z.number(),
  onshape_cap_year: z.number(), runs_running: z.number(), approvals_pending: z.number(),
  data_dir: z.string().nullish(),
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

// ---- Plan 3: profile, keys, setup, Claude Code, Onshape test

/** Defaults mirror core profile.Profile, so a core that adds a field later still parses here. */
export const profileSchema = z.object({
  display_name: z.string().default(""), units: z.literal("mm").default("mm"), default_material: z.string().default("PLA"),
  default_agent: z.string().default("designer"), default_model: z.string().nullable().default(null),
  onshape_calls_year_cap: z.number().default(2500), onshape_calls_run_cap: z.number().default(200),
  max_parallel_agents: z.number().int().default(2), local_endpoints: z.record(z.string(), z.string()).default({}),
  theme: z.enum(["system", "light", "dark"]).default("system"),
  setup_completed_at: z.number().nullable().default(null),
});

/** Status only. Any extra key (a secret, by mistake) is stripped before it reaches the UI. */
export const keyStatusSchema = z.object({ vendor: z.string(), present: z.boolean(), source: z.enum(["env", "keychain", "none"]) });

export const setupStateSchema = z.object({
  complete: z.boolean(), has_model: z.boolean(), has_onshape: z.boolean(), missing: z.array(z.enum(SETUP_PIECES)),
});

export const localTestSchema = z.object({ ok: z.boolean(), error: z.string().nullish() });
export const onshapeTestSchema = z.object({ ok: z.boolean(), user_name: z.string().nullish(), error: z.string().nullish() });

export const claudeCodeStatusSchema = z.object({
  installed: z.boolean(), version: z.string().nullable().default(null), signed_in: z.boolean(),
  auth_method: z.string().nullable().default(null),
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
  Assert<Same<z.infer<typeof profileSchema>, Profile>>,
  Assert<Same<z.infer<typeof keyStatusSchema>, KeyStatus>>,
  Assert<Same<z.infer<typeof setupStateSchema>, SetupState>>,
  Assert<Same<z.infer<typeof onshapeTestSchema>, OnshapeTestResult>>,
  Assert<Same<z.infer<typeof localTestSchema>, LocalTestResult>>,
  Assert<Same<z.infer<typeof claudeCodeStatusSchema>, ClaudeCodeStatus>>,
];
