// TS types for the real core JSON (core/src/mises: store.py, events.py, types.py, server/app.py,
// server/ui_api.py). Field names are exactly the server's. Times are unix seconds (float).
// Runtime validation lives in schemas.ts; keep both in step.
import type { PartEdge, PartKind } from "./builds";
import type { Appearance } from "../lib/appearance";
import type { SuggestedSite } from "./browser";

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
  // ---- Plan 5 (C1) agent-management fields; enriched by the core's run_view. Optional so an
  //      older core / fixture that predates them still parses (the UI falls back).
  /** The build this run belongs to. */
  project_id?: string | null;
  /** A user-renamable label; falls back to the task in the UI. */
  title?: string | null;
  pinned?: boolean;
  archived?: boolean;
  /** Bumped as the run works; the manager sorts by it (falls back to updated_at). */
  last_activity_at?: number | null;
  /** The API name of the one parent column (an orchestrator's worker points at its parent). */
  parent_run_id?: string | null;
  /** Plan 7 U3c: the build's lead agent ("lead") or one of its sub-agents ("sub"); null otherwise. */
  role?: "lead" | "sub" | null;
  /** Rolled-up cost: dollars (null when unpriced) and total tokens. */
  cost?: RunCost;
  /** Plan 8 G1: how shape-heavy the prompt looks (the router prefers strong vision models for it).
   *  Kept raw: ui/console/cards/critique/shapeWarning.ts reads a score, a level or {level, score, keywords}. */
  shape_complexity?: unknown;
  /** The lead's per-turn caps window (core budget.start_turn): null until a turn starts. */
  turn_started_at?: number | null;
  turn_usd0?: number | null;
  /** How the router chose this run's agent (POST /runs with no `agent`). A sub-agent's task
   *  title is its `title` (core lead.task_title). */
  routed?: Routed | null;
}

/** A message waiting for a busy lead (core lead.queued_messages), oldest first. */
export interface QueuedMessage { message_id: string; text: string; position: number }

/** GET /projects/{id}/agents node (core lead._node): a run_view whose `task` is the run's task
 *  title (its title, else its prompt's first line), plus its waiting messages, live activity
 *  (the agent_activity shape, untrusted; null when none) and its sub-agents, oldest first. */
export interface AgentNode extends AgentRun { queued: QueuedMessage[]; activity: unknown; children: AgentNode[] }

/** GET /projects/{id}/agents (core lead.agents_view): the build's lead with its sub-tree, and its
 *  other top-level runs (started with an explicit agent, earlier leads). */
export interface AgentsView { project_id: string; lead: AgentNode | null; others: AgentNode[] }

/** The router's choice when POST /runs is sent with no `agent` (U3c): the agent it picked, a short
 *  reason ("system build"), and a 0–1 confidence. Shown in the UI as a small chip. */
export interface Routed { agent: string; reason: string; confidence: number }

/** Rolled-up run cost (run_view `cost`). */
export interface RunCost { usd: number | null; tokens: number }

/** GET /runs/tree node: a run_view plus its worker children (the orchestrator → worker forest). */
export interface RunNode extends AgentRun { children: RunNode[] }

/** POST /runs/cleanup answer (dry-run or real): the ids removed (or that would be) and the count. */
export interface CleanupResult { ids: string[]; count: number }

/** Why DELETE /runs/{id} was refused (409): a run that is still running (with its blocking worker
 *  ids) or awaiting your approval. */
export interface DeleteRefused { reason: "running" | "pending_approval"; children: string[] }

/** PATCH /runs/{id} body: a partial rename / pin / archive. */
export interface RunPatch { title?: string; pinned?: boolean; archived?: boolean }

/** POST /runs/cleanup body. */
export interface CleanupBody { project_id?: string | null; older_than_days?: number | null; status_in?: string[]; dry_run?: boolean }

/** POST /agents and PUT /agents/{name} body. `tools` null means "inherit the defaults". */
export interface AgentDefBody { name?: string; description?: string; model?: string; tools?: string[] | null; body?: string }

