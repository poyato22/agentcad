// Typed HTTP client for the local core. Every request carries the bearer token; every response
// is validated with zod. Failures always surface as CoreError (never a raw fetch/zod error).
import { z } from "zod";
import { debug } from "../lib/log";
import {
  agentInfoSchema, agentRunSchema, approvalSchema, cancelAllSchema, claudeCodeStatusSchema, coreErrorBodySchema, coreStatusSchema,
  keyStatusSchema, modelsInfoSchema, okSchema, onshapeTestSchema, partSummarySchema, profileSchema, runIdSchema, setupStateSchema,
  stepSchema,
} from "./schemas";
import type {
  AgentInfo, AgentRun, Approval, CancelAllResult, ClaudeCodeStatus, CoreInfo, CoreStatusInfo, DecisionBody, ErrorCode, KeyStatus,
  ModelsInfo, NewRunBody, OnshapeTestResult, PartSummary, Profile, ProfilePatch, SetupState, Step, UiContext,
} from "./types";

type Method = "GET" | "POST" | "PUT" | "DELETE";

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

  async partGlb(partId: string, etag?: string): Promise<{ bytes: ArrayBuffer; etag: string } | "not-modified"> {
    const headers: Record<string, string> = {};
    if (etag) headers["If-None-Match"] = etag;
    const res = await this.request("GET", `/parts/${enc(partId)}/model.glb`, { headers });
    if (res.status === 304) return "not-modified";
    if (!res.ok) throw await toError(res);
    let bytes: ArrayBuffer;
    try {
      bytes = await res.arrayBuffer();
    } catch (e) {
      throw new CoreError("network", `reading model.glb failed: ${errText(e)}`, { retryable: true });
    }
    return { bytes, etag: res.headers.get("etag") ?? "" };
  }

  // ---- Plan 3: profile, keys, setup, Claude Code, Onshape test, UI context (admin token routes)

  getProfile(): Promise<Profile> { return this.json("GET", "/profile", profileSchema); }
  /** Partial merge; only the fields given are sent. */
  updateProfile(patch: ProfilePatch): Promise<Profile> { return this.json("PUT", "/profile", profileSchema, { body: patch }); }

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

  setupState(): Promise<SetupState> { return this.json("GET", "/setup", setupStateSchema); }
  /** Marks setup done (sets profile.setup_completed_at). Re-read setupState() for the result. */
  async completeSetup(): Promise<void> { await this.json("POST", "/setup/complete", anyBody); }

  claudeCodeStatus(): Promise<ClaudeCodeStatus> { return this.json("GET", "/claude-code/status", claudeCodeStatusSchema); }
  /** Starts the user's own `claude auth login` (Anthropic's browser flow). Returns at once; poll claudeCodeStatus(). */
  async claudeCodeLogin(): Promise<void> { await this.json("POST", "/claude-code/login", anyBody); }
  async claudeCodeLogout(): Promise<void> { await this.json("POST", "/claude-code/logout", anyBody); }

  async putContext(ctx: UiContext): Promise<void> { await this.json("PUT", "/context", anyBody, { body: ctx }); }

  // ---- internals

  private async json<T>(method: Method, path: string, schema: z.ZodType<T>, opts: { query?: Query; body?: unknown } = {}): Promise<T> {
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

  private async request(method: Method, path: string, opts: { query?: Query; body?: unknown; headers?: Record<string, string> }): Promise<Response> {
    let url = this.base + path;
    if (opts.query) {
      const q = new URLSearchParams();
      for (const [k, v] of Object.entries(opts.query)) if (v !== undefined && v !== null) q.set(k, String(v));
      const s = q.toString();
      if (s) url += `?${s}`;
    }
    const headers: Record<string, string> = { Authorization: `Bearer ${this.token}`, ...opts.headers };
    const init: RequestInit = { method, headers };
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
  if (res.status === 422) {
    return new CoreError("validation", `request rejected: ${summarizeDetail(body)}`, { status: 422 });
  }
  const parsed = coreErrorBodySchema.safeParse(body);
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
