// zod schemas mirroring types.ts. Every server response is parsed with one of these at the
// client boundary, so schema drift fails loudly (CoreError "validation") instead of leaking
// half-shaped data into the store. Unknown extra keys are stripped, not rejected.
// The `Same<...>` checks at the bottom fail the type check if a schema and its type drift apart.
import { z } from "zod";
import {
  ERROR_CODES, SETUP_PIECES, STEP_KINDS,
  type AgentInfo, type AgentRun, type Approval, type CancelAllResult, type ClaudeCodeStatus, type CoreErrorBody, type CoreStatusInfo,
  type KeyStatus, type MisesEvent, type ModelsInfo, type LocalTestResult, type LocalStatus, type LocalActionResult, type LocalPullEvent, type OnshapeTestResult, type PartHole, type PartMetrics, type PartSummary,
  type Profile, type SetupState, type SketchSummary, type Step, type ToolOutput,
  type LocalPull, type ModelEntry, type ModelMeta, type ParallelInfo, type VendorInfo,
} from "./types";
import type { DrawingInfo, PrintCall, PrintCapabilities, Printer, PrinterStatus, PrintStart, Slice, SliceStart } from "./types";

const record = z.record(z.string(), z.unknown());
const vec3 = z.tuple([z.number(), z.number(), z.number()]);

export const errorCodeSchema = z.enum(ERROR_CODES);
export const stepKindSchema = z.enum(STEP_KINDS);