export type StepKind = "message" | "plan" | "tool_call" | "tool_result" | "diff" | "decision" | "error"
  | "onshape_call" | "sim_run" | "measure" | "verify" | "compaction";

export const STEP_KINDS = [
  "message", "plan", "tool_call", "tool_result", "diff", "decision", "error",
  "onshape_call", "sim_run", "measure", "verify", "compaction",
] as const satisfies readonly StepKind[];

/** Plan 9 D4: the core tags every step and event with the stream it belongs to (studies,
 *  renders / views, or the conversation). Optional: older fixtures and cores omit it. */
export type Stream = "text" | "sim" | "view";

export interface Step {
  id: number; run_id: string; ts: number; kind: StepKind; payload: Record<string, unknown>;
  evidence_hash: string; prev_hash: string | null; collapsed: boolean; stream?: Stream;
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

export interface MisesEvent { type: string; run_id: string | null; data: Record<string, unknown>; ts: number; stream?: Stream }

/** One structured question of a batched `ui_ask_user` (C3): an id for the answer, the prompt,
 *  options and how to pick them. `multi`: several options; `rank`: order them (first = most
 *  important); `allow_text`: the user may answer in their own words. */
export interface AskItem { id: string; prompt: string; options: string[]; multi: boolean; rank: boolean; allow_text: boolean }

/** Built from a `question_asked` event (there is no GET /questions). `items` is the batched shape
 *  (up to 3 structured questions); when present the thread shows a single interview card and the
 *  answer is a JSON object `{id: value}`. A single question has no `items`. */
export interface Question { question_id: string; run_id: string; question: string; options: string[]; items?: AskItem[] }

/** `sketch_id` (POST /sketches): the run gets that sketch as data (and its images for vision models).
 *  `project_id` (C1): the build the run belongs to. The app always sends the active build, so a
 *  run never lands under the profile's build while a tab switch is still being saved. */
export interface NewRunBody { agent?: string; prompt: string; model_ref?: string | null; edit_scope?: string[]; read_scope?: string[]; sketch_id?: string; project_id?: string }

/** POST /runs answer. With no `agent` (U3c lead.submit) it also carries the routing, `new_lead`,
 *  `queued` (the lead was busy: the message waits, delivered at its turn boundary — then
 *  `message_id` and `position`), and `notes` (what the existing lead ignored, e.g. a model). */
export interface CreateRunResult {
  run_id: string; routed?: Routed | null; role?: "lead" | null; new_lead?: boolean; queued?: boolean;
  message_id?: string | null; position?: number | null; notes?: string[];
}

/** `agent_announce` event kinds (U3c announce.KINDS): the clear things an agent did, shown as a
 *  card in the thread (R16) whatever the model says. view_presented / critique_round are Plan 8
 *  G1's (geometry quality) cards, their fields in `ref`. */
export const ANNOUNCE_KINDS = ["part_created", "agent_spawned", "assembly_added", "bom_added", "part_shown", "drawing_imported", "view_presented", "critique_round"] as const;
export type AnnounceKind = (typeof ANNOUNCE_KINDS)[number];

/** Where an announcement points: a part, a child run, an assembly instance, a BOM row, a part-card
 *  snapshot, a drawing import. All optional; the card jumps to whichever it carries. */
export interface AnnounceRef {
  part_id?: string | null; run_id?: string | null; instance_id?: string | null;
  bom_id?: string | null; snapshot_id?: string | null; catalog_id?: string | null; import_id?: string | null;
}

/** Parsed `agent_announce` event (U3c). `title` is a short line; `detail` an optional second line.
 *  Both are untrusted model/tool text and render as text. */
export interface Announcement {
  id: string; run_id: string;
  /** One of ANNOUNCE_KINDS; a kind from a newer core still shows (with a generic icon). */
  kind: AnnounceKind | (string & {});
  title: string; detail: string | null; ref: AnnounceRef; ts: number;
}

/** One move of an agent's animation: a joint from → to (joint values: revolute deg, slider mm,
 *  cylindrical [deg, mm], ball [rx, ry, rz]) over duration_s. */
export interface AnimateStep { joint_id: string; from: number | number[]; to: number | number[]; duration_s: number }

/** `assembly_animate` event (U3c assembly_animate, R19), normalised: the single-joint form
 *  {joint_id, from, to, duration_s} and the `sequence` form both become `steps`, played one after
 *  another, `loops` times (1 = once). */
export interface AnimateSpec { project_id: string | null; steps: AnimateStep[]; loops: number }

/** edited_payload is not sent in v1. */
export interface DecisionBody { payload_hash: string; approve: boolean }

/** POST /runs/cancel_all. `failed` lists runs the core could not cancel. */
export interface CancelAllResult { cancelled: string[]; failed: { run_id: string; error: string }[] }

/** GET /agents item. `model` may be "inherit". Plan 5 (C1) adds `tools` (null = inherit the
 *  defaults), `builtin` (a built-in agent can be hidden but not edited or deleted) and `hidden`
 *  (the profile's hidden_agents). Optional so older cores/fixtures still parse. */
export interface AgentInfo { name: string; description: string; model: string; tools?: string[] | null; builtin?: boolean; hidden?: boolean }

/** GET /agents/{name}: one agent with its instructions. `body` is null for a built-in (its
 *  prompt isn't exposed). PUT /agents/{name} without `body` keeps the current instructions. */
export interface AgentDetail {
  name: string; description: string; model: string; tools: string[] | null; builtin: boolean; hidden: boolean; body: string | null;
}

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
/** `radius`: a cylinder's (U2a; parts built before it have none). */
export interface PartFace { id: string; type: string; center: Vec3; normal: Vec3 | null; area: number; radius?: number }

export interface PartMetrics {
  valid: boolean; volume_mm3: number; area_mm2: number; mass_g: number;
  bbox: { min: Vec3; max: Vec3; size: Vec3 };
  center_of_mass: Vec3; faces: number; edges: number; holes: PartHole[]; solids: number;
  /** Every face (parts built before Plan 4 have none). */
  face_list?: PartFace[];
  /** Every edge (Plan 5 C6): what fillet / chamfer picks name. */
  edge_list?: PartEdge[];
}

/**
 * GET /parts row. The Plan 5 part-tree fields (C1) are optional so a core or fixture from before
 * builds still parses; `id` equals `part_id`. `mtime` is 0 for a group/catalog row with no build.
 */
export interface PartSummary {
  part_id: string; vars: Record<string, number>; metrics: PartMetrics | null; mtime: number;
  id?: string; label?: string; project_id?: string | null; parent_id?: string | null; order?: number;
  color?: string | null; kind?: PartKind; version?: number;
  /** Plan 7 U8: the part's material (lib/appearance.ts); absent or null = the drafting look. */
  appearance?: Appearance | null;
}

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
  /** Built-in agents the user has hidden (custom agents are deleted outright, not hidden).
   *  Optional: a core/fixture that predates it reads as "none hidden". */
  hidden_agents?: string[];
  /** Plan 7 U8: the viewer's background. Optional: a core/fixture that predates it reads as "theme". */
  viewer_background?: ViewerBackground;
  /** Curated, user-editable sites the agent browses without an approval card (Plan 7 R3).
   *  Optional: a core/fixture that predates it reads as the empty list in the UI. */
  suggested_sites?: SuggestedSite[];
  /** Web fetches (sites searched + pages opened) one agent run may make (1-1000; core default 60). */
  browser_fetches_per_run?: number;
  setup_completed_at: number | null;
}

