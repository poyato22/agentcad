// Fake Mises core for unit and e2e tests. Mirrors core/src/mises/server/app.py + ui_api.py:
// same routes, bearer auth and 401 body, error JSON and status codes, 422 shape, CORS for the
// app origins, and `WS /events?token=&run_id=` with close 4401 on a bad token.
// Test-only control routes live under /__fake/* (see control.ts).
import { randomBytes, randomUUID } from "node:crypto";
import { existsSync, readFileSync, statSync } from "node:fs";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { WebSocketServer, type WebSocket } from "ws";
import type {
  AgentInfo, AgentRun, Approval, CoreStatusInfo, ErrorCode, LocalStatus, MisesEvent, ModelsInfo, PartSummary, Question, Step, StepKind,
  SystemCheckRow, SystemFix,
} from "../../src/api/types";
import { INSTALL_DEPS } from "../../src/api/types";
import { assemblyFrom, assemblyRoutes, DEFAULT_PROJECT, DEFAULT_PROJECT_ROW, type AssemblyScenario, type FakeAssembly } from "./assembly";
import { agentEdit, buildRoutes, buildsFrom, type BuildsDeps, type BuildsScenario, type FakeBuilds } from "./builds";
import { handleControl } from "./control";
import { directoryFrom, directoryRoutes, type DirectoryScenario, type FakeDirectory } from "./directory";
import { printingFrom, printingRoutes, stopPrinting, type FakePrinting, type PrintScenario } from "./printing";
import { setupFrom, setupRoutes, type FakeSetup, type SetupScenario } from "./setup";
import { cancelSimsOfRun, clearSimTimers, simRoutes, simsFrom, type FakeSims, type SimScenario } from "./sims";
import { checkRunSketch, sketchRoutes, sketchUserPayload, type FakeSketch } from "./sketch";
import { specFrom, specRoutes, type FakeSpec, type SpecScenario } from "./spec";

const HERE = dirname(fileURLToPath(import.meta.url));
export const SCENARIO_DIR = resolve(HERE, "scenarios");
export const DEFAULT_GLB = resolve(HERE, "../../../core/tests/fixtures/ui/mount_plate.glb");

/** Same list as core ui_api.UI_ORIGINS. */
export const UI_ORIGINS = ["tauri://localhost", "http://tauri.localhost", "http://localhost:1420", "http://127.0.0.1:1420"];

/** Same list as core ui_api.add_cors (allow_headers); ETag is exposed. */
export const CORS_ALLOW_HEADERS = ["Authorization", "Content-Type", "If-None-Match"];

