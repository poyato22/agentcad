// zod schemas mirroring types.ts. Every server response is parsed with one of these at the
// client boundary, so schema drift fails loudly (CoreError "validation") instead of leaking
// half-shaped data into the store. Unknown extra keys are stripped, not rejected.
// The `Same<...>` checks at the bottom fail the type check if a schema and its type drift apart.
import { z } from "zod";
import { partEdgeSchema } from "./builds";
import { PRESET_IDS } from "../lib/appearance";
import { suggestedSitesSchema } from "./browser";
import {
  ERROR_CODES, SETUP_PIECES, STEP_KINDS, SYSTEM_STATUSES,
  type AgentNode, type AgentsView, type RunNode,
  type SystemCheckRow, type SystemFix, type SystemInstallStart,
  type AgentInfo, type AgentRun, type Approval, type CancelAllResult, type ClaudeCodeStatus, type CoreErrorBody, type CoreStatusInfo,
  type KeyStatus, type MisesEvent, type ModelsInfo, type LocalTestResult, type LocalStatus, type LocalActionResult, type LocalPullEvent, type OnshapeTestResult, type PartFace, type PartHole, type PartMetrics, type PartSummary,
  type Profile, type SetupState, type SketchSummary, type Step, type ToolOutput,
  type LocalPull, type ModelEntry, type ModelMeta, type ParallelInfo, type VendorInfo,
} from "./types";
import type { DrawingInfo, PrintCall, PrintCapabilities, Printer, PrinterStatus, PrintStart, Slice, SliceStart } from "./types";

const record = z.record(z.string(), z.unknown());
const vec3 = z.tuple([z.number(), z.number(), z.number()]);

export const errorCodeSchema = z.enum(ERROR_CODES);
export const stepKindSchema = z.enum(STEP_KINDS);

export const runCostSchema = z.object({ usd: z.number().nullable(), tokens: z.number() });
/** The router's choice (U3c). Confidence is clamped to 0–1; an out-of-range or missing one reads 0. */
export const routedSchema = z.object({
  agent: z.string(), reason: z.string().default(""),
  confidence: z.number().min(0).max(1).catch(0).default(0),
});

export const agentRunSchema = z.object({
  id: z.string(), parent_id: z.string().nullable(), agent: z.string(), provider: z.string(), model: z.string(),
  task: z.string(), status: z.enum(["queued", "running", "done", "failed", "cancelled"]), branch: z.string().nullable(),
  onshape_calls: z.number(), input_tokens: z.number(), output_tokens: z.number(), usd: z.number(),
  created_at: z.number(), updated_at: z.number(), owner_pid: z.number().nullish(),
  external: z.boolean().optional(), session_ref: z.string().nullish(),
  // Plan 5 (C1) agent-management fields (enriched by run_view)
  project_id: z.string().nullish(), title: z.string().nullish(), pinned: z.boolean().optional(), archived: z.boolean().optional(),
  last_activity_at: z.number().nullish(), parent_run_id: z.string().nullish(),
  cost: runCostSchema.optional(),
  // Plan 8 G1 routing hint (shape is read leniently by the model warning chip)
  shape_complexity: z.unknown().optional(),
  // Plan 9 G-B (D27): {score, cls: engineering|mixed|design, per_part, reasons, source}
  design_class: z.unknown().optional(),
  turn_started_at: z.number().nullable().optional(), turn_usd0: z.number().nullable().optional(),
  // Plan 7 (U3c): lead/sub role and the router's choice
  role: z.enum(["lead", "sub"]).nullish(),
  routed: routedSchema.nullish(),
});

/** GET /runs/tree node: a run plus its worker children. */
export const runNodeSchema: z.ZodType<RunNode> = z.lazy(() =>
  agentRunSchema.extend({ children: z.array(runNodeSchema) }) as unknown as z.ZodType<RunNode>);

/** GET /projects/{id}/agents (U3c lead.agents_view): {project_id, lead: node|null, others}. A
 *  malformed waiting-message list reads as empty rather than failing the whole view. */
export const queuedMessageSchema = z.object({ message_id: z.string(), text: z.string(), position: z.number() });
export const agentNodeSchema: z.ZodType<AgentNode> = z.lazy(() =>
  agentRunSchema.extend({
    queued: z.array(queuedMessageSchema).catch([]).default([]),
    activity: z.unknown().optional().transform((v) => v ?? null),
    children: z.array(agentNodeSchema),
  }) as unknown as z.ZodType<AgentNode>);
export const agentsViewSchema: z.ZodType<AgentsView> = z.object({
  project_id: z.string(), lead: agentNodeSchema.nullable(), others: z.array(agentNodeSchema),
}) as unknown as z.ZodType<AgentsView>;

