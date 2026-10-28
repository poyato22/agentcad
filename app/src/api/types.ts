// TS types for the real core JSON (core/src/mises: store.py, events.py, types.py, server/app.py,
// server/ui_api.py). Field names are exactly the server's. Times are unix seconds (float).
// Runtime validation lives in schemas.ts; keep both in step.

export type ErrorCode = "stale_version" | "budget_exhausted" | "needs_approval" | "transient" | "invalid_input"
  | "out_of_scope" | "denied" | "not_found" | "tool_failed" | "cancelled";

export const ERROR_CODES = [
  "stale_version", "budget_exhausted", "needs_approval", "transient", "invalid_input",
  "out_of_scope", "denied", "not_found", "tool_failed", "cancelled",
] as const satisfies readonly ErrorCode[];

/** "queued": waiting for a parallel-agents slot (event `run_queued`); it starts when one frees. */
export type RunStatus = "queued" | "running" | "done" | "failed" | "cancelled";

export interface AgentRun {
  id: string; parent_id: string | null; agent: string; provider: string; model: string;
  task: string; status: RunStatus; branch: string | null; onshape_calls: number; input_tokens: number;
  output_tokens: number; usd: number; created_at: number; updated_at: number;
  /** The core process driving the run (null: from before owner tracking). Not shown in the UI. */
  owner_pid?: number | null;
  /** Driven by an outside agent (POST /runs/external, ACP, the REPL, the user's Claude Code).
   *  Persisted by the core; absent from cores older than the flag (treated as false).
   *  External runs never take messages, except Claude Code runs (provider "claude-code"): the
   *  core continues or resumes their session. See ui/console/command/isExternal.ts. */
  external?: boolean;
  /** Claude Code runs: the session id Mises resumes with `--resume`. Not shown in the UI. */
  session_ref?: string | null;
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

/** `sketch_id` (POST /sketches): the run gets that sketch as data (and its images for vision models). */
export interface NewRunBody { agent: string; prompt: string; model_ref?: string | null; edit_scope?: string[]; read_scope?: string[]; sketch_id?: string }

/** edited_payload is not sent in v1. */
export interface DecisionBody { payload_hash: string; approve: boolean }

/** POST /runs/cancel_all. `failed` lists runs the core could not cancel. */
export interface CancelAllResult { cancelled: string[]; failed: { run_id: string; error: string }[] }

/** GET /agents item. `model` may be "inherit". */
export interface AgentInfo { name: string; description: string; model: string }

/** signed_in / signed_out / not_installed: the "claude-code" vendor (Plan 3 Task 3). */
export type KeySource = "env" | "keychain" | "none" | "not_needed" | "signed_in" | "signed_out" | "not_installed";
/** Why the core points a model out: "best" (strongest at CAD), "local" (a researched local model
 *  that fits this Mac), "free" (OpenRouter free). */
export type Recommended = "best" | "local" | "free";
/** Badges of one listed model (built from the core's per-vendor `entries`). */
export interface ModelMeta { recommended: Recommended | null; note: string | null; free: boolean }

export interface VendorInfo {
  vendor: string; key: KeySource;
  /** Models the vendor offers, when the core lists them: claude-code aliases (sonnet, opus, ...),
   *  or full refs for live lists (`ollama/<name>`, `lmstudio/<id>`, `openrouter/<id>`). */
  models?: string[];
  /** Every listed model is free (OpenRouter's free tool-capable models). */
  free?: boolean;
  /** Why the list is empty (e.g. "Ollama isn't running ..."). */
  hint?: string | null;
  /** Vendor-level badge and one-line note: not sent by the current core (it badges each model in
   *  `entries`); tolerated for a core that adds them. Untrusted text. */
  recommended?: Recommended | null;
  note?: string | null;
  /** Per-model badges from the core's `entries`, keyed by full ref (e.g. "claude-code/opus"). */
  model_meta?: Record<string, ModelMeta>;
}
/** One entry of GET /models `recommended` (`model` is a full ref, e.g. "claude-code/opus"). */
export interface ModelEntry { model: string; recommended: Recommended | null; note: string | null }
export interface ModelsInfo {
  default: string; vendors: VendorInfo[];
  /** Every entry with a `recommended` badge: best first, then local, then free. */
  recommended?: ModelEntry[];
}

export interface CoreStatusInfo {
  core_version: string; onshape: "connected" | "not_configured"; onshape_calls_year: number;
  onshape_cap_year: number; runs_running: number; approvals_pending: number;
  /** Core data folder (absolute path); absent on older cores. */
  data_dir?: string | null;
  runs_queued?: number;
  /** How many agents run at once for the default model, and why ("M5 Pro · 24 GB · cloud model -> 3"). */
  parallel?: ParallelInfo;
}

/** GET /status `parallel`: how many runs may work at once, and why. */
export interface ParallelInfo { limit: number; auto: boolean; reason: string }

export type Vec3 = [number, number, number];

/** One detected hole in PartMetrics.holes (cad/metrics). */
export interface PartHole { diameter_mm: number; axis: Vec3; center: Vec3; depth_mm: number }

/** One face of a part (core cad/worker.py _face_list): ids "f0".. name faces for study fixtures/loads. */
export interface PartFace { id: string; type: string; center: Vec3; normal: Vec3 | null; area: number }

export interface PartMetrics {
  valid: boolean; volume_mm3: number; area_mm2: number; mass_g: number;
  bbox: { min: Vec3; max: Vec3; size: Vec3 };
  center_of_mass: Vec3; faces: number; edges: number; holes: PartHole[]; solids: number;
  /** Every face (parts built before Plan 4 have none). */
  face_list?: PartFace[];
}

export interface PartSummary { part_id: string; vars: Record<string, number>; metrics: PartMetrics | null; mtime: number }

/** From the Rust shell's `core_info` command (camelCase), or the dev/test env fallback. */
export interface CoreInfo {
  baseUrl: string; wsUrl: string; token: string; owned: boolean;
  /** The core process (from the shell's server.json); absent in dev/test (VITE_MISES_*). */
  pid?: number | null;
}

/** Error JSON of every non-2xx core response except 401/422. */
export interface CoreErrorBody { error: ErrorCode; message: string; hint: string | null; retryable: boolean }

// ---- Plan 3: profile, keys, setup, Claude Code, Onshape test, UI context (core server/profile_api.py)

/** UI theme: follow the OS, or force light/dark. Applied via `data-theme` on <html>. */
export type Theme = "system" | "light" | "dark";

/** data_home()/profile.json. Never holds secrets. */
export interface Profile {
  display_name: string; units: "mm"; default_material: string; default_agent: string;
  /** e.g. "claude-code/sonnet", "anthropic/claude-sonnet-5-5", "ollama/qwen3"; null = the core's default. */
  default_model: string | null;
  onshape_calls_year_cap: number; onshape_calls_run_cap: number;
  /** "auto" (default): from this Mac's specs and the model; a number is a manual limit (1-16). */
  max_parallel_agents: number | "auto";
  /** {"ollama": "http://localhost:11434/v1", "lmstudio": ...} */
  local_endpoints: Record<string, string>;
  /** UI theme (default "system"). */
  theme: Theme;
  setup_completed_at: number | null;
}

/** PUT /profile body: a partial merge. setup_completed_at is set only by POST /setup/complete. */
export type ProfilePatch = Partial<Omit<Profile, "setup_completed_at" | "units">>;

/** Where a secret comes from. The secret itself is never returned by any route. */
export type SecretSource = "env" | "keychain" | "none";

/** GET /keys item, and the answer of POST/DELETE /keys/{vendor}. */
export interface KeyStatus { vendor: string; present: boolean; source: SecretSource }

export type SetupPiece = "model" | "onshape" | "profile";
export const SETUP_PIECES = ["model", "onshape", "profile"] as const satisfies readonly SetupPiece[];

/** GET /setup. complete = setup_completed_at is set (the wizard was finished, possibly without a
 *  model); "profile" stays in `missing` until then. has_model is separate: the app shows a
 *  "connect a model" notice when complete && !has_model. Onshape is optional. */
export interface SetupState { complete: boolean; has_model: boolean; has_onshape: boolean; missing: SetupPiece[] }

/** POST /onshape/test: exactly one ledgered Onshape call. Wrong keys → {ok:false, error:"denied"}. */
export interface OnshapeTestResult { ok: boolean; user_name?: string | null; error?: string | null }
/** POST /local/test: does a local model server answer at this URL? */
export interface LocalTestResult { ok: boolean; error?: string | null }

/** GET /local/status (Ollama). `recommended`: researched local models; `fits`: fits this Mac's memory. */
export interface LocalStatus {
  installed: boolean; running: boolean; version: string | null;
  models: { name: string; size_gb: number | null }[];
  recommended: { name: string; size_gb: number; fits: boolean; note: string }[];
}
/** POST /local/start and POST /local/pull. `note`: tell the user (e.g. `ollama serve` keeps running). */
/** POST /local/pull: {ok:false, already:true} while that model downloads; error "too_many_pulls" past 2; POST /local/pull/cancel {model} -> {ok}. */
export interface LocalActionResult { ok: boolean; running?: boolean; note?: string; error?: string; hint?: string; already?: boolean }
/** Data of a `local_pull` event (run_id null). status "success", "error" (with `error`) or "cancelled" ends the download. */
export interface LocalPullEvent { model: string; completed: number; total: number; status: string; error?: string }
/** Alias used by the model picker. */
export type LocalPull = LocalPullEvent;

/** GET /claude-code/status (from the user's own `claude auth status`; non-secret fields only). */
export interface ClaudeCodeStatus { installed: boolean; version: string | null; signed_in: boolean; auth_method: string | null }

/** PUT /context: what the user has selected, for the agent's mises_context_get tool. */
export interface UiContext { selected_part_id: string | null; selected_run_id: string | null; picks: unknown[] }

// ---- Sketch page (core server/sketch_api.py, sketch.py). Coordinates are mm, origin bottom-left, +y up.

export type SketchStrokeKind = "freehand" | "line" | "rect" | "circle";
export interface SketchStroke {
  id: string; kind: SketchStrokeKind; points: [number, number][]; closed: boolean;
  center?: [number, number]; radius_mm?: number; label: string;
}
export interface SketchDimension { stroke_id: string; text: string; value_mm: number | null; kind: "length" | "radius" | "diameter" | "angle" | "note" }
/** POST /sketches body. The photo (PNG/JPEG/HEIC, ≤ 10 MB) is checked and converted to PNG by the core. */
export interface SketchUpload {
  sketch: { units: "mm"; width_mm: number; height_mm: number; strokes: SketchStroke[]; dimensions: SketchDimension[]; notes: string };
  photo?: { data_b64: string; name?: string } | null;
  /** The drawing rasterised by the app (PNG), for vision-capable models. */
  render_png_b64?: string | null;
}
/** The stored photo: JPEG sized for a model's image input (base64 under the 5 MB per-image limit). */
export interface SketchPhotoInfo { source_type: "png" | "jpeg" | "heic"; media_type: "image/jpeg" | "image/png"; width: number; height: number; bytes: number }
/** POST /sketches and GET /sketches/{id}. */
export interface SketchSummary { sketch_id: string; strokes: number; dimensions: number; photo: SketchPhotoInfo | null; render: boolean }

// ---- Plan 4: drawings and printing (core server/drawing_api.py, server/print_api.py)

/** GET /parts/{id}/drawing.json: the A4 sheet the SVG/DXF are drawn on. */
export interface DrawingInfo {
  /** Sheet size in mm ([297, 210] = A4 landscape). */
  sheet: [number, number]; scale: number; scale_text: string; views: string[];
  overall_mm: Vec3; holes: string[]; label: string; material: string; date: string; step_mtime_ns: number;
}

export interface PrintMaterial { name: string; density_g_cm3: number; usd_per_kg: number }
/** GET /print/capabilities. A missing slicer comes with the exact install command. */
export interface PrintCapabilities {
  slicer: { installed: boolean; path: string | null; install_command: string };
  kinds: { kind: string; available: boolean; note: string | null }[];
  models: string[];
  materials: PrintMaterial[];
}

/** GET /printers item. The API key lives in the Keychain; only `has_key` comes back. */
export interface Printer { id: string; kind: string; host: string; name: string; model: string; has_key: boolean }
/** POST /printers body. `api_key` goes out once, in the body, and is never returned. */
export interface NewPrinterBody { name: string; host: string; api_key: string; model?: string; kind?: string }

export interface PrinterJob { id?: number; progress?: number; time_remaining?: number; time_printing?: number }
/** GET /printers/{id}/status. Unreachable is a 200 with reachable:false and an error. */
export interface PrinterStatus {
  printer_id: string; reachable: boolean; state: string | null; sendable: boolean;
  temp_nozzle: number | null; temp_bed: number | null; job: PrinterJob | null; error: string | null; hint?: string | null;
}

/** POST /slices answer (the slice runs under the shared "ui" run). */
export interface SliceStart { slice_id: string; status: string; run_id: string; call_id: string }
export interface SliceEstimate { time_s: number; time_text: string; filament_mm: number; filament_g: number; cost_usd: number }
export type SliceStatus = "running" | "done" | "failed" | "cancelled";
/** GET /slices/{id}. */
export interface Slice {
  slice_id: string; status: SliceStatus; part_id: string; printer_id: string; material: string; model: string;
  created_at: number; estimate: SliceEstimate | null; preview: boolean; error: string | null; hint: string | null;
  sent_to: string | null;
}

/** POST /prints answer: the irreversible print_send call; its approval card is in the console. */
export interface PrintStart { call_id: string; run_id: string; status: string }
/** GET /prints/{call_id}. `output` is the tool's ToolOutput; `error` the core's error JSON. */
export interface PrintCall {
  call_id: string; run_id: string; tool: string; status: "running" | "done" | "failed" | "cancelled";
  output: Record<string, unknown> | null; error: Record<string, unknown> | null;
}