/** The viewer's background: the theme's drafting sheet, one solid color, or a two-stop
 *  (top, bottom) gradient. */
export interface ViewerBackground { mode: "theme" | "solid" | "gradient"; colors: string[] }

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

export type SketchStrokeKind = "freehand" | "line" | "polyline" | "rect" | "circle" | "arc" | "slot";
/** `points` by kind: line 2 ends; polyline / freehand a chain; rect 4 corners; circle samples (exact:
 *  center + radius_mm); arc start, a point on the arc, end; slot the 2 end centres (radius_mm = half
 *  width). `corner_r`: a fillet radius per vertex (polyline, rect). Construction = reference only. */
export interface SketchStroke {
  id: string; kind: SketchStrokeKind; points: [number, number][]; closed: boolean;
  center?: [number, number]; radius_mm?: number; label: string;
  construction?: boolean; corner_r?: number[];
}
/** A constraint target: a vertex (points index; a circle's 0 is its centre), an edge (i: points[i] → [i+1]) or the stroke. */
export interface SketchRef { stroke_id: string; vertex?: number; edge?: number }
export type SketchConstraintKind =
  | "horizontal" | "vertical" | "equal" | "coincident" | "distance" | "diameter" | "radius"
  /** offsets along x / y between 2 vertices; a vertex (refs[0]) on a circle or arc (refs[1]; a slot: its first end) */
  | "distance_x" | "distance_y" | "point_on_circle";