/** Same rule as core ui_api._etag_matches: "*", or any listed tag (W/ prefix ignored). */
export function etagMatches(header: string | undefined, etag: string): boolean {
  if (!header) return false;
  if (header.trim() === "*") return true;
  return header.split(",").some((t) => t.trim().replace(/^W\//, "") === etag);
}

const PART_ID_RE = /^[a-z][a-z0-9_]{1,40}$/;
const CLAUDE_CODE = "claude-code";
/** Core claude_code.session.model_alias: what may follow "claude-code/". */
const CLAUDE_ALIAS_RE = /^[A-Za-z0-9][A-Za-z0-9._[\]-]{0,79}$/;
/** GET /status data_dir when a scenario leaves it out (the real core always sends it). */
const FAKE_DATA_DIR = "/Users/fake/Library/Application Support/Mises";
const DECIDED_BY = "local-user";

const HTTP_STATUS: Record<ErrorCode, number> = {
  not_found: 404, invalid_input: 400, stale_version: 409, needs_approval: 409, cancelled: 409, denied: 403,
  out_of_scope: 403, budget_exhausted: 429, transient: 503, tool_failed: 500,
};

// ---- scenario + state

export type EventIn = { type: string; run_id?: string | null; data?: Record<string, unknown>; ts?: number };
export type StepIn = Partial<Step> & { kind: StepKind };

export interface EmitExtras {
  /** Steps appended (ids, hashes and ts are filled in when missing). */
  steps?: StepIn[];
  /** Approval added or replaced. */
  approval?: Approval;
  /** Run added or replaced. */
  run?: AgentRun;
  /** Replaces the part list. */
  parts?: PartSummary[];
  /** Replaces GLB files per part_id (paths relative to the scenarios dir), e.g. after a rebuild. */
  glb?: Record<string, string>;
}

export interface ScriptEntry extends EmitExtras { at_ms: number; event: EventIn }

export interface Scenario {
  runs: AgentRun[]; steps: Step[]; approvals: Approval[]; parts: PartSummary[];
  agents: AgentInfo[]; models: ModelsInfo; status: CoreStatusInfo;
  /** Open questions at start (normally they only come from question_asked events). */
  questions?: Question[];
  /** Timed emits, started when the first WebSocket client connects after a (re)set. */
  script?: ScriptEntry[];
  /** Scripts played when something happens; "$run" anywhere in their text becomes that run's id.
   *  create_run: after POST /runs (the new run). decide: after an approval is approved
   *  (its run); decide_reject: after a rejection. */
  on?: { create_run?: ScriptEntry[]; decide?: ScriptEntry[]; decide_reject?: ScriptEntry[] };
  /** Optional GLB per part_id (path relative to the scenarios dir); default DEFAULT_GLB. */
  glb?: Record<string, string>;
  /** Profile / keys / Claude Code / Onshape-test state (Plan 3). Absent: setup already complete. */
  setup?: SetupScenario;
  /** Ollama on this Mac (Plan 4 GET /local/status). Absent: the /local routes answer 404 (older core). */
  local?: LocalStatus;
  /** ms between `local_pull` progress events (default 120). */
  local_pull_ms?: number;
  /** Static stress studies: solver status, stored studies, how POST /sims ends (Studies page). */
  sims?: SimScenario;
  /** Printers, slicer and drawing state (Plan 4). Absent: no printers, PrusaSlicer installed. */
  print?: PrintScenario;
  /** GET /system/check rows (Plan 5). Absent: a small healthy default set. */
  system?: SystemCheckRow[];
  /** POST /system/install/{dep} behaviour per dep. A `fix` refuses the install (e.g. brew missing);
   *  otherwise `lines` stream as `system_install` events and the install ends with `ok`. */
  system_install?: Record<string, { lines?: string[]; ok?: boolean; fix?: SystemFix }>;
  /** ms between `system_install` lines (default 60). */
  system_install_ms?: number;
  /** Onshape browser / budget / parts directory (Plan 5 A6). Absent: defaults from the fixtures. */
  directory?: DirectoryScenario;
  /** Builds and their assemblies (Plan 5 A4). Absent: one empty "Default" build. */
  assembly?: AssemblyScenario;
  /** Plan 5 engineering loop: the build's spec and design studies (spec panel, A7). */
  spec?: SpecScenario;
  /** Plan 5 builds, part tree and live edits (A3). Absent: one "Default" build with every part. */
  builds?: BuildsScenario;
  /** GET /tools (the QA-fix core's registry list). Absent: every real core tool; null: no route
   *  (404, an older core). */
  tools?: FakeTool[] | null;
}

export interface FakeTool { name: string; description: string; safety_class: string }

/** The real core's registered tools (core/src/mises, `@tool(...)`), with their safety class. */
const CORE_TOOLS: readonly [string, string][] = [
  ["agent_result", "read"], ["agent_spawn", "local_mutate"], ["agent_wait", "read"], ["assembly_add_instance", "local_mutate"],
  ["assembly_add_joint", "local_mutate"], ["assembly_check", "read"], ["assembly_drive", "local_mutate"], ["assembly_get", "read"],
  ["assembly_remove", "local_mutate"], ["catalog_get", "read"], ["catalog_import_url", "network"], ["catalog_insert", "local_mutate"],
  ["catalog_search", "read"], ["design_review", "read"], ["interview_plan", "read"], ["loads_impact", "read"], ["mirror_drawing", "read"],
  ["mirror_edges", "read"], ["mirror_export", "read"], ["mirror_faces", "read"], ["mirror_measure", "read"], ["mirror_part_create", "local_mutate"],
  ["mirror_part_get_source", "read"], ["mirror_part_list", "read"], ["mirror_part_op", "local_mutate"], ["mirror_part_set_source", "local_mutate"],
  ["mirror_part_set_vars", "local_mutate"], ["onshape_budget_get", "read"], ["onshape_doc_elements", "remote_read"], ["onshape_doc_open", "remote_read"],
  ["onshape_docs_search", "remote_read"], ["onshape_features_list", "remote_read"], ["onshape_import_element", "remote_read"],
  ["onshape_merge_request", "remote_mutate"], ["onshape_propose", "remote_mutate"], ["onshape_variables_get", "remote_read"],
  ["orchestrator_run", "local_mutate"], ["orchestrator_submit_plan", "local_mutate"], ["print_printers_list", "read"], ["print_send", "physical"],
  ["print_slice", "local_mutate"], ["sim_cancel", "local_mutate"], ["sim_compare", "read"], ["sim_env_list", "read"], ["sim_result_get", "read"],
  ["sim_run", "local_mutate"], ["sim_static_run", "local_mutate"], ["sim_types_list", "read"], ["spec_check", "read"], ["spec_get", "read"],
  ["spec_set", "local_mutate"], ["study_apply", "local_mutate"], ["study_get", "read"], ["study_run", "local_mutate"], ["ui_ask_user", "read"], ["ui_note", "read"],
];
export const DEFAULT_TOOLS: readonly FakeTool[] = CORE_TOOLS.map(([name, safety_class]) => ({ name, safety_class, description: `${name.replace(/_/g, " ")}.` }));

/** A small healthy default when a scenario names no `system` rows. */
export const DEFAULT_SYSTEM_CHECK: SystemCheckRow[] = [
  { id: "python", name: "Core Python & venv", status: "ok", version: "3.13.1", path: "/opt/mises/.venv", detail: "Ready.", fix: null },
  { id: "build123d", name: "build123d / OCCT", status: "ok", version: "0.9.1", path: null, detail: "Loaded.", fix: null },
  { id: "prusaslicer", name: "PrusaSlicer", status: "missing", version: null, path: null,
    detail: "Not found. Needed to slice and estimate prints.", fix: { kind: "install", label: "Install PrusaSlicer", dep: "prusaslicer" } },
];

export interface FakeCall { method: string; path: string; query: Record<string, string>; body: unknown; ts: number; ifNoneMatch?: string }

export interface FakeState {
  token: string;
  runs: Map<string, AgentRun>;
  steps: Step[];
  approvals: Map<string, Approval>;
  questions: Map<string, Question>;
  parts: PartSummary[];
  agents: AgentInfo[];
  models: ModelsInfo;
  status: CoreStatusInfo;
  glb: Record<string, string>;
  script: ScriptEntry[];
  on: NonNullable<Scenario["on"]>;
  calls: FakeCall[];
  setup: FakeSetup;
  local: LocalStatus | null;
  localPullMs: number;
  /** Downloads in flight: model -> their pending progress timers (POST /local/pull/cancel clears them). */
  pulling: Map<string, ReturnType<typeof setTimeout>[]>;
  sims: FakeSims;
  /** POST /sketches uploads, by sketch_id. */
  sketches: Map<string, FakeSketch>;
  print: FakePrinting;
  system: SystemCheckRow[];
  systemInstall: Record<string, { lines?: string[]; ok?: boolean; fix?: SystemFix }>;
  systemInstallMs: number;
  /** Plan 5 A6: Onshape browser, budget and parts directory. */
  directory: FakeDirectory;
  assembly: FakeAssembly;
  spec: FakeSpec;
  /** Plan 5 A3: builds, part-tree rows, scripts and versions. */
  builds: FakeBuilds;
  /** GET /tools; null answers 404. */
  tools: FakeTool[] | null;
}

export interface FakeCore {
  readonly url: string;
  readonly token: string;
  readonly state: FakeState;
  emit(ev: EventIn, extras?: EmitExtras): { step_ids: number[] };
  reset(scenario?: string | Scenario, token?: string): void;
  /** Closes every events socket (code 1012, or abnormal drop with `terminate`). */
  dropSockets(mode?: "close" | "terminate"): void;
  rotateToken(token: string): void;
  /** An agent's edit of a part (Plan 5 C6): version+1, vars merged, `part_changed {by:"agent"}`. */
  agentEdit(b: Record<string, unknown>): { ok: true; version: number };
  stop(): Promise<void>;
}

export interface FakeCoreOptions {
  port?: number;
  token?: string;
  scenario: string | Scenario;
  /** Extra allowed CORS origins (e2e runs Vite on a per-worktree port). */
  origins?: string[];
  glbPath?: string;
}

export function loadScenario(s: string | Scenario): Scenario {
  if (typeof s !== "string") return structuredClone(s);
  const file = s.endsWith(".json") ? resolve(s) : resolve(SCENARIO_DIR, `${s}.json`);
  return JSON.parse(readFileSync(file, "utf8")) as Scenario;
}

/** ONE list of builds behind the ONE fake GET /projects (A3's builds routes): A6's directory and
 *  A4's assembly share it. A scenario names builds through A3's `builds`, else A4's
 *  `assembly.projects` or A6's `directory.builds`; the default is the real core's Default build
 *  (projects.json), so A4's and A7's fixture ids line up. */
function sharedBuilds(sc: Scenario): { builds: FakeBuilds; directory: FakeDirectory; assembly: FakeAssembly } {
  const named = sc.builds?.builds?.length ? sc.builds.builds
    : sc.assembly?.projects?.length ? sc.assembly.projects.map((p) => ({ id: p.id, name: p.name, created_at: p.created_at, trashed_at: p.trashed_at ?? null }))
    : sc.directory?.builds?.length ? sc.directory.builds.map((b) => ({ id: b.id, name: b.name }))
    : [{ id: DEFAULT_PROJECT, name: "Default", created_at: DEFAULT_PROJECT_ROW.created_at }];
  const builds = buildsFrom({ ...sc.builds, builds: named }, sc.parts.map((p) => p.part_id));
  const assembly = assemblyFrom(sc.assembly);
  assembly.projects = builds.builds;
  const directory = directoryFrom(sc.directory, sc.status.onshape === "connected", sc.status);
  directory.builds = builds.builds;
  if (sc.directory?.active_build === undefined && sc.builds?.active) directory.activeBuild = sc.builds.active;
  return { builds, directory, assembly };
}

function stateFrom(sc: Scenario, token: string): FakeState {
  return {
    token,
    runs: new Map(sc.runs.map((r) => [r.id, { external: false, session_ref: null, ...r }])),
    steps: [...sc.steps].sort((a, b) => a.id - b.id),
    approvals: new Map(sc.approvals.map((a) => [a.id, a])),
    questions: new Map((sc.questions ?? []).map((q) => [q.question_id, q])),
    parts: sc.parts, agents: sc.agents, models: sc.models, status: sc.status,
    tools: sc.tools === null ? null : structuredClone(sc.tools ?? [...DEFAULT_TOOLS]),
    glb: sc.glb ?? {}, script: sc.script ?? [], on: sc.on ?? {}, calls: [],
    setup: withBuildTabs(setupFrom(sc), sc.builds),
    local: sc.local ? structuredClone(sc.local) : null, localPullMs: sc.local_pull_ms ?? 120, pulling: new Map(),
    sims: simsFrom(sc.sims),
    sketches: new Map(),
    print: printingFrom(sc.print),
    system: sc.system ? structuredClone(sc.system) : structuredClone(DEFAULT_SYSTEM_CHECK),
    systemInstall: sc.system_install ? structuredClone(sc.system_install) : {},
    systemInstallMs: sc.system_install_ms ?? 60,
    ...sharedBuilds(sc),
    spec: specFrom(sc.spec),
  };
}

/** The scenario's open build tabs, on the fake profile (C1 profile.active_project_id / open_project_ids). */
function withBuildTabs(setup: FakeSetup, b: BuildsScenario | undefined): FakeSetup {
  if (b && (b.active !== undefined || b.open !== undefined)) {
    Object.assign(setup.profile, { active_project_id: b.active ?? null, open_project_ids: b.open ?? [] });
  }
  return setup;
}

const now = () => Date.now() / 1000;
const hex = (bytes: number) => randomBytes(bytes).toString("hex");

// ---- HTTP helpers

export class HttpError extends Error {
  readonly status: number;
  readonly body: unknown;
  constructor(status: number, body: unknown) {
    super(String(status));
    this.status = status;
    this.body = body;
  }
}

export function mises(code: ErrorCode, message: string, hint: string | null = null, status?: number): HttpError {
  return new HttpError(status ?? HTTP_STATUS[code], { error: code, message, hint, retryable: code === "transient" });
}

export function missing(field: string, input: unknown): HttpError {
  return new HttpError(422, { detail: [{ type: "missing", loc: ["body", field], msg: "Field required", input }] });
}

export function wrongType(field: string, type: string, input: unknown): HttpError {
  return new HttpError(422, { detail: [{ type: `${type}_type`, loc: ["body", field], msg: `Input should be a valid ${type}`, input }] });
}

export type Body = Record<string, unknown>;

function req<T extends "string" | "boolean">(b: Body, field: string, type: T): T extends "string" ? string : boolean {
  if (!(field in b)) throw missing(field, b);
  if (typeof b[field] !== type) throw wrongType(field, type, b[field]);
  return b[field] as T extends "string" ? string : boolean;
}

function strList(b: Body, field: string, dflt: string[]): string[] {
  const v = b[field];
  if (v === undefined) return dflt;
  if (!Array.isArray(v) || v.some((x) => typeof x !== "string")) throw wrongType(field, "list", v);
  return v as string[];
}

async function readBody(r: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const c of r) chunks.push(c as Buffer);
  const text = Buffer.concat(chunks).toString("utf8");
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    throw new HttpError(422, { detail: [{ type: "json_invalid", loc: ["body", 0], msg: "JSON decode error", input: {} }] });
  }
}

