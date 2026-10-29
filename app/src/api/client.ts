// Typed HTTP client for the local core. Every request carries the bearer token; every response
// is validated with zod. Failures always surface as CoreError (never a raw fetch/zod error).
import { z } from "zod";
import { debug } from "../lib/log";
import {
  agentInfoSchema, agentRunSchema, approvalSchema, cancelAllSchema, claudeCodeStatusSchema, coreErrorBodySchema, coreStatusSchema,
  keyStatusSchema, localActionSchema, localStatusSchema, localTestSchema, modelsInfoSchema, okSchema, onshapeTestSchema, partSummarySchema, profileSchema, runIdSchema, setupStateSchema, sketchSummarySchema,
  stepSchema, systemCheckRowSchema, systemInstallStartSchema,
} from "./schemas";
import {
  simRecordSchema, simSolversSchema, simStartedSchema, simSummarySchema,
  type SimRecord, type SimSolvers, type SimStartBody, type SimStarted, type SimSummary,
} from "./sims";
import {
  profileActiveSchema, projectsLiteSchema, specCheckSchema, specDocSchema, studyAppliedSchema, studySchema,
  type Spec, type SpecCheck, type SpecDoc, type Study, type StudyApplied, type StudyApplyBody,
} from "./spec";
import type {
  AgentInfo, AgentRun, Approval, CancelAllResult, ClaudeCodeStatus, CoreInfo, CoreStatusInfo, DecisionBody, ErrorCode, KeyStatus, LocalActionResult, LocalStatus, LocalTestResult,
  ModelsInfo, NewRunBody, OnshapeTestResult, PartSummary, Profile, ProfilePatch, SetupState, SketchSummary, SketchUpload, Step,
  SystemCheckRow, SystemInstallStart, UiContext,
} from "./types";
import {
  drawingInfoSchema, printCallSchema, printCapabilitiesSchema, printerSchema, printerStatusSchema, printStartSchema, sliceSchema, sliceStartSchema,
} from "./schemas";
import type { DrawingInfo, NewPrinterBody, PrintCall, PrintCapabilities, Printer, PrinterStatus, PrintStart, Slice, SliceStart } from "./types";

type Method = "GET" | "POST" | "PUT" | "DELETE";

/** The fields PUT /profile may carry: core profile.Profile minus units, setup_completed_at and
 *  schema_version, which the app never writes. */
const PROFILE_PATCH_KEYS: ReadonlySet<string> = new Set<string>([
  "display_name", "default_material", "default_agent", "default_model", "onshape_calls_year_cap", "onshape_calls_run_cap",
  "max_parallel_agents", "local_endpoints", "theme",
] satisfies (keyof ProfilePatch)[]);

/** For routes whose answer the UI doesn't use (it refetches status instead): any 2xx JSON body. */
const anyBody = z.unknown();

export type CoreErrorCode = ErrorCode | "unauthorized" | "validation" | "network";

export class CoreError extends Error {
  readonly status: number;
  readonly code: CoreErrorCode;
  readonly hint: string | null;
  readonly retryable: boolean;

  constructor(code: CoreErrorCode, message: string, opts: { status?: number; hint?: string | null; retryable?: boolean } = {}) {
    super(message);
    this.name = "CoreError";
    this.code = code;
    this.status = opts.status ?? 0;
    this.hint = opts.hint ?? null;
    this.retryable = opts.retryable ?? false;
  }
}

/** Status → code when the body isn't the core's error JSON (e.g. FastAPI's `{"detail":"Not Found"}`). */
const FALLBACK: Record<number, ErrorCode> = { 400: "invalid_input", 403: "denied", 404: "not_found", 409: "stale_version", 429: "budget_exhausted", 503: "transient" };

type Query = Record<string, string | number | null | undefined>;

export class CoreClient {
  private readonly base: string;
  private readonly token: string;
  private readonly fetchImpl: typeof fetch;

  constructor(info: CoreInfo, fetchImpl?: typeof fetch) {
    this.base = info.baseUrl.replace(/\/+$/, "");
    this.token = info.token;
    this.fetchImpl = fetchImpl ?? ((input, init) => globalThis.fetch(input, init));
  }

  health(): Promise<{ ok: true }> { return this.json("GET", "/health", okSchema); }