export interface SketchConstraint { id: string; kind: SketchConstraintKind; refs: SketchRef[]; value_mm?: number }
export interface SketchDimension { stroke_id: string; text: string; value_mm: number | null; kind: "length" | "radius" | "diameter" | "angle" | "note" }
/** POST /sketches body. The photo (PNG/JPEG/HEIC, ≤ 10 MB) is checked and converted to PNG by the core. */
export interface SketchUpload {
  sketch: {
    units: "mm"; width_mm: number; height_mm: number; strokes: SketchStroke[]; dimensions: SketchDimension[]; notes: string;
    constraints?: SketchConstraint[]; extrude_mm?: number | null;
  };
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

// ---- System check (GET /system/check) and installers (POST /system/install/{dep}) --------------
// A1 owns the client-side shape; C6 owns the real core routes. The fields mirror the plan's
// System check contract; `url` (for open_url) and `setting` (for setting) ride on the fix object
// alongside the contract's {kind, label, dep?}.
export const SYSTEM_STATUSES = ["ok", "missing", "outdated", "error", "optional_missing"] as const;
export type SystemCheckStatus = (typeof SYSTEM_STATUSES)[number];
export type SystemFixKind = "install" | "open_url" | "setting";
export interface SystemFix { kind: SystemFixKind; label: string; dep?: string; url?: string; setting?: string }
export interface SystemCheckRow {
  id: string; name: string; status: SystemCheckStatus; version: string | null; path: string | null;
  detail: string; fix: SystemFix | null;
}
/** Deps POST /system/install/{dep} accepts; the body carries no arguments (Review Focus 3). */
export const INSTALL_DEPS = ["prusaslicer", "calculix", "ollama", "openfoam"] as const;
export type InstallDep = (typeof INSTALL_DEPS)[number];
/** A dep name from core data (a system-check row's fix) the app is willing to send. */
export const isInstallDep = (v: unknown): v is InstallDep => typeof v === "string" && (INSTALL_DEPS as readonly string[]).includes(v);
/** POST /system/install/{dep} answer: install started, or refused with a fix (e.g. brew missing). */
export interface SystemInstallStart { ok: boolean; fix: SystemFix | null }
/** `system_install` event: one output line, then a terminal frame (done:true, ok set). */
export interface SystemInstallEvent { dep: string; line: string; done: boolean; ok: boolean | null }

// ---- Agent activity (agent_activity event; GET /runs/{id}.activity) -----------------------------
export const ACTIVITY_STATES = [
  "queued", "starting", "thinking", "writing", "tool", "waiting_approval", "waiting_user", "idle", "done",
] as const;
export type ActivityState = (typeof ACTIVITY_STATES)[number];
/** Emitted on every transition, throttled to >=4/s while writing. `detail` is a short human label. */
export interface AgentActivity { run_id: string; state: ActivityState; detail: string; since: number; tokens_out?: number }