export const agentDetailSchema = z.object({
  name: z.string(), description: z.string(), model: z.string(), tools: z.array(z.string()).nullable(),
  builtin: z.boolean(), hidden: z.boolean(), body: z.string().nullable(),
});

export const cleanupResultSchema = z.object({ ids: z.array(z.string()), count: z.number().int() });
export const deleteRefusedSchema = z.object({ reason: z.enum(["running", "pending_approval"]), children: z.array(z.string()).default([]) });

/** Plan 9 B3's dock stream; an unknown value from a newer core reads as absent (type fallback). */
export const streamKindSchema = z.enum(["text", "sim", "view"]).optional().catch(undefined);

export const stepSchema = z.object({
  id: z.number().int(), run_id: z.string(), ts: z.number(), kind: stepKindSchema, payload: record,
  evidence_hash: z.string(), prev_hash: z.string().nullable(), collapsed: z.boolean(),
  stream: streamKindSchema,
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
  type: z.string(), run_id: z.string().nullable(), data: record, ts: z.number(), stream: streamKindSchema,
});

export const agentInfoSchema = z.object({
  name: z.string(), description: z.string(), model: z.string(),
  tools: z.array(z.string()).nullish(), builtin: z.boolean().optional(), hidden: z.boolean().optional(),
});

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

export const partFaceSchema = z.object({ id: z.string(), type: z.string(), center: vec3, normal: vec3.nullable(), area: z.number(), radius: z.number().optional() });

export const partMetricsSchema = z.object({
  valid: z.boolean(), volume_mm3: z.number(), area_mm2: z.number(), mass_g: z.number(),
  bbox: z.object({ min: vec3, max: vec3, size: vec3 }),
  center_of_mass: vec3, faces: z.number(), edges: z.number(), holes: z.array(partHoleSchema), solids: z.number(),
  face_list: z.array(partFaceSchema).optional(),
  edge_list: z.array(partEdgeSchema).optional(),
});

const ZERO3: [number, number, number] = [0, 0, 0];
/** Plan 9 B2: the part list's metrics, caught per field, so one bad or missing field (an old
 *  build's `holes` count, a catalog part without `area_mm2`) no longer nulls every measurement.
 *  A field that fails reads as a neutral value (0, [], valid; mass NaN, which lib/mass shows as
 *  "—"); a non-object stays null. */
export const partMetricsLenientSchema = z.object({
  valid: z.boolean().catch(true), volume_mm3: z.number().catch(0), area_mm2: z.number().catch(0), mass_g: z.number().catch(Number.NaN),
  bbox: z.object({ min: vec3, max: vec3, size: vec3 }).catch({ min: ZERO3, max: ZERO3, size: ZERO3 }),
  center_of_mass: vec3.catch(ZERO3), faces: z.number().catch(0), edges: z.number().catch(0),
  holes: z.array(partHoleSchema).catch([]), solids: z.number().catch(0),
  face_list: z.array(partFaceSchema).optional().catch(undefined),
  edge_list: z.array(partEdgeSchema).optional().catch(undefined),
});

/** Plan 9 B2: where a part's mass comes from (core mises.mass). */
export const MASS_SOURCES = ["built", "catalog", "sum", "unknown"] as const;

/** Plan 7 U8: core appearance.normalize's shape. */
export const appearanceSchema = z.object({
  preset: z.enum(PRESET_IDS).nullable(), color: z.string().nullable(),
  metalness: z.number(), roughness: z.number(), opacity: z.number(),
});

/** A row whose metrics don't parse keeps its tree place with metrics null (never drops the list). */
export const partSummarySchema = z.object({
  part_id: z.string(), vars: z.record(z.string(), z.number()), metrics: partMetricsLenientSchema.nullable().catch(null),
  mtime: z.number().nullable().transform((v) => v ?? 0),
  id: z.string().optional(), label: z.string().optional(), project_id: z.string().nullish(), parent_id: z.string().nullish(),
  order: z.number().optional(), color: z.string().nullish(), kind: z.enum(["part", "group", "catalog", "skeleton", "reference"]).optional(),
  version: z.number().int().optional(),
  // a malformed appearance falls back to the drafting look (the row is never dropped)
  appearance: appearanceSchema.nullish().catch(null),
  // G4 (core mises/looks.py): the look it shows while nobody gave it one (material, concept CMF)
  default_appearance: appearanceSchema.nullish().catch(null),
  // Plan 9 B2: the tree mass (volume x the part's material; groups sum their subtree), or null
  // with the reason in mass_note. Absent from older cores (the app falls back to metrics.mass_g).
  mass_g: z.number().nullish().catch(null), mass_source: z.enum(MASS_SOURCES).nullish().catch(null),
  mass_note: z.string().nullish().catch(null), material: z.string().nullish().catch(null),
});