  listRuns(limit = 50): Promise<AgentRun[]> { return this.json("GET", "/runs", z.array(agentRunSchema), { query: { limit } }); }
  getRun(id: string): Promise<AgentRun> { return this.json("GET", `/runs/${enc(id)}`, agentRunSchema); }
  /** One malformed step (e.g. a kind this UI doesn't know yet) is dropped, not the whole page. */
  steps(runId: string, afterId = 0): Promise<Step[]> {
    return this.json("GET", `/runs/${enc(runId)}/steps`, lenientSteps, { query: { after_id: afterId } });
  }
  createRun(body: NewRunBody): Promise<{ run_id: string }> { return this.json("POST", "/runs", runIdSchema, { body }); }
  message(runId: string, text: string): Promise<{ ok: true }> {
    return this.json("POST", `/runs/${enc(runId)}/message`, okSchema, { body: { text } });
  }
  cancel(runId: string): Promise<{ ok: true }> { return this.json("POST", `/runs/${enc(runId)}/cancel`, okSchema); }
  cancelAll(): Promise<CancelAllResult> { return this.json("POST", "/runs/cancel_all", cancelAllSchema); }

  pendingApprovals(runId?: string): Promise<Approval[]> {
    return this.json("GET", "/approvals", z.array(approvalSchema), { query: { status: "pending", run_id: runId } });
  }
  decide(approvalId: string, body: DecisionBody): Promise<Approval> {
    // Exactly {payload_hash, approve}: edited_payload is never sent in v1.
    const exact: DecisionBody = { payload_hash: body.payload_hash, approve: body.approve };
    return this.json("POST", `/approvals/${enc(approvalId)}/decide`, approvalSchema, { body: exact });
  }
  answer(questionId: string, text: string): Promise<{ ok: true }> {
    return this.json("POST", `/questions/${enc(questionId)}/answer`, okSchema, { body: { text } });
  }

  status(): Promise<CoreStatusInfo> { return this.json("GET", "/status", coreStatusSchema); }
  agents(): Promise<AgentInfo[]> { return this.json("GET", "/agents", z.array(agentInfoSchema)); }
  models(): Promise<ModelsInfo> { return this.json("GET", "/models", modelsInfoSchema); }
  parts(): Promise<PartSummary[]> { return this.json("GET", "/parts", z.array(partSummarySchema)); }

  partGlb(partId: string, etag?: string): Promise<{ bytes: ArrayBuffer; etag: string } | "not-modified"> {
    return this.glb(`/parts/${enc(partId)}/model.glb`, etag);
  }

  // ---- static stress studies (core server/sim_api.py; Studies page)

  simSolvers(): Promise<SimSolvers> { return this.json("GET", "/sims/solvers", simSolversSchema); }
  /** Newest first; filter by part and/or by the run that ran them. */
  listSims(filter: { partId?: string; runId?: string } = {}): Promise<SimSummary[]> {
    return this.json("GET", "/sims", z.array(simSummarySchema), { query: { part_id: filter.partId, run_id: filter.runId } });
  }
  getSim(simId: string): Promise<SimRecord> { return this.json("GET", `/sims/${enc(simId)}`, simRecordSchema); }
  /** 202: a "studies" run drives sim_static_run through the Gate; cancel it with cancel(run_id). */
  startSim(body: SimStartBody): Promise<SimStarted> { return this.json("POST", "/sims", simStartedSchema, { body }); }
  /** The von Mises heatmap (per-vertex COLOR_0, same frame as the part's model.glb). */
  simGlb(simId: string, etag?: string): Promise<{ bytes: ArrayBuffer; etag: string } | "not-modified"> {
    return this.glb(`/sims/${enc(simId)}/result.glb`, etag);
  }

  private async glb(path: string, etag?: string): Promise<{ bytes: ArrayBuffer; etag: string } | "not-modified"> {
    const headers: Record<string, string> = {};
    if (etag) headers["If-None-Match"] = etag;
    const res = await this.request("GET", path, { headers });
    if (res.status === 304) return "not-modified";
    if (!res.ok) throw await toError(res);
    let bytes: ArrayBuffer;
    try {
      bytes = await res.arrayBuffer();
    } catch (e) {
      const name = path.slice(path.lastIndexOf("/") + 1);
      throw new CoreError("network", `reading ${name} failed: ${errText(e)}`, { retryable: true });
    }
    return { bytes, etag: res.headers.get("etag") ?? "" };
  }

  // ---- Plan 4: drawings (server/drawing_api.py) and printing (server/print_api.py)