export function asBody(v: unknown): Body {
  if (typeof v !== "object" || v === null || Array.isArray(v)) {
    throw new HttpError(422, { detail: [{ type: "model_attributes_type", loc: ["body"], msg: "Input should be a valid dictionary", input: v ?? null }] });
  }
  return v as Body;
}

// ---- server

export async function startFakeCore(opts: FakeCoreOptions): Promise<FakeCore> {
  const origins = new Set([...UI_ORIGINS, ...(opts.origins ?? [])]);
  const defaultGlb = opts.glbPath ?? DEFAULT_GLB;
  let state = stateFrom(loadScenario(opts.scenario), opts.token ?? "dev-token");
  let scriptTimers: ReturnType<typeof setTimeout>[] = [];
  let scriptStarted = false;
  const sockets = new Set<{ ws: WebSocket; runId: string | null }>();

  // -- domain helpers

  function appendStep(s: StepIn, fallbackRun: string | null): Step {
    const runId = s.run_id ?? fallbackRun;
    if (!runId) throw new Error("step needs a run_id");
    const prev = [...state.steps].reverse().find((x) => x.run_id === runId);
    const id = s.id ?? (state.steps.length ? state.steps[state.steps.length - 1].id + 1 : 1);
    const step: Step = {
      id, run_id: runId, ts: s.ts ?? now(), kind: s.kind, payload: s.payload ?? {},
      evidence_hash: s.evidence_hash ?? hex(32), prev_hash: s.prev_hash ?? prev?.evidence_hash ?? null,
      collapsed: s.collapsed ?? false,
    };
    state.steps = [...state.steps.filter((x) => x.id !== id), step].sort((a, b) => a.id - b.id);
    return step;
  }

  function broadcast(ev: MisesEvent) {
    const text = JSON.stringify(ev);
    for (const s of sockets) {
      if (s.runId !== null && ev.run_id !== s.runId) continue;
      if (s.ws.readyState === s.ws.OPEN) s.ws.send(text);
    }
  }

  /** The latest agent_activity per run (GET /runs/{id} `activity`, like the core's tracker). */
  const lastActivity = new Map<string, unknown>();

  /** Keeps fake state consistent with what an event implies in the real core. */
  function applyEvent(ev: MisesEvent) {
    const d = ev.data;
    if (ev.type === "agent_activity" && ev.run_id) lastActivity.set(ev.run_id, d);
    if (ev.type === "question_asked" && ev.run_id && typeof d.question_id === "string") {
      state.questions.set(d.question_id, {
        question_id: d.question_id, run_id: ev.run_id, question: String(d.question ?? ""),
        options: Array.isArray(d.options) ? d.options.map(String) : [],
        // the app's Question carries a batch as `items` (A2; the event field is `questions`)
        ...(Array.isArray(d.questions) && d.questions.length ? { items: d.questions as Question["items"] & object } : {}),
      });
    } else if (ev.type === "question_answered" && typeof d.question_id === "string") {
      state.questions.delete(d.question_id);
    } else if (ev.type === "approval_requested" && typeof d.id === "string") {
      state.approvals.set(d.id, d as unknown as Approval);
    } else if (ev.type === "run_finished" && ev.run_id) {
      const r = state.runs.get(ev.run_id);
      const st = d.status;
      if (r && (st === "done" || st === "failed" || st === "cancelled" || st === "running")) {
        state.runs.set(r.id, { ...r, status: st, updated_at: now() });
      }
    } else if (ev.type === "run_started" && ev.run_id && d.resumed === true) {
      const r = state.runs.get(ev.run_id);
      if (r) state.runs.set(r.id, { ...r, status: "running", updated_at: now() });
    }
  }

  function emit(evIn: EventIn, extras: EmitExtras = {}): { step_ids: number[] } {
    const ev: MisesEvent = { type: evIn.type, run_id: evIn.run_id ?? null, data: evIn.data ?? {}, ts: evIn.ts ?? now() };
    if (extras.run) state.runs.set(extras.run.id, { external: false, session_ref: null, ...extras.run });
    const ids = (extras.steps ?? []).map((s) => appendStep(s, ev.run_id).id);
    if (extras.approval) state.approvals.set(extras.approval.id, extras.approval);
    if (extras.parts) state.parts = extras.parts;
    if (extras.glb) state.glb = { ...state.glb, ...extras.glb };
    applyEvent(ev);
    broadcast(ev);
    // like the core's activity tracker (final fix wave): a pending approval or question puts the
    // run in waiting_approval / waiting_user until it is decided or answered
    if (ev.run_id && (ev.type === "approval_requested" || ev.type === "question_asked")) {
      const st = ev.type === "approval_requested" ? "waiting_approval" : "waiting_user";
      emit({ type: "agent_activity", run_id: ev.run_id, data: { run_id: ev.run_id, state: st, detail: "", since: now() } });
    }
    return { step_ids: ids };
  }

  /** The run worked on after you decided or answered (it was waiting on you): back to thinking. */
  function resumeActivity(runId: string | null) {
    if (!runId) return;
    const last = lastActivity.get(runId) as { state?: string } | undefined;
    if (last?.state !== "waiting_approval" && last?.state !== "waiting_user") return;
    if (state.runs.get(runId)?.status !== "running") return;
    emit({ type: "agent_activity", run_id: runId, data: { run_id: runId, state: "thinking", detail: "", since: now() } });
  }

  function cancelRun(id: string) {
    const r = state.runs.get(id);
    if (!r || r.status !== "running") return;
    state.runs.set(id, { ...r, status: "cancelled", updated_at: now() });
    // same as core approvals.py on cancel
    for (const a of state.approvals.values()) {
      if (a.run_id === id && a.status === "pending") {
        state.approvals.set(a.id, { ...a, status: "rejected", decided_by: "system:cancel", decided_at: now() });
      }
    }
    for (const q of [...state.questions.values()]) if (q.run_id === id) state.questions.delete(q.question_id);
    cancelSimsOfRun(state.sims, id); // like the core: the mesher/solver is killed, the study is cancelled
    emit({ type: "run_finished", run_id: id, data: { status: "cancelled", stop_reason: "cancelled", error: null, final_text: null } });
  }

  function getRun(id: string): AgentRun {
    const r = state.runs.get(id);
    if (!r) throw mises("not_found", `unknown run: ${id}`);
    return r;
  }

  /** Custom agents' instructions (the core keeps them in ~/…/Mises/agents/<name>.md). */
  const agentBodies = new Map<string, string>();

  // ---- Plan 5 (C1) run-management helpers (mirror core projects.run_view / delete_reason)
  /** A run enriched with the API's run_view fields (parent_run_id, last_activity_at, cost). */
  function runView(r: AgentRun): AgentRun {
    return {
      ...r,
      project_id: r.project_id ?? state.assembly.projects[0]?.id ?? null,
      title: r.title ?? null, pinned: r.pinned ?? false, archived: r.archived ?? false,
      parent_run_id: r.parent_id,
      last_activity_at: r.last_activity_at ?? r.updated_at,
      cost: { usd: r.usd, tokens: r.input_tokens + r.output_tokens },
    };
  }

  function childrenOf(id: string): AgentRun[] {
    return [...state.runs.values()].filter((r) => r.parent_id === id);
  }
  function descendants(id: string): AgentRun[] {
    const out: AgentRun[] = [];
    const stack = [...childrenOf(id)];
    while (stack.length) {
      const r = stack.pop() as AgentRun;
      out.push(r);
      stack.push(...childrenOf(r.id));
    }
    return out;
  }
  function blockedReason(r: AgentRun): "running" | "pending_approval" | null {
    if (r.status === "running" || r.status === "queued") return "running";
    if ([...state.approvals.values()].some((a) => a.run_id === r.id && a.status === "pending")) return "pending_approval";
    return null;
  }
  /** Why a run can't be deleted yet: any busy descendant blocks it with reason "running". */
  function deleteReason(id: string): { reason: "running" | "pending_approval"; children: string[] } | null {
    const kids = descendants(id).filter((c) => blockedReason(c) !== null).map((c) => c.id);
    if (kids.length) return { reason: "running", children: kids };
    const own = blockedReason(getRun(id));
    return own ? { reason: own, children: [] } : null;
  }
  function pendingOf(runId: string): { questions: Record<string, unknown>[]; approvals: Approval[] } {
    return {
      questions: [...state.questions.values()].filter((q) => q.run_id === runId)
        .map((q) => ({ question_id: q.question_id, run_id: q.run_id, question: q.question, options: q.options, questions: q.items ?? [], status: "pending" })),
      approvals: [...state.approvals.values()].filter((a) => a.run_id === runId && a.status === "pending"),
    };
  }

  function removeRun(id: string): void {
    state.runs.delete(id);
    state.steps = state.steps.filter((s) => s.run_id !== id);
    for (const a of [...state.approvals.values()]) if (a.run_id === id) state.approvals.delete(a.id);
  }
  function cleanupCandidates(projectId: string | null, olderThanDays: number | null, statusIn: string[]): string[] {
    const terminal = new Set(["done", "failed", "cancelled"]);
    const statuses = statusIn.filter((s) => terminal.has(s));
    if (!statuses.length) return [];
    const cutoff = olderThanDays ? now() - olderThanDays * 86400 : null;
    const out: string[] = [];
    for (const r of state.runs.values()) {
      if (r.provider === "ui" || !statuses.includes(r.status)) continue;
      if (projectId && r.project_id !== projectId) continue;
      if (cutoff !== null && (r.last_activity_at ?? r.updated_at) > cutoff) continue;
      if (deleteReason(r.id) !== null) continue;
      out.push(r.id);
    }
    return out;
  }
  /** The orchestrator → worker forest (ui runs excluded), each node run_view-shaped. */
  function runsTree(projectId: string | null): (AgentRun & { children: unknown[] })[] {
    const runs = [...state.runs.values()].filter((r) => r.provider !== "ui" && (!projectId || r.project_id === projectId));
    const nodes = new Map(runs.map((r) => [r.id, { ...runView(r), children: [] as unknown[] }]));
    const roots: (AgentRun & { children: unknown[] })[] = [];
    for (const r of runs) {
      const node = nodes.get(r.id)!;
      const parent = r.parent_id !== null ? nodes.get(r.parent_id) : undefined;
      if (parent) parent.children.push(node);
      else roots.push(node);
    }
    return roots;
  }

  function expireIfDue(a: Approval): Approval {
    if (a.status === "pending" && a.expires_at <= now()) {
      const e = { ...a, status: "expired" as const };
      state.approvals.set(a.id, e);
      return e;
    }
    return a;
  }

  function newRun(agent: string, provider: string, model: string, task: string, external = false, status: AgentRun["status"] = "running"): AgentRun {
    const t = now();
    const run: AgentRun = {
      id: hex(6), parent_id: null, agent, provider, model, task, status, branch: null,
      onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0, created_at: t, updated_at: t, owner_pid: process.pid, external,
      // the core keeps the Claude Code session id it resumes with
      session_ref: provider === CLAUDE_CODE ? randomUUID() : null,
    };
    state.runs.set(run.id, run);
    return run;
  }

  function glbFor(partId: string): string {
    const rel = state.glb[partId];
    return rel ? resolve(SCENARIO_DIR, rel) : defaultGlb;
  }

  // -- routes

  const buildsDeps: BuildsDeps = {
    get: () => state.builds, parts: () => state.parts, setParts: (list) => { state.parts = list; },
    profile: () => state.setup.profile as unknown as ReturnType<BuildsDeps["profile"]>,
    emit: (ev) => { emit(ev); },
  };

  const routes: Route[] = [
    { method: "GET", re: /^\/health$/, h: () => ({ ok: true }) },
    {
      method: "POST", re: /^\/runs$/, h: (_m, _q, raw) => {
        const b = asBody(raw);
        const agent = req(b, "agent", "string");
        const prompt = req(b, "prompt", "string");
        const modelRef = b.model_ref;
        if (modelRef !== undefined && modelRef !== null && typeof modelRef !== "string") throw wrongType("model_ref", "string", modelRef);
        strList(b, "edit_scope", ["*"]);
        strList(b, "read_scope", ["*"]);
        const sketchId = checkRunSketch(b.sketch_id, state.sketches);
        // C1: an explicit build for the run (the app always sends it); unknown or trashed → 404
        const projectId = b.project_id;
        if (projectId !== undefined && projectId !== null && typeof projectId !== "string") throw wrongType("project_id", "string", projectId);
        if (typeof projectId === "string" && !state.builds.builds.some((x) => x.id === projectId && x.trashed_at === null)) {
          throw mises("not_found", `unknown build: ${projectId}`);
        }
        const def = state.agents.find((a) => a.name === agent);
        if (!def) throw mises("not_found", `unknown agent: ${agent}`, `agents: ${state.agents.map((a) => a.name).join(", ")}`);
        const ref = (typeof modelRef === "string" && modelRef) || (def.model !== "inherit" ? def.model : state.models.default);
        const slash = ref.indexOf("/");
        const vendor = slash > 0 ? ref.slice(0, slash) : "";
        const model = slash > 0 ? ref.slice(slash + 1) : "";
        const vendors = models().vendors;
        const spec = vendors.find((v) => v.vendor === vendor);
        if (!spec || !model) {
          throw mises("invalid_input", `unknown vendor or empty model in ${JSON.stringify(ref)}`, `vendors: ${vendors.map((v) => v.vendor).join(", ")}`);
        }
        if (spec.key === "none") throw mises("denied", `no API key for ${vendor}`, `run \`mises keys set ${vendor}\``);
        // the user's Claude Code: same checks and answers as core Runtime._start_claude_run
        const claude = vendor === CLAUDE_CODE;
        if (claude && !CLAUDE_ALIAS_RE.test(model)) throw mises("invalid_input", `bad Claude Code model alias: ${JSON.stringify(model)}`, "use claude-code/sonnet, opus, haiku or fable");
        if (claude && spec.key === "not_installed") throw mises("not_found", "Claude Code is not installed", "install it from code.claude.com, then sign in from Setup");
        if (claude && spec.key !== "signed_in") throw mises("denied", "Claude Code is not signed in", "sign in from Setup or Profile (runs `claude auth login`)");
        // Plan 4: beyond the parallel limit a run waits (status "queued", event run_queued)
        const limit = state.status.parallel?.limit;
        const working = [...state.runs.values()].filter((r) => r.status === "running").length;
        const queued = limit !== undefined && working >= limit;
        const run = newRun(agent, vendor, model, prompt, claude, queued ? "queued" : "running");
        if (typeof projectId === "string") run.project_id = projectId;
        // a sketch run's user step: like the core, the words sent plus the sketch as `context`
        const sk = sketchId ? state.sketches.get(sketchId) : undefined;
        appendStep({ kind: "message", payload: sk ? sketchUserPayload(prompt, sk) : { role: "user", text: prompt } }, run.id);
        if (queued) {
          emit({ type: "run_queued", run_id: run.id, data: { agent, provider: vendor, model, limit } });
          return { run_id: run.id };
        }
        emit({ type: "run_started", run_id: run.id, data: { agent, provider: vendor, model, parent_id: null, ...(claude ? { external: true } : {}) } });
        play(state.on.create_run, run.id);
        return { run_id: run.id };
      },
    },
    {
      method: "POST", re: /^\/runs\/external$/, h: (_m, _q, raw) => {
        const b = asBody(raw);
        const agent = req(b, "agent", "string");
        const provider = req(b, "provider", "string");
        const model = req(b, "model", "string");
        if (!agent) throw mises("invalid_input", "external run needs an agent name");
        const run = newRun(agent, provider, model, `external agent: ${agent}`, true);
        emit({ type: "run_started", run_id: run.id, data: { agent, provider, model, parent_id: null, external: true } });
        return { run_id: run.id };
      },
    },
    {
      method: "POST", re: /^\/runs\/cancel_all$/, h: () => {
        const ids = [...state.runs.values()].filter((r) => r.status === "running").map((r) => r.id);
        ids.forEach(cancelRun);
        return { cancelled: ids, failed: [] };
      },
    },
    {
      method: "GET", re: /^\/runs$/, h: (_m, q) => {
        const limit = Number(q.get("limit") ?? 50);
        // like the core: the app's own "ui" run is hidden unless include_ui=1
        const withUi = Number(q.get("include_ui") ?? 0) !== 0;
        return [...state.runs.values()].filter((r) => withUi || r.provider !== "ui").sort((a, b) => b.created_at - a.created_at).slice(0, limit)
          .map(runView);
      },
    },
    // ---- Plan 5 (C1): run tree, cleanup, patch, delete. Must precede /runs/{id}.
    {
      method: "GET", re: /^\/runs\/tree$/, h: (_m, q) => {
        const projectId = q.get("project_id");
        return runsTree(projectId);
      },
    },
    {
      method: "POST", re: /^\/runs\/cleanup$/, h: (_m, _q, raw) => {
        const b = asBody(raw);
        const projectId = typeof b.project_id === "string" ? b.project_id : null;
        const olderThanDays = typeof b.older_than_days === "number" ? b.older_than_days : null;
        const statusIn = Array.isArray(b.status_in) ? (b.status_in as string[]) : ["done", "failed", "cancelled"];
        const dryRun = b.dry_run !== false;
        const ids = cleanupCandidates(projectId, olderThanDays, statusIn);
        if (!dryRun) for (const id of ids) removeRun(id);
        return { ids, count: ids.length };
      },
    },
    {
      method: "PATCH", re: /^\/runs\/([^/]+)$/, h: (m, _q, raw) => {
        const r = getRun(m[1]);
        const b = asBody(raw);
        const next = { ...r };
        if (typeof b.title === "string") next.title = b.title;
        if (typeof b.pinned === "boolean") next.pinned = b.pinned;
        if (typeof b.archived === "boolean") next.archived = b.archived;
        next.updated_at = now();
        state.runs.set(r.id, next);
        return runView(next);
      },
    },
    {
      method: "DELETE", re: /^\/runs\/([^/]+)$/, h: (m, _q, _raw, _r, res) => {
        getRun(m[1]);
        const refused = deleteReason(m[1]);
        if (refused) {
          send(res, 409, refused);
          return RAW;
        }
        removeRun(m[1]);
        return { ok: true };
      },
    },
    // like the core's GET /runs/{id}: run_view plus the live `activity` (C4; null when none)
    { method: "GET", re: /^\/runs\/([^/]+)$/, h: (m) => ({ ...runView(getRun(m[1])), activity: lastActivity.get(m[1]) ?? null }) },
    // the QA-fix core's open ui_ask_user questions and approvals of a run, so a reload brings a
    // question card back (C-2). Assumed shape; the app also reads `pending` inside GET /runs/{id}.
    // (GET /runs/{id} stays the real core's dumped shape: test/contract.routes.test.ts.)
    { method: "GET", re: /^\/runs\/([^/]+)\/pending$/, h: (m) => { getRun(m[1]); return pendingOf(m[1]); } },
    {
      method: "GET", re: /^\/runs\/([^/]+)\/steps$/, h: (m, q) => {
        getRun(m[1]);
        const after = Number(q.get("after_id") ?? 0);
        return state.steps.filter((s) => s.run_id === m[1] && s.id > after);
      },
    },
    {
      method: "POST", re: /^\/runs\/([^/]+)\/message$/, h: (m, _q, raw) => {
        const text = req(asBody(raw), "text", "string");
        const r = getRun(m[1]);
        // Claude Code runs are external but take messages: the core continues (or resumes) the session
        if (r.external && r.provider !== CLAUDE_CODE) throw mises("invalid_input", "run is driven by an external agent", "send the message to that agent instead");
        if (r.status === "running") throw mises("invalid_input", "run is still working", "wait for it to finish or stop it");
        appendStep({ kind: "message", payload: { role: "user", text } }, r.id);
        emit({ type: "run_started", run_id: r.id, data: { resumed: true } });
        return { ok: true };
      },
    },
    {
      method: "POST", re: /^\/runs\/([^/]+)\/cancel$/, h: (m) => {
        getRun(m[1]);
        cancelRun(m[1]);
        return { ok: true };
      },
    },
    {
      method: "GET", re: /^\/approvals$/, h: (_m, q) => {
        const status = q.get("status") ?? "pending";
        if (status !== "pending") throw mises("invalid_input", `unsupported status filter: ${status}`, "only status=pending is supported");
        const runId = q.get("run_id");
        return [...state.approvals.values()].filter((a) => a.status === "pending" && (!runId || a.run_id === runId));
      },
    },
    {
      method: "POST", re: /^\/approvals\/([^/]+)\/decide$/, h: (m, _q, raw) => {
        const b = asBody(raw);
        const hash = req(b, "payload_hash", "string");
        const approve = req(b, "approve", "boolean");
        const found = state.approvals.get(m[1]);
        if (!found) throw mises("not_found", `unknown approval: ${m[1]}`);
        const a = expireIfDue(found);
        if (a.payload_hash !== hash) throw mises("stale_version", "approval payload changed since the card was shown", "refresh the approval card");
        if (a.status !== "pending") return a;
        const edited = approve && b.edited_payload && typeof b.edited_payload === "object" ? (b.edited_payload as Record<string, unknown>) : null;
        const decided: Approval = { ...a, status: approve ? "approved" : "rejected", decided_by: DECIDED_BY, decided_at: now(), edited_payload: edited };
        state.approvals.set(a.id, decided);
        resumeActivity(a.run_id);
        play(approve ? state.on.decide : state.on.decide_reject, a.run_id);
        return decided;
      },
    },
    {
      method: "POST", re: /^\/questions\/([^/]+)\/answer$/, h: (m, _q, raw) => {
        req(asBody(raw), "text", "string");
        if (!state.questions.has(m[1])) throw mises("not_found", `no open question: ${m[1]}`);
        const q = state.questions.get(m[1]) as Question;
        emit({ type: "question_answered", run_id: q.run_id, data: { question_id: q.question_id } });
        resumeActivity(q.run_id);
        return { ok: true };
      },
    },
    {
      method: "GET", re: /^\/status$/, h: () => ({
        data_dir: FAKE_DATA_DIR,
        ...state.status,
        runs_running: [...state.runs.values()].filter((r) => r.status === "running").length,
        approvals_pending: [...state.approvals.values()].filter((a) => a.status === "pending").length,
      }),
    },
    {
      // An agent carrying a `builtin` flag (a created custom agent, or a scenario that opts in)
      // is returned in the Plan 5 shape; a plain scenario agent stays {name,description,model}
      // so the pre-Plan-5 contract fixture (agents.json) still matches shape.
      method: "GET", re: /^\/tools$/, h: () => {
        if (state.tools === null) throw new HttpError(404, { detail: "Not Found" });
        return state.tools;
      },
    },
    {
      method: "GET", re: /^\/agents$/, h: () => {
        const hidden = new Set(state.setup.profile.hidden_agents ?? []);
        // the Plan 5 shape for every agent (the re-dumped agents.json); scenario agents are built-ins
        return [...state.agents]
          .map((a) => ({ name: a.name, description: a.description, model: a.model,
            tools: a.tools ?? null, builtin: a.builtin ?? true, hidden: hidden.has(a.name) }))
          .sort((a, b) => a.name.localeCompare(b.name));
      },
    },
    {
      // custom agents only; built-ins are hidden via the profile's hidden_agents (PUT /profile)
      method: "POST", re: /^\/agents$/, h: (_m, _q, raw) => {
        const b = asBody(raw);
        const name = req(b, "name", "string");
        if (!/^[a-z][a-z0-9_-]{1,40}$/.test(name)) throw mises("invalid_input", `bad agent name: ${JSON.stringify(name)}`, "lowercase letters, digits, - and _; start with a letter");
        if (state.agents.some((a) => a.name === name)) throw mises("invalid_input", `agent ${JSON.stringify(name)} already exists`);
        const agent: AgentInfo = {
          name, description: typeof b.description === "string" ? b.description : "",
          model: typeof b.model === "string" ? b.model : "inherit",
          tools: Array.isArray(b.tools) ? (b.tools as string[]) : null, builtin: false, hidden: false,
        };
        state.agents = [...state.agents, agent];
        agentBodies.set(name, typeof b.body === "string" ? b.body.trim() : "");
        return { ...agent };
      },
    },
    {
      // core projects_api.get_agent: the body for a custom agent, null for a built-in
      method: "GET", re: /^\/agents\/([^/]+)$/, h: (m) => {
        const name = decodeURIComponent(m[1]);
        const a = state.agents.find((x) => x.name === name);
        if (!a) throw mises("not_found", `no agent ${JSON.stringify(name)}`);
        const builtin = a.builtin ?? true;
        return { name: a.name, description: a.description, model: a.model, tools: a.tools ?? null, builtin,
          hidden: (state.setup.profile.hidden_agents ?? []).includes(name), body: builtin ? null : agentBodies.get(name) ?? "" };
      },
    },
    {
      method: "PUT", re: /^\/agents\/([^/]+)$/, h: (m, _q, raw) => {
        const name = decodeURIComponent(m[1]);
        const existing = state.agents.find((a) => a.name === name);
        if (existing && (existing.builtin ?? true)) throw mises("denied", `${JSON.stringify(name)} is a built-in agent; it can be hidden but not changed or deleted`, "hide it via the profile instead");
        if (!existing) throw mises("not_found", `no custom agent ${JSON.stringify(name)}`);
        const b = asBody(raw);
        const agent: AgentInfo = {
          name, description: typeof b.description === "string" ? b.description : "",
          model: typeof b.model === "string" ? b.model : "inherit",
          tools: Array.isArray(b.tools) ? (b.tools as string[]) : null, builtin: false,
          hidden: (state.setup.profile.hidden_agents ?? []).includes(name),
        };
        // like the core: a PUT without a body keeps the current instructions
        if (typeof b.body === "string") agentBodies.set(name, b.body.trim());
        state.agents = state.agents.map((a) => (a.name === name ? agent : a));
        return { ...agent };
      },
    },
    {
      method: "DELETE", re: /^\/agents\/([^/]+)$/, h: (m) => {
        const name = decodeURIComponent(m[1]);
        const existing = state.agents.find((a) => a.name === name);
        if (existing && (existing.builtin ?? true)) throw mises("denied", `${JSON.stringify(name)} is a built-in agent; it can be hidden but not changed or deleted`, "hide it via the profile instead");
        state.agents = state.agents.filter((a) => a.name !== name);
        agentBodies.delete(name);
        state.setup.profile.hidden_agents = (state.setup.profile.hidden_agents ?? []).filter((a) => a !== name);
        return { ok: true };
      },
    },
    { method: "GET", re: /^\/models$/, h: () => models() },
    // GET /parts (?project_id) lives in buildRoutes: the part tree of a build
    ...buildRoutes(buildsDeps),
    {
      method: "GET", re: /^\/parts\/([^/]+)\/model\.glb$/, h: (m, _q, _b, r, res) => {
        const id = decodeURIComponent(m[1]);
        if (!PART_ID_RE.test(id)) throw mises("invalid_input", `bad part_id: ${JSON.stringify(id)}`, "lowercase letters, digits, underscores; start with a letter");
        const part = state.parts.find((p) => p.part_id === id);
        const file = glbFor(id);
        if (!part || !existsSync(file)) throw mises("not_found", `no model.glb for part ${id}`, "build the part first");
        const size = statSync(file).size;
        const etag = `"${BigInt(Math.round(part.mtime * 1e9))}-${size}"`;
        if (etagMatches(r.headers["if-none-match"], etag)) {
          res.writeHead(304, { ETag: etag });
          res.end();
          return RAW;
        }
        res.writeHead(200, { "Content-Type": "model/gltf-binary", "Content-Length": size, ETag: etag });
        res.end(readFileSync(file));
        return RAW;
      },
    },
    ...setupRoutes(() => state.setup, () => state.parts),
    // ---- Plan 4: Ollama on this Mac (core local_models.py); 404 without a `local` block (older core)
    { method: "GET", re: /^\/local\/status$/, h: () => localState() },
    {
      method: "POST", re: /^\/local\/start$/, h: () => {
        // core local_models.start_ollama: failures are {ok:false, error, hint}, not HTTP errors
        const l = localState();
        if (l.running) return { ok: true, running: true, note: "Ollama is already running" };
        if (!l.installed) return { ok: false, error: "not_installed", hint: "Install Ollama: `brew install ollama` (or the app from ollama.com), then retry" };
        state.local = { ...l, running: true };
        return { ok: true, running: true, note: "Started `ollama serve` in the background; it keeps running after Mises quits (stop it with `pkill ollama`). Log: logs/ollama.log in the Mises data folder" };
      },
    },
    {
      method: "POST", re: /^\/local\/pull\/cancel$/, h: (_m, _q, raw) => {
        const model = pullName(raw);
        localState();
        const timers = state.pulling.get(model);
        if (!timers) return { ok: false };
        timers.forEach(clearTimeout);
        state.pulling.delete(model);
        emit({ type: "local_pull", run_id: null, data: { model, completed: 0, total: 0, status: "cancelled" } });
        return { ok: true };
      },
    },
    {
      method: "POST", re: /^\/local\/pull$/, h: (_m, _q, raw) => {
        // core ui_api.local_pull: one download per model, at most 2 at once; refusals are 200s
        const model = pullName(raw);
        const l = localState();
        if (state.pulling.has(model)) return { ok: false, already: true };
        if (!l.running) return { ok: false, error: "not_running", hint: "Ollama isn't running — start it with `ollama serve`" };
        if (state.pulling.size >= 2) return { ok: false, error: "too_many_pulls", hint: "at most 2 downloads at once" };
        const rec = l.recommended.find((r) => r.name === model);
        const total = Math.round((rec?.size_gb ?? 1) * 1e9);
        const steps = [0, 0.25, 0.6, 1];
        const timers = steps.map((f, i) => setTimeout(() => {
          const done = i === steps.length - 1;
          if (done) {
            state.pulling.delete(model);
            if (state.local && !state.local.models.some((m) => m.name === model)) {
              state.local = { ...state.local, models: [...state.local.models, { name: model, size_gb: rec?.size_gb ?? 1 }] };
            }
          }
          emit({ type: "local_pull", run_id: null, data: { model, completed: Math.round(total * f), total, status: done ? "success" : i === 0 ? "pulling manifest" : "downloading" } });
        }, state.localPullMs * (i + 1)));
        state.pulling.set(model, timers);
        scriptTimers.push(...timers);
        return { ok: true };
      },
    },
    ...simRoutes({
      sims: () => state.sims, parts: () => state.parts, defaultMaterial: () => state.setup.profile.default_material,
      newRun, emit,
    }),
    ...sketchRoutes(() => state.sketches),
    ...directoryRoutes({ get: () => state.directory, emit: (ev) => { emit(ev); } }),
    ...specRoutes({
      get: () => state.spec, parts: () => state.parts, setParts: (p) => { state.parts = p; }, emit: (ev) => emit(ev),
      partVersion: (id) => state.builds.rows.get(id)?.version,
      bumpPart: (id, summary) => {
        const r = state.builds.rows.get(id);
        if (!r) return;
        r.version += 1;
        emit({ type: "part_changed", run_id: state.builds.uiRun, data: { part_id: id, version: r.version, by: "user", run_id: null, summary } });
      },
    }),
    ...printingRoutes({
      get: () => state.print, parts: () => state.parts, defaultMaterial: () => state.setup.profile.default_material,
      emit: (ev, extras) => { emit(ev, extras); }, approval: (id) => state.approvals.get(id),
    }),
    ...assemblyRoutes({ get: () => state.assembly, parts: () => state.parts, emit: (ev) => { emit(ev); } }),
    { method: "GET", re: /^\/system\/check$/, h: () => state.system },
    {
      // POST /system/install/{dep}: allowlist only, no arguments from the body (Review Focus 3).
      method: "POST", re: /^\/system\/install\/([^/]+)$/, h: (m) => {
        const dep = m[1];
        if (!(INSTALL_DEPS as readonly string[]).includes(dep)) {
          throw mises("invalid_input", `unknown dependency ${dep}`, `one of ${INSTALL_DEPS.join(", ")}`);
        }
        const cfg = state.systemInstall[dep] ?? {};
        if (cfg.fix) return { ok: false, fix: cfg.fix };          // e.g. brew missing → open_url
        const lines = cfg.lines ?? [`Installing ${dep}…`, "done"];
        const ok = cfg.ok ?? true;
        lines.forEach((line, i) => {
          const t = setTimeout(() => emit({ type: "system_install", run_id: null, data: { dep, line, done: false, ok: false } }),
            state.systemInstallMs * (i + 1));
          scriptTimers.push(t);
        });
        const done = setTimeout(() => emit({ type: "system_install", run_id: null, data: { dep, line: "", done: true, ok } }),
          state.systemInstallMs * (lines.length + 1));
        scriptTimers.push(done);
        return { ok: true, fix: null };
      },
    },
  ];

  /** Core ui_api._pull_name: lowercase Ollama names; the submitted name is never echoed. */
  function pullName(raw: unknown): string {
    const model = typeof raw === "object" && raw !== null && !Array.isArray(raw) ? (raw as Record<string, unknown>).model : undefined;
    if (typeof model !== "string" || !/^[a-z0-9][a-z0-9._/:-]{0,120}$/.test(model)) {
      throw mises("invalid_input", "invalid model name", "lowercase Ollama names like qwen3-coder:30b");
    }
    return model;
  }

  function localState(): LocalStatus {
    if (!state.local) throw new HttpError(404, { detail: "Not Found" });
    return state.local;
  }

  /** /models: with a setup block, vendor key status follows the saved keys and Claude Code status. */
  function models(): ModelsInfo {
    const st = state.setup;
    if (!st.derived) return state.models;
    return {
      ...state.models,
      vendors: state.models.vendors.map((v) => {
        if (v.vendor === "claude-code") return { ...v, key: !st.claude.installed ? "not_installed" : st.claude.signed_in ? "signed_in" : "signed_out" };
        if (v.key === "not_needed") return v;
        return { ...v, key: st.keys[v.vendor] ?? "none" };
      }),
    };
  }

  // -- request pipeline: CORS (outermost) → auth → control/real routes

  function corsHeaders(origin: string | undefined): Record<string, string> {
    if (!origin || !origins.has(origin)) return {};
    return { "Access-Control-Allow-Origin": origin, "Access-Control-Expose-Headers": "ETag", Vary: "Origin" };
  }

  function send(res: ServerResponse, status: number, body: unknown, extra: Record<string, string> = {}) {
    const text = JSON.stringify(body);
    res.writeHead(status, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(text), ...extra });
    res.end(text);
  }

  const server = createServer((r, res) => {
    void handle(r, res).catch((e: unknown) => {
      if (!res.headersSent) send(res, 500, { detail: String(e) });
      else res.end();
    });
  });

  async function handle(r: IncomingMessage, res: ServerResponse) {
    const origin = r.headers.origin;
    const cors = corsHeaders(origin);
    for (const [k, v] of Object.entries(cors)) res.setHeader(k, v);
    const url = new URL(r.url ?? "/", "http://127.0.0.1");
    const method = r.method ?? "GET";

    if (method === "OPTIONS" && r.headers["access-control-request-method"]) {
      if (!cors["Access-Control-Allow-Origin"]) {
        res.writeHead(400, { "Content-Type": "text/plain" });
        res.end("Disallowed CORS origin");
        return;
      }
      // Starlette's CORSMiddleware refuses a preflight asking for a header outside the list
      const asked = String(r.headers["access-control-request-headers"] ?? "").split(",").map((h) => h.trim().toLowerCase()).filter(Boolean);
      if (asked.some((h) => !CORS_ALLOW_HEADERS.map((x) => x.toLowerCase()).includes(h))) {
        res.writeHead(400, { "Content-Type": "text/plain" });
        res.end("Disallowed CORS headers");
        return;
      }
      res.writeHead(200, {
        "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE", "Access-Control-Allow-Headers": CORS_ALLOW_HEADERS.join(", "),
        "Access-Control-Max-Age": "600", "Content-Length": 0,
      });
      res.end();
      return;
    }

    const auth = r.headers.authorization ?? "";
    const given = auth.toLowerCase().startsWith("bearer ") ? auth.slice(7) : null;
    if (given !== state.token) {
      send(res, 401, { error: "unauthorized", message: "missing or bad token" }, { "WWW-Authenticate": "Bearer" });
      return;
    }

    try {
      const body = method === "POST" || method === "PUT" || method === "PATCH" ? await readBody(r) : undefined;
      if (url.pathname.startsWith("/__fake/")) {
        const out = await handleControl(api, method, url.pathname, body);
        if (out === undefined) throw new HttpError(404, { detail: "Not Found" });
        send(res, 200, out);
        return;
      }
      const inm = r.headers["if-none-match"];
      state.calls.push({ method, path: url.pathname, query: Object.fromEntries(url.searchParams), body: body ?? null, ts: now(), ...(typeof inm === "string" ? { ifNoneMatch: inm } : {}) });
      const matching = routes.filter((x) => x.re.test(url.pathname));
      const route = matching.find((x) => x.method === method);
      if (!route) throw new HttpError(matching.length ? 405 : 404, { detail: matching.length ? "Method Not Allowed" : "Not Found" });
      const out = route.h(url.pathname.match(route.re) as RegExpMatchArray, url.searchParams, body, r, res);
      if (out !== RAW) send(res, 200, out);
    } catch (e) {
      if (e instanceof HttpError) send(res, e.status, e.body);
      else throw e;
    }
  }

  // -- websocket

  const wss = new WebSocketServer({ noServer: true });
  server.on("upgrade", (r, socket, head) => {
    const url = new URL(r.url ?? "/", "http://127.0.0.1");
    if (url.pathname !== "/events") {
      socket.write("HTTP/1.1 403 Forbidden\r\n\r\n");
      socket.destroy();
      return;
    }
    wss.handleUpgrade(r, socket, head, (ws) => {
      if (url.searchParams.get("token") !== state.token) {
        ws.close(4401);
        return;
      }
      const entry = { ws, runId: url.searchParams.get("run_id") };
      sockets.add(entry);
      ws.on("close", () => sockets.delete(entry));
      ws.on("error", () => sockets.delete(entry));
      startScript();
    });
  });

  function startScript() {
    if (scriptStarted) return;
    scriptStarted = true;
    for (const entry of state.script) {
      const { at_ms, event, ...extras } = entry;
      scriptTimers.push(setTimeout(() => emit(event, extras), at_ms));
    }
  }

  /** Plays a trigger script for `runId` ("$run" in it becomes the id). Entries run in order. */
  function play(entries: ScriptEntry[] | undefined, runId: string) {
    if (!entries?.length) return;
    const bound = JSON.parse(JSON.stringify(entries).replaceAll("$run", runId)) as ScriptEntry[];
    for (const entry of bound) {
      const { at_ms, event, ...extras } = entry;
      scriptTimers.push(setTimeout(() => emit(event, extras), at_ms));
    }
  }

  function dropSockets(mode: "close" | "terminate" = "close") {
    for (const s of [...sockets]) {
      if (mode === "terminate") s.ws.terminate();
      else s.ws.close(1012, "service restart");
      sockets.delete(s);
    }
  }

  function reset(scenario: string | Scenario = opts.scenario, token?: string) {
    scriptTimers.forEach(clearTimeout);
    scriptTimers = [];
    scriptStarted = false;
    clearSimTimers(state.sims);
    dropSockets();
    stopPrinting(state.print);
    state = stateFrom(loadScenario(scenario), token ?? state.token);
  }

  await new Promise<void>((ok, fail) => {
    server.once("error", fail);
    server.listen(opts.port ?? 0, "127.0.0.1", () => ok());
  });
  const port = (server.address() as AddressInfo).port;

  const api: FakeCore = {
    url: `http://127.0.0.1:${port}`,
    get token() { return state.token; },
    get state() { return state; },
    emit,
    reset,
    dropSockets,
    agentEdit: (b) => agentEdit(buildsDeps, b),
    rotateToken(token: string) {
      state.token = token;
      dropSockets();
    },
    async stop() {
      scriptTimers.forEach(clearTimeout);
      clearSimTimers(state.sims);
      stopPrinting(state.print);
      dropSockets("terminate");
      wss.close();
      server.closeAllConnections();
      await new Promise<void>((ok) => server.close(() => ok()));
    },
  };
  return api;
}

export const RAW = Symbol("raw-response");

export type Handler = (m: RegExpMatchArray, q: URLSearchParams, body: unknown, r: IncomingMessage, res: ServerResponse) => unknown;
export interface Route { method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE"; re: RegExp; h: Handler }