export const coreErrorBodySchema = z.object({
  error: errorCodeSchema, message: z.string(),
  hint: z.string().nullable().default(null), retryable: z.boolean().default(false),
});

export const okSchema = z.object({ ok: z.literal(true) });
export const runIdSchema = z.object({ run_id: z.string() });
/** POST /runs answer: the run id, plus the router's choice when no agent was named (U3c). */
export const createRunResultSchema = z.object({
  run_id: z.string(), routed: routedSchema.nullish(), role: z.literal("lead").nullish(), new_lead: z.boolean().optional(),
  queued: z.boolean().optional(), message_id: z.string().nullish(), position: z.number().nullish(), notes: z.array(z.string()).optional(),
});
/** `failed` defaults to [] so a core that predates it still parses. */
export const cancelAllSchema = z.object({
  cancelled: z.array(z.string()),
  failed: z.array(z.object({ run_id: z.string(), error: z.string() })).default([]),
});

/** One structured question of a batched ui_ask_user (core ui_tools.AskItem). */
export const askItemSchema = z.object({
  id: z.string(), prompt: z.string(), options: z.array(z.string()).default([]),
  multi: z.boolean().default(false), rank: z.boolean().default(false), allow_text: z.boolean().default(true),
});

/** Data of a `question_asked` event (the run id comes from the event itself). `questions` is the
 *  batched shape (C3); absent for a single question. */
export const questionAskedSchema = z.object({
  question_id: z.string(), question: z.string(), options: z.array(z.string()).default([]),
  // a malformed batch falls back to the plain question (whose text lists every prompt)
  questions: z.array(askItemSchema).max(6).catch([]),
  image_id: z.string().regex(/^[A-Za-z0-9_-]{1,96}$/).optional().catch(undefined),
});

// ---- Plan 3: profile, keys, setup, Claude Code, Onshape test

/** Defaults mirror core profile.Profile, so a core that adds a field later still parses here. */
export const profileSchema = z.object({
  display_name: z.string().default(""), units: z.literal("mm").default("mm"), default_material: z.string().default("PLA"),
  default_agent: z.string().default("designer"), default_model: z.string().nullable().default(null),
  onshape_calls_year_cap: z.number().default(2500), onshape_calls_run_cap: z.number().default(200),
  max_parallel_agents: z.union([z.number().int(), z.literal("auto")]).default("auto"), local_endpoints: z.record(z.string(), z.string()).default({}),
  theme: z.enum(["system", "light", "dark"]).default("system"),
  hidden_agents: z.array(z.string()).optional(),
  viewer_background: z.object({ mode: z.enum(["theme", "solid", "gradient"]), colors: z.array(z.string()) }).optional().catch(undefined),
  // Plan 7 R3: the agent's curated browse list (apart in api/browser.ts)
  suggested_sites: suggestedSitesSchema.optional(),
  browser_fetches_per_run: z.number().int().optional(),
  // Plan 10 E3: extra environment for Claude Code (CA bundle path / proxy URL; credentials masked)
  claude_code_env: z.record(z.string(), z.string()).optional(),
  setup_completed_at: z.number().nullable().default(null),
  // the core's Profile carries `schema_version` and `printers`; both are stripped here and never sent
  // back in PUT /profile (the core merges the patch, so they are kept; printers go through /printers)
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
  Assert<Same<z.infer<typeof partFaceSchema>, PartFace>>,
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
  Assert<Same<z.infer<typeof runCostSchema>, import("./types").RunCost>>,
  Assert<Same<z.infer<typeof agentDetailSchema>, import("./types").AgentDetail>>,
  Assert<Same<z.infer<typeof cleanupResultSchema>, import("./types").CleanupResult>>,
  Assert<Same<z.infer<typeof deleteRefusedSchema>, import("./types").DeleteRefused>>,
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

export const systemFixSchema = z.object({
  kind: z.enum(["install", "open_url", "setting"]),
  label: z.string(),
  dep: z.string().optional(),
  url: z.string().optional(),
  setting: z.string().optional(),
});

export const systemCheckRowSchema = z.object({
  id: z.string(), name: z.string(), status: z.enum(SYSTEM_STATUSES),
  version: z.string().nullable(), path: z.string().nullable(),
  detail: z.string(), fix: systemFixSchema.nullable(),
});

export const systemInstallStartSchema = z.object({ ok: z.boolean(), fix: systemFixSchema.nullable() });

export type SystemSchemaChecks = [
  Assert<Same<z.infer<typeof systemFixSchema>, SystemFix>>,
  Assert<Same<z.infer<typeof systemCheckRowSchema>, SystemCheckRow>>,
  Assert<Same<z.infer<typeof systemInstallStartSchema>, SystemInstallStart>>,
];

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