  drawingInfo(partId: string, material?: string | null): Promise<DrawingInfo> {
    return this.json("GET", `/parts/${enc(partId)}/drawing.json`, drawingInfoSchema, { query: { material } });
  }
  /** The drawing as a typed Blob (SVG: shown via a blob: URL in an <img>, never as markup). */
  drawingFile(partId: string, kind: "svg" | "dxf", material?: string | null): Promise<Blob> {
    return this.blob(`/parts/${enc(partId)}/drawing.${kind}`, kind === "svg" ? "image/svg+xml" : "image/vnd.dxf", { material });
  }

  printCapabilities(): Promise<PrintCapabilities> { return this.json("GET", "/print/capabilities", printCapabilitiesSchema); }
  printers(): Promise<Printer[]> { return this.json("GET", "/printers", z.array(printerSchema)); }
  /** The API key goes out in the body only (to the Keychain via the core) and never comes back. */
  addPrinter(body: NewPrinterBody): Promise<Printer> { return this.json("POST", "/printers", printerSchema, { body }); }
  async deletePrinter(id: string): Promise<void> { await this.json("DELETE", `/printers/${enc(id)}`, okSchema); }
  printerStatus(id: string, signal?: AbortSignal): Promise<PrinterStatus> {
    return this.json("GET", `/printers/${enc(id)}/status`, printerStatusSchema, { signal });
  }

  /** 424 (tool_failed, hint = install command) when PrusaSlicer is missing. */
  startSlice(body: { part_id: string; printer_id: string; material?: string | null }): Promise<SliceStart> {
    return this.json("POST", "/slices", sliceStartSchema, { body });
  }
  getSlice(id: string): Promise<Slice> { return this.json("GET", `/slices/${enc(id)}`, sliceSchema); }
  async cancelSlice(id: string): Promise<void> { await this.json("POST", `/slices/${enc(id)}/cancel`, okSchema); }
  slicePreview(id: string): Promise<Blob> { return this.blob(`/slices/${enc(id)}/preview.png`, "image/png"); }

  /** Starts the irreversible print_send call; nothing is sent until its approval card is approved. */
  startPrint(sliceId: string, printerId: string): Promise<PrintStart> {
    return this.json("POST", "/prints", printStartSchema, { body: { slice_id: sliceId, printer_id: printerId } });
  }
  getPrint(callId: string): Promise<PrintCall> { return this.json("GET", `/prints/${enc(callId)}`, printCallSchema); }

  // ---- Plan 3: profile, keys, setup, Claude Code, Onshape test, UI context (admin token routes)

  getProfile(): Promise<Profile> { return this.json("GET", "/profile", profileSchema); }
  /** Partial merge; only the fields given are sent, and only fields the core's Profile has (it is
   *  extra="forbid": an unknown key would fail the whole PUT). */
  updateProfile(patch: ProfilePatch): Promise<Profile> {
    const body = Object.fromEntries(Object.entries(patch).filter(([k, v]) => PROFILE_PATCH_KEYS.has(k) && v !== undefined));
    return this.json("PUT", "/profile", profileSchema, { body });
  }

  /** Status only ({vendor, present, source}); secrets are never returned. */
  listKeys(): Promise<KeyStatus[]> { return this.json("GET", "/keys", z.array(keyStatusSchema)); }
  /** Stores an API key in the Keychain (via the core). The secret goes out in the body only, never in a URL or log. */
  setKey(vendor: string, secret: string): Promise<KeyStatus> {
    return this.json("POST", `/keys/${enc(vendor)}`, keyStatusSchema, { body: { secret } });
  }
  setOnshapeKeys(accessKey: string, secretKey: string): Promise<KeyStatus> {
    return this.json("POST", "/keys/onshape", keyStatusSchema, { body: { access_key: accessKey, secret_key: secretKey } });
  }
  deleteKey(vendor: string): Promise<KeyStatus> { return this.json("DELETE", `/keys/${enc(vendor)}`, keyStatusSchema); }
  /** One ledgered Onshape call; wrong keys → {ok:false, error:"denied"}. */
  testOnshape(): Promise<OnshapeTestResult> { return this.json("POST", "/onshape/test", onshapeTestSchema); }

  /** Does a local model server answer at this URL (loopback only)? "No answer" is {ok:false, error}, not a throw. */
  testLocal(url: string): Promise<LocalTestResult> { return this.json("POST", "/local/test", localTestSchema, { body: { url } }); }

  // ---- Plan 4: Ollama on this Mac (admin routes)

