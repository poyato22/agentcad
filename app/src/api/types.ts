// TS types for the real core JSON (core/src/mises: store.py, events.py, types.py, server/app.py,
// server/ui_api.py). Field names are exactly the server's. Times are unix seconds (float).
// Runtime validation lives in schemas.ts; keep both in step.

export type ErrorCode = "stale_version" | "budget_exhausted" | "needs_approval" | "transient" | "invalid_input"
  | "out_of_scope" | "denied" | "not_found" | "tool_failed" | "cancelled";

export const ERROR_CODES = [
  "stale_version", "budget_exhausted", "needs_approval", "transient", "invalid_input",
  "out_of_scope", "denied", "not_found", "tool_failed", "cancelled",
] as const satisfies readonly ErrorCode[];

export type RunStatus = "running" | "done" | "failed" | "cancelled";

export interface AgentRun {
  id: string; parent_id: string | null; agent: string; provider: string; model: string;
  task: string; status: RunStatus; branch: string | null; onshape_calls: number; input_tokens: number;
  output_tokens: number; usd: number; created_at: number; updated_at: number;
  /** The core process driving the run (null: from before owner tracking). Not shown in the UI. */
  owner_pid?: number | null;
}

export type StepKind = "message" | "plan" | "tool_call" | "tool_result" | "diff" | "decision" | "error"
  | "onshape_call" | "sim_run" | "measure" | "verify" | "compaction";

export const STEP_KINDS = [
  "message", "plan", "tool_call", "tool_result", "diff", "decision", "error",
  "onshape_call", "sim_run", "measure", "verify", "compaction",
] as const satisfies readonly StepKind[];

export interface Step {
  id: number; run_id: string; ts: number; kind: StepKind; payload: Record<string, unknown>;
  evidence_hash: string; prev_hash: string | null; collapsed: boolean;
}

export interface ToolOutput {
  ok: boolean; summary: string; data: Record<string, unknown>; handles: string[];
  warnings: string[]; step_kind: StepKind;
}

export type ApprovalStatus = "pending" | "approved" | "rejected" | "expired" | "executed";

export interface Approval {
  id: string; run_id: string; action: string; payload: Record<string, unknown>;
  payload_hash: string; microversion: string | null; deltas: Record<string, unknown>; status: ApprovalStatus;
  expires_at: number; decided_by: string | null; decided_at: number | null; edited_payload: Record<string, unknown> | null;
}

export interface MisesEvent { type: string; run_id: string | null; data: Record<string, unknown>; ts: number }

/** Built from a `question_asked` event (there is no GET /questions). */
export interface Question { question_id: string; run_id: string; question: string; options: string[] }

export interface NewRunBody { agent: string; prompt: string; model_ref?: string | null; edit_scope?: string[]; read_scope?: string[] }

/** edited_payload is not sent in v1. */
export interface DecisionBody { payload_hash: string; approve: boolean }

/** POST /runs/cancel_all. `failed` lists runs the core could not cancel. */
export interface CancelAllResult { cancelled: string[]; failed: { run_id: string; error: string }[] }

/** GET /agents item. `model` may be "inherit". */
export interface AgentInfo { name: string; description: string; model: string }

export type KeySource = "env" | "keychain" | "none" | "not_needed";
export interface ModelsInfo { default: string; vendors: { vendor: string; key: KeySource }[] }

export interface CoreStatusInfo {
  core_version: string; onshape: "connected" | "not_configured"; onshape_calls_year: number;
  onshape_cap_year: number; runs_running: number; approvals_pending: number;
}

export type Vec3 = [number, number, number];

/** One detected hole in PartMetrics.holes (cad/metrics). */
export interface PartHole { diameter_mm: number; axis: Vec3; center: Vec3; depth_mm: number }

export interface PartMetrics {
  valid: boolean; volume_mm3: number; area_mm2: number; mass_g: number;
  bbox: { min: Vec3; max: Vec3; size: Vec3 };
  center_of_mass: Vec3; faces: number; edges: number; holes: PartHole[]; solids: number;
}

export interface PartSummary { part_id: string; vars: Record<string, number>; metrics: PartMetrics | null; mtime: number }

/** From the Rust shell's `core_info` command (camelCase), or the dev/test env fallback. */
export interface CoreInfo { baseUrl: string; wsUrl: string; token: string; owned: boolean }

/** Error JSON of every non-2xx core response except 401/422. */
export interface CoreErrorBody { error: ErrorCode; message: string; hint: string | null; retryable: boolean }