export const agentRunSchema = z.object({
  id: z.string(), parent_id: z.string().nullable(), agent: z.string(), provider: z.string(), model: z.string(),
  task: z.string(), status: z.enum(["queued", "running", "done", "failed", "cancelled"]), branch: z.string().nullable(),
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

/** An unknown badge from a newer core is dropped (null), never fatal. */
const recommendedSchema = z.enum(["best", "local", "free"]).nullable().catch(null);

/** GET /models `entries[]` / `recommended[]`: one model as a full ref with its badge. */
export const modelEntrySchema = z.object({
  model: z.string(), recommended: recommendedSchema.default(null), note: z.string().nullable().catch(null).default(null),
});

const vendorSchema = z.object({
  vendor: z.string(),
  key: z.enum(["env", "keychain", "none", "not_needed", "signed_in", "signed_out", "not_installed"]),
  models: z.array(z.string()).optional(),
  free: z.boolean().optional(),
  hint: z.string().nullable().optional(),
  // vendor-level badge: not sent by the current core; tolerated so a core that adds it still parses
  recommended: recommendedSchema.optional(),
  note: z.string().nullable().optional(),
  entries: z.array(modelEntrySchema).optional(),
}).transform((v): VendorInfo => {
  // the core's per-vendor `entries` become `model_meta` (keyed by full ref); a vendor the core
  // lists only through `entries` (anthropic: its best models) gets them as `models` too
  const { entries, ...rest } = v;
  if (!entries || entries.length === 0) return rest;
  const meta: Record<string, ModelMeta> = {};
  for (const e of entries) meta[e.model] = { recommended: e.recommended, note: e.note, free: e.recommended === "free" || v.free === true };
  return { ...rest, models: rest.models ?? entries.map((e) => e.model), model_meta: meta };
});

export const modelsInfoSchema = z.object({
  default: z.string(), vendors: z.array(vendorSchema), recommended: z.array(modelEntrySchema).optional(),
});

export const parallelSchema = z.object({ limit: z.number().int(), auto: z.boolean(), reason: z.string().default("") });

export const coreStatusSchema = z.object({
  core_version: z.string(), onshape: z.enum(["connected", "not_configured"]), onshape_calls_year: z.number(),
  onshape_cap_year: z.number(), runs_running: z.number(), approvals_pending: z.number(),
  data_dir: z.string().nullish(),
  runs_queued: z.number().optional(),
  parallel: parallelSchema.optional(),
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
  max_parallel_agents: z.union([z.number().int(), z.literal("auto")]).default("auto"), local_endpoints: z.record(z.string(), z.string()).default({}),
  theme: z.enum(["system", "light", "dark"]).default("system"),
  setup_completed_at: z.number().nullable().default(null),
  // the core's Profile carries `schema_version`; it is stripped here (never sent back in PUT /profile)
});

/** Status only. Any extra key (a secret, by mistake) is stripped before it reaches the UI. */
export const keyStatusSchema = z.object({ vendor: z.string(), present: z.boolean(), source: z.enum(["env", "keychain", "none"]) });

export const setupStateSchema = z.object({
  complete: z.boolean(), has_model: z.boolean(), has_onshape: z.boolean(), missing: z.array(z.enum(SETUP_PIECES)),
});

export const localTestSchema = z.object({ ok: z.boolean(), error: z.string().nullish() });

export const localStatusSchema = z.object({
  installed: z.boolean(), running: z.boolean(), version: z.string().nullable().default(null),
  models: z.array(z.object({ name: z.string(), size_gb: z.number().nullable() })).default([]),
  recommended: z.array(z.object({ name: z.string(), size_gb: z.number(), fits: z.boolean(), note: z.string() })).default([]),
});
export const localActionSchema = z.object({
  ok: z.boolean(), running: z.boolean().optional(), note: z.string().optional(), error: z.string().optional(),
  hint: z.string().optional(), already: z.boolean().optional(),
});
export const localPullEventSchema = z.object({
  model: z.string(), completed: z.number(), total: z.number(), status: z.string(), error: z.string().optional(),
});
/** `local_pull` event data (same as localPullEventSchema; the picker's name for it). */
export const localPullSchema = localPullEventSchema;
export const onshapeTestSchema = z.object({ ok: z.boolean(), user_name: z.string().nullish(), error: z.string().nullish() });

export const claudeCodeStatusSchema = z.object({
  installed: z.boolean(), version: z.string().nullable().default(null), signed_in: z.boolean(),
  auth_method: z.string().nullable().default(null),
});

// ---- Sketch page

export const sketchSummarySchema = z.object({
  sketch_id: z.string(), strokes: z.number().int(), dimensions: z.number().int(),
  photo: z.object({
    source_type: z.enum(["png", "jpeg", "heic"]), media_type: z.enum(["image/jpeg", "image/png"]),
    width: z.number(), height: z.number(), bytes: z.number(),
  }).nullable(),
  render: z.boolean(),
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
  Assert<Same<z.infer<typeof localStatusSchema>, LocalStatus>>,
  Assert<Same<z.infer<typeof localActionSchema>, LocalActionResult>>,
  Assert<Same<z.infer<typeof localPullEventSchema>, LocalPullEvent>>,
  Assert<Same<z.infer<typeof claudeCodeStatusSchema>, ClaudeCodeStatus>>,
  Assert<Same<z.infer<typeof parallelSchema>, ParallelInfo>>,
  Assert<Same<z.infer<typeof localPullSchema>, LocalPull>>,
  Assert<Same<z.infer<typeof modelEntrySchema>, ModelEntry>>,
  Assert<Same<z.infer<typeof sketchSummarySchema>, SketchSummary>>,
];

// ---- Plan 4: drawings and printing

export const drawingInfoSchema = z.object({
  sheet: z.tuple([z.number(), z.number()]), scale: z.number(), scale_text: z.string(), views: z.array(z.string()),
  overall_mm: vec3, holes: z.array(z.string()), label: z.string(), material: z.string(), date: z.string(), step_mtime_ns: z.number(),
});

export const printCapabilitiesSchema = z.object({
  slicer: z.object({ installed: z.boolean(), path: z.string().nullable(), install_command: z.string() }),
  kinds: z.array(z.object({ kind: z.string(), available: z.boolean(), note: z.string().nullable() })),
  models: z.array(z.string()),
  materials: z.array(z.object({ name: z.string(), density_g_cm3: z.number(), usd_per_kg: z.number() })),
});

/** Status only. Any extra key (an api_key, by mistake) is stripped before it reaches the UI. */
export const printerSchema = z.object({
  id: z.string(), kind: z.string(), host: z.string(), name: z.string(), model: z.string(), has_key: z.boolean(),
});

export const printerStatusSchema = z.object({
  printer_id: z.string(), reachable: z.boolean(), state: z.string().nullable(), sendable: z.boolean(),
  temp_nozzle: z.number().nullable(), temp_bed: z.number().nullable(),
  job: z.object({
    id: z.number().optional(), progress: z.number().optional(), time_remaining: z.number().optional(), time_printing: z.number().optional(),
  }).nullable(),
  error: z.string().nullable(), hint: z.string().nullish(),
});

export const sliceStartSchema = z.object({ slice_id: z.string(), status: z.string(), run_id: z.string(), call_id: z.string() });

export const sliceSchema = z.object({
  slice_id: z.string(), status: z.enum(["running", "done", "failed", "cancelled"]), part_id: z.string(), printer_id: z.string(),
  material: z.string(), model: z.string(), created_at: z.number(),
  estimate: z.object({ time_s: z.number(), time_text: z.string(), filament_mm: z.number(), filament_g: z.number(), cost_usd: z.number() }).nullable(),
  preview: z.boolean(), error: z.string().nullable(), hint: z.string().nullable(), sent_to: z.string().nullable(),
});

export const printStartSchema = z.object({ call_id: z.string(), run_id: z.string(), status: z.string() });

export const printCallSchema = z.object({
  call_id: z.string(), run_id: z.string(), tool: z.string(), status: z.enum(["running", "done", "failed", "cancelled"]),
  output: record.nullable(), error: record.nullable(),
});

export type DrawPrintSchemaChecks = [
  Assert<Same<z.infer<typeof drawingInfoSchema>, DrawingInfo>>,
  Assert<Same<z.infer<typeof printCapabilitiesSchema>, PrintCapabilities>>,
  Assert<Same<z.infer<typeof printerSchema>, Printer>>,
  Assert<Same<z.infer<typeof printerStatusSchema>, PrinterStatus>>,
  Assert<Same<z.infer<typeof sliceStartSchema>, SliceStart>>,
  Assert<Same<z.infer<typeof sliceSchema>, Slice>>,
  Assert<Same<z.infer<typeof printStartSchema>, PrintStart>>,
  Assert<Same<z.infer<typeof printCallSchema>, PrintCall>>,
];