  /** Is Ollama installed / running, what's downloaded, which models fit this Mac. */
  localStatus(): Promise<LocalStatus> { return this.json("GET", "/local/status", localStatusSchema); }
  /** Starts `ollama serve` when installed but not running. A failure is {ok:false, error, hint}. */
  localStart(): Promise<LocalActionResult> { return this.json("POST", "/local/start", localActionSchema); }
  /** Starts a download; progress arrives as `local_pull` events. {ok:false, already:true}: that
   *  model is already downloading; {ok:false, error:"not_running"|"too_many_pulls", hint}. */
  localPull(model: string): Promise<LocalActionResult> { return this.json("POST", "/local/pull", localActionSchema, { body: { model } }); }
  /** Stops a download; the core then sends a `local_pull` event with status "cancelled". */
  async localPullCancel(model: string): Promise<boolean> {
    return (await this.json("POST", "/local/pull/cancel", localActionSchema, { body: { model } })).ok;
  }

  setupState(): Promise<SetupState> { return this.json("GET", "/setup", setupStateSchema); }
  /** Marks setup done (sets profile.setup_completed_at). Re-read setupState() for the result. */
  async completeSetup(): Promise<void> { await this.json("POST", "/setup/complete", anyBody); }

  /** `signal` aborts the request (sign-in polling gives each status check a deadline). */
  claudeCodeStatus(signal?: AbortSignal): Promise<ClaudeCodeStatus> {
    return this.json("GET", "/claude-code/status", claudeCodeStatusSchema, { signal });
  }
  /** Starts the user's own `claude auth login` (Anthropic's browser flow). Returns at once; poll claudeCodeStatus(). */
  async claudeCodeLogin(): Promise<void> { await this.json("POST", "/claude-code/login", anyBody); }
  async claudeCodeLogout(): Promise<void> { await this.json("POST", "/claude-code/logout", anyBody); }

  async putContext(ctx: UiContext): Promise<void> { await this.json("PUT", "/context", anyBody, { body: ctx }); }

  // ---- System check & installers (A1 shell; C6 owns the real core routes)

  /** GET /system/check: one row per dependency with status, version, path and an optional fix. */
  systemCheck(): Promise<SystemCheckRow[]> { return this.json("GET", "/system/check", z.array(systemCheckRowSchema)); }
  /** POST /system/install/{dep}: starts a fixed, allowlisted install (no arguments). Output streams
   *  as `system_install` events; the body says whether it started or hands back a fix (e.g. no brew). */
  systemInstall(dep: string): Promise<SystemInstallStart> {
    return this.json("POST", `/system/install/${enc(dep)}`, systemInstallStartSchema);
  }

  // ---- Sketch page

  /** Stores a sketch (strokes in mm, dimensions, notes, optional photo + render); start a run with its id. */
  postSketch(body: SketchUpload): Promise<SketchSummary> { return this.json("POST", "/sketches", sketchSummarySchema, { body }); }
  getSketch(id: string): Promise<SketchSummary> { return this.json("GET", `/sketches/${enc(id)}`, sketchSummarySchema); }

  // ---- Plan 5 engineering loop: spec, design studies (core server/spec_api.py)

  /** The build a project-less request belongs to, like core projects.active_project_id: the
   *  profile's active build when it is live, else the oldest live build. */
  async activeProjectId(): Promise<string | null> {
    const [list, prof] = await Promise.all([
      this.json("GET", "/projects", projectsLiteSchema), this.json("GET", "/profile", profileActiveSchema),
    ]);
    const live = list.filter((p) => p.trashed_at === null);
    return live.find((p) => p.id === prof.active_project_id)?.id ?? live[0]?.id ?? null;
  }
  getSpec(projectId: string): Promise<SpecDoc> { return this.json("GET", `/projects/${enc(projectId)}/spec`, specDocSchema); }
  /** The user's own edit; validated like the agent's spec_set (422 names the bad fields). With
   *  `baseUpdatedAt` (the `updated_at` the edit started from; null = no spec yet) a spec written
   *  since is refused: 409 stale_version. `undefined` saves regardless ("Save mine anyway"). */
  putSpec(projectId: string, spec: Spec, baseUpdatedAt?: number | null): Promise<SpecDoc> {
    const body = baseUpdatedAt === undefined ? { spec } : { spec, base_updated_at: baseUpdatedAt };
    return this.json("PUT", `/projects/${enc(projectId)}/spec`, specDocSchema, { body });
  }
  /** spec_check through the Gate: the part measured against the spec, per requirement. */
  specCheck(projectId: string, partId: string): Promise<SpecCheck> {
    return this.json("GET", `/projects/${enc(projectId)}/spec/check`, specCheckSchema, { query: { part_id: partId } });
  }
  /** Design studies of a build (iterations persist live while one runs). */
  listStudies(projectId: string): Promise<Study[]> { return this.json("GET", `/projects/${enc(projectId)}/studies`, z.array(studySchema)); }
  /** "Use this variant": study_apply through the Gate. 409 stale_version when the part moved on. */
  applyStudy(projectId: string, studyId: string, body: StudyApplyBody): Promise<StudyApplied> {
    return this.json("POST", `/projects/${enc(projectId)}/studies/${enc(studyId)}/apply`, studyAppliedSchema, { body });
  }

  // ---- internals

  /** A binary answer as a Blob of `type` (the type is ours, not the server's). */
  private async blob(path: string, type: string, query?: Query): Promise<Blob> {
    const res = await this.request("GET", path, { query });
    if (!res.ok) throw await toError(res);
    try {
      return new Blob([await res.arrayBuffer()], { type });
    } catch (e) {
      throw new CoreError("network", `reading ${path} failed: ${errText(e)}`, { retryable: true });
    }
  }

  private async json<T>(method: Method, path: string, schema: z.ZodType<T>, opts: { query?: Query; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
    const res = await this.request(method, path, opts);
    if (!res.ok) throw await toError(res);
    let data: unknown;
    try {
      data = await res.json();
    } catch {
      throw new CoreError("validation", `${method} ${path}: response is not JSON`, { status: res.status });
    }
    const parsed = schema.safeParse(data);
    if (!parsed.success) {
      const issues = parsed.error.issues.slice(0, 5)
        .map((i) => `${i.path.length ? i.path.join(".") : "(root)"}: ${i.message}`).join("; ");
      throw new CoreError("validation", `${method} ${path}: unexpected response shape (${issues})`, { status: res.status });
    }
    return parsed.data;
  }

  private async request(method: Method, path: string, opts: { query?: Query; body?: unknown; headers?: Record<string, string>; signal?: AbortSignal }): Promise<Response> {
    let url = this.base + path;
    if (opts.query) {
      const q = new URLSearchParams();
      for (const [k, v] of Object.entries(opts.query)) if (v !== undefined && v !== null) q.set(k, String(v));
      const s = q.toString();
      if (s) url += `?${s}`;
    }
    const headers: Record<string, string> = { Authorization: `Bearer ${this.token}`, ...opts.headers };
    const init: RequestInit = { method, headers };
    if (opts.signal) init.signal = opts.signal;
    if (opts.body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    try {
      return await this.fetchImpl(url, init);
    } catch (e) {
      throw new CoreError("network", `${method} ${path}: ${errText(e)}`, { retryable: true });
    }
  }
}

async function toError(res: Response): Promise<CoreError> {
  let body: unknown = null;
  try { body = await res.json(); } catch { /* not JSON */ }
  const message = typeof (body as { message?: unknown })?.message === "string"
    ? (body as { message: string }).message : `HTTP ${res.status}`;
  if (res.status === 401) return new CoreError("unauthorized", message, { status: 401 });
  const parsed = coreErrorBodySchema.safeParse(body);
  // a 422 is pydantic's {"detail": [...]}, except where the core answers with its own error JSON
  // (PUT /profile: "invalid profile: max_parallel_agents: ...")
  if (res.status === 422 && !parsed.success) {
    return new CoreError("validation", `request rejected: ${summarizeDetail(body)}`, { status: 422 });
  }
  if (parsed.success) {
    return new CoreError(parsed.data.error, parsed.data.message, { status: res.status, hint: parsed.data.hint, retryable: parsed.data.retryable });
  }
  const code = FALLBACK[res.status] ?? "tool_failed";
  const detail = typeof (body as { detail?: unknown })?.detail === "string" ? (body as { detail: string }).detail : message;
  return new CoreError(code, detail, { status: res.status, retryable: code === "transient" });
}

function summarizeDetail(body: unknown): string {
  const detail = (body as { detail?: unknown })?.detail;
  if (!Array.isArray(detail)) return "invalid request";
  return detail.slice(0, 3).map((d: { loc?: unknown[]; msg?: string }) =>
    `${Array.isArray(d.loc) ? d.loc.join(".") : "?"}: ${d.msg ?? "invalid"}`).join("; ");
}

const lenientSteps: z.ZodType<Step[]> = z.array(z.unknown()).transform((items) => {
  const out: Step[] = [];
  for (const item of items) {
    const r = stepSchema.safeParse(item);
    if (r.success) out.push(r.data);
    else debug("dropping malformed step", (item as { id?: unknown } | null)?.id, r.error.issues[0]?.message);
  }
  return out;
});

const enc = encodeURIComponent;
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));
