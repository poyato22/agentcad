// Typed HTTP client for the local core. Every request carries the bearer token; every response
// is validated with zod. Failures always surface as CoreError (never a raw fetch/zod error).
import { z } from "zod";
import { feedbackResultSchema, measureResultSchema, measureWarmSchema, type FeedbackBody, type FeedbackResult, type MeasureRef, type MeasureResult } from "./measure";
import { debug } from "../lib/log";
import type { Appearance } from "../lib/appearance";
import {
  agentDetailSchema, agentInfoSchema, agentRunSchema, approvalSchema, cancelAllSchema, claudeCodeStatusSchema, cleanupResultSchema, coreErrorBodySchema, coreStatusSchema, deleteRefusedSchema,
  agentsViewSchema, createRunResultSchema,
  keyStatusSchema, localActionSchema, localStatusSchema, localTestSchema, modelsInfoSchema, okSchema, onshapeTestSchema, partSummarySchema, profileSchema, runNodeSchema, setupStateSchema, sketchSummarySchema,
  questionAskedSchema, stepSchema, systemCheckRowSchema, systemInstallStartSchema,
} from "./schemas";
import {
  calibrationPreviewSchema, calibrationSchema, type Calibration, type CalibrationBody, type CalibrationPreview,
} from "./accuracy";
import {
  simRecordSchema, simSolversSchema, simStartedSchema, simSummarySchema,
  type SimRecord, type SimSolvers, type SimStartBody, type SimStarted, type SimSummary,
} from "./sims";
import {
  topoptAcceptResultSchema, topoptRecordSchema,
  type TopoptAcceptBody, type TopoptAcceptResult, type TopoptRecord, type TopoptStartBody,
} from "./topopt";
import {
  profileActiveSchema, projectsLiteSchema, specCheckSchema, specDocSchema, studyAppliedSchema, studySchema,
  type Spec, type SpecCheck, type SpecDoc, type Study, type StudyApplied, type StudyApplyBody,
} from "./spec";
import {
  studyCancelSchema, studyChartsSchema, studyCompareSchema, studyMaterialSchema, studyMaterialsSchema, studyProbeSchema, studyRecordSchema,
  studySectionSchema, studySolversSchema, studySummarySchema, studyTemplateSchema, studyTypeInfoSchema,
  type CustomMaterialBody, type StudyCharts, type StudyCompare, type StudyMaterial, type StudyMaterials, type StudyProbe, type StudyRecord,
  type StudySection, type StudySolvers, type StudyStartBody, type StudySummary, type StudyTemplate, type StudyType, type StudyTypeInfo,
} from "./studies";
import { studiesContextSchema, type StudiesContext } from "./studiesAgent";
import {
  assemblyKindsSchema, assemblyStartedSchema, assemblyStudyRecordSchema, assemblyStudySummarySchema,
  type AssemblyKind, type AssemblyStudyKind, type AssemblyStudyRecord, type AssemblyStudySummary,
} from "./assemblyStudies";
import { effortDefaultSchema, type EffortDefault, type ThinkChoice } from "./think";
import { backgroundJobSchema, jobCancelSchema, type BackgroundJob, type JobCancel } from "./jobs";
import type {
  AgentDefBody, AgentDetail, AgentInfo, AgentRun, Approval, CancelAllResult, ClaudeCodeStatus, CleanupBody, CleanupResult, CoreInfo, CoreStatusInfo, DecisionBody, DeleteRefused, ErrorCode, KeyStatus, LocalActionResult, LocalStatus, LocalTestResult,
  AgentsView, CreateRunResult, ModelsInfo, NewRunBody, OnshapeTestResult, PartSummary, Profile, ProfilePatch, Question, RunNode, RunPatch, SetupState, SketchSummary, SketchUpload, Step,
  SystemCheckRow, SystemInstallStart, UiContext,
} from "./types";
import {
  drawingInfoSchema, printCallSchema, printCapabilitiesSchema, printerSchema, printerStatusSchema, printStartSchema, sliceSchema, sliceStartSchema,
} from "./schemas";
import {
  activeBuildSchema, buildRefSchema, catalogItemSchema, onshapeBudgetSchema, onshapeDocSchema, onshapeElementSchema, partResultSchema,
  type BuildRef, type CatalogEdit, type CatalogItem, type OnshapeBudget, type OnshapeDoc, type OnshapeElement, type PartResult,
} from "./directory";
import { drawingImportSchema, type DrawingImport } from "./imports";
import {
  conceptDecisionSchema, conceptSketchSchema, conceptViewSchema, feedbackSentSchema, pitchChosenSchema,
  type ChooseBody, type ConceptDecision, type ConceptSketch, type ConceptView, type FeedbackSent, type PitchChosen, type SketchFeedbackBody,
} from "./concept";
import type { DrawingInfo, NewPrinterBody, PrintCall, PrintCapabilities, Printer, PrinterStatus, PrintStart, Slice, SliceStart } from "./types";
import {
  assemblyPartSchema, assemblySchema, driveResultSchema, instanceDeletedSchema, instanceSchema, interferenceSchema, jointDeletedSchema,
  jointSchema, projectRowSchema,
  type Assembly, type AssemblyPart, type DriveResult, type Instance, type InstancePatch, type Interference, type Joint, type JointPatch,
  type JointValue, type NewInstanceBody, type NewJointBody, type ProjectRow,
} from "./assembly";
import {
  buildSchema, buildTabsProfileSchema, emptyTrashRefusedSchema, emptyTrashSchema, partEditResultSchema, partSourceSchema,
  type Build, type BuildTabsProfile, type EmptyTrashRefused, type EmptyTrashResult, type OpRequest, type PartEditResult, type PartSource,
} from "./builds";
import {
  bomBody, bomRowSchema, bomSchema, browserListSchema, browserSnapshotSchema, isProxyPath, partCardSchema, suggestedSitesSchema,
  type Bom, type BomPatch, type BomRow, type BrowserSnapshot, type NewBomRow, type PartCard, type SuggestedSite,
} from "./browser";

type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

/** The fields PUT /profile may carry: core profile.Profile minus units, setup_completed_at and
 *  schema_version, which the app never writes. */
const PROFILE_PATCH_KEYS: ReadonlySet<string> = new Set<string>([
  "display_name", "default_material", "default_agent", "default_model", "onshape_calls_year_cap", "onshape_calls_run_cap",
  "max_parallel_agents", "local_endpoints", "theme", "hidden_agents", "viewer_background", "suggested_sites", "browser_fetches_per_run",
  // P10 E3: Profile → Claude Code → Network (the core masks proxy credentials and keeps them when the mask comes back)
  "claude_code_env",
] satisfies (keyof ProfilePatch)[]);

/** For routes whose answer the UI doesn't use (it refetches status instead): any 2xx JSON body. */
const anyBody = z.unknown();

export type CoreErrorCode = ErrorCode | "unauthorized" | "validation" | "network";

/** P11 F7: one image attached to a chat message (core server/app.py ImageIn). */
export interface ChatImageIn { media_type: string; data_b64: string }

/** What rides along with a chat message: attached images (P11 F7) and the Think choice (F8). */
export interface ChatOptions { images?: readonly ChatImageIn[]; effort?: ThinkChoice | null }

export class CoreError extends Error {
  readonly status: number;
  readonly code: CoreErrorCode;
  readonly hint: string | null;
  readonly retryable: boolean;
  /** The whole JSON error body (e.g. a 409's `current_version`), or null. Untrusted data. */
  readonly body: Record<string, unknown> | null;

  constructor(code: CoreErrorCode, message: string, opts: { status?: number; hint?: string | null; retryable?: boolean; body?: Record<string, unknown> | null } = {}) {
    super(message);
    this.name = "CoreError";
    this.code = code;
    this.status = opts.status ?? 0;
    this.hint = opts.hint ?? null;
    this.retryable = opts.retryable ?? false;
    this.body = opts.body ?? null;
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
  /**
   * What a working run is doing right now, for seeding the UI on load and reconnect (C-2, C-3):
   * GET /runs/{id}'s live `activity` snapshot ({state, detail, since, tokens_out?}, untrusted:
   * state/activity.ts parses it like an agent_activity event) and its open ui_ask_user questions.
   * The questions come from the run itself (`pending.questions`, `pending_questions` or
   * `questions`), else from GET /runs/{id}/pending; `questions` is null when the core has neither
   * (an older core: the store keeps what it has).
   */
  async runLive(id: string): Promise<RunLive> {
    const r = await this.json("GET", `/runs/${enc(id)}`, z.looseObject({ activity: z.unknown().optional() }));
    const raw = r as Record<string, unknown>;
    const pending = raw.pending as Record<string, unknown> | undefined;
    let qs: unknown = pending && typeof pending === "object" ? pending.questions : undefined;
    if (qs === undefined) qs = raw.pending_questions ?? raw.questions;
    if (qs === undefined) {
      const res = await this.request("GET", `/runs/${enc(id)}/pending`, {});
      if (res.ok) {
        let body: unknown = null;
        try { body = await res.json(); } catch { /* not JSON: no questions known */ }
        qs = Array.isArray(body) ? body : (body as { questions?: unknown } | null)?.questions;
      } else if (res.status !== 404 && res.status !== 405) throw await toError(res);
    }
    return { activity: r.activity ?? null, questions: Array.isArray(qs) ? parsePendingQuestions(id, qs) : null };
  }
  /** One malformed step (e.g. a kind this UI doesn't know yet) is dropped, not the whole page. */
  steps(runId: string, afterId = 0): Promise<Step[]> {
    return this.json("GET", `/runs/${enc(runId)}/steps`, lenientSteps, { query: { after_id: afterId } });
  }
  /** Start a run. With no `agent` the core routes to one (U3c) and the answer carries `routed`. */
  createRun(body: NewRunBody): Promise<CreateRunResult> { return this.json("POST", "/runs", createRunResultSchema, { body }); }
  /** The build's agents (U3c lead.agents_view): its lead with the sub-tree, and its other
   *  top-level runs. A build with no lead yet answers `lead: null`. */
  projectAgents(projectId: string): Promise<AgentsView> {
    return this.json("GET", `/projects/${enc(projectId)}/agents`, agentsViewSchema);
  }
  /** P11 F7: `images` (at most 5, already shrunk by the composer) go with the message as real
   *  image blocks; the core checks them again and keeps them under the build. */
  message(runId: string, text: string, images?: readonly ChatImageIn[]): Promise<{ ok: true }> {
    return this.json("POST", `/runs/${enc(runId)}/message`, okSchema, { body: images?.length ? { text, images } : { text } });
  }
  /** P11 F7: an image attached to a message in this build (the thread's thumbnail). */
  chatImage(projectId: string, imageId: string, mediaType = "image/jpeg"): Promise<Blob> {
    return this.blob(`/projects/${enc(projectId)}/chat-images/${enc(imageId)}`, mediaType);
  }
  cancel(runId: string): Promise<{ ok: true }> { return this.json("POST", `/runs/${enc(runId)}/cancel`, okSchema); }
  /** P10 E24 (core C1): one chat per build. The message goes to the build's lead (queued behind
   *  its current step while it works); with no lead yet the core starts one on the default model
   *  (Claude Code when signed in). null: this core has no such route (an older core). */
  /** P11 F7 / F8: `images` (at most 5, already shrunk by the composer) go with the message as
   *  real image blocks; `effort`, the Think choice, rides along when it is not Auto (an older core
   *  never sees either). */
  async chat(projectId: string, text: string, opts: ChatOptions = {}): Promise<CreateRunResult | null> {
    const { images, effort } = opts;
    const res = await this.request("POST", `/projects/${enc(projectId)}/chat`, { body: {
      text, ...(images?.length ? { images } : {}), ...(effort && effort !== "auto" ? { effort } : {}),
    } });
    if (await routeMissing(res)) return null;
    if (!res.ok) throw await toError(res);
    return createRunResultSchema.parse(await res.json());
  }
  /** Plan 11 F8: the build's default Think choice. null: this core has no such route. */
  async effortDefault(projectId: string): Promise<EffortDefault | null> {
    const res = await this.request("GET", `/projects/${enc(projectId)}/effort`, {});
    if (await routeMissing(res)) return null;
    if (!res.ok) throw await toError(res);
    return effortDefaultSchema.parse(await res.json());
  }
  setEffortDefault(projectId: string, choice: ThinkChoice): Promise<EffortDefault> {
    return this.json("PUT", `/projects/${enc(projectId)}/effort`, effortDefaultSchema, { body: { default: choice } });
  }
  /** P10 E9 (core C1): take back a message still waiting for a busy lead (it is dropped with the
   *  reason "cancelled"). false: this core can't cancel a queued message (no such route). */
  async cancelQueued(runId: string, messageId: string): Promise<boolean> {
    const res = await this.request("POST", `/runs/${enc(runId)}/queued/${enc(messageId)}/cancel`, {});
    if (await routeMissing(res)) return false;
    if (!res.ok) throw await toError(res);
    return true;
  }
  /** Carry a stopped run on: the core sends it an internal continuation ("AgentCAD continued after:
   *  <cause>" in the thread), never your message again and never as your words. */
  async continueRun(runId: string, cause?: string | null): Promise<void> {
    const res = await this.request("POST", `/runs/${enc(runId)}/continue`, { body: cause ? { cause: cause.slice(0, 2000) } : {} });
    if (!res.ok) throw await toError(res);
  }
  cancelAll(): Promise<CancelAllResult> { return this.json("POST", "/runs/cancel_all", cancelAllSchema); }

  // ---- Plan 5 (C1): agent management — the run tree, rename/pin/archive, delete, cleanup
  /** The orchestrator → worker forest (roots are runs with no parent). */
  runsTree(projectId?: string | null): Promise<RunNode[]> {
    return this.json("GET", "/runs/tree", z.array(runNodeSchema), { query: { project_id: projectId } });
  }
  /** Rename, pin or archive a run. */
  patchRun(id: string, patch: RunPatch): Promise<AgentRun> {
    return this.json("PATCH", `/runs/${enc(id)}`, agentRunSchema, { body: patch });
  }
  /** Delete a run and its steps/workdir. Returns null on success, or the refusal (409) when the
   *  run is still running (with blocking worker ids) or awaiting your approval. */
  async deleteRun(id: string): Promise<DeleteRefused | null> {
    const res = await this.request("DELETE", `/runs/${enc(id)}`, {});
    if (res.ok) return null;
    if (res.status === 409) {
      let body: unknown = null;
      try { body = await res.json(); } catch { /* fall through to the generic error */ }
      const refused = deleteRefusedSchema.safeParse(body);
      if (refused.success) return refused.data;
    }
    throw await toError(res);
  }
  /** Dry-run (default) or really remove finished runs; returns the ids (that would be) removed. */
  cleanupRuns(body: CleanupBody = {}): Promise<CleanupResult> {
    return this.json("POST", "/runs/cleanup", cleanupResultSchema, { body: { dry_run: true, ...body } });
  }

  /** Build names for grouping runs (GET /projects, read leniently: only id and name are used, so
   *  this stays independent of the full builds client). Trashed builds are included. */
  async projectNames(): Promise<Map<string, string>> {
    const rows = await this.json("GET", "/projects", z.array(z.object({ id: z.string(), name: z.string() })));
    return new Map(rows.map((p) => [p.id, p.name]));
  }

  // ---- custom agent definitions (built-ins can only be hidden, via the profile)
  /** One agent with its instructions (body null for a built-in), for the edit form. */
  getAgent(name: string): Promise<AgentDetail> {
    return this.json("GET", `/agents/${enc(name)}`, agentDetailSchema);
  }
  createAgent(body: AgentDefBody): Promise<AgentInfo> { return this.json("POST", "/agents", agentInfoSchema, { body }); }
  updateAgent(name: string, body: AgentDefBody): Promise<AgentInfo> {
    return this.json("PUT", `/agents/${enc(name)}`, agentInfoSchema, { body });
  }
  async deleteAgent(name: string): Promise<void> { await this.json("DELETE", `/agents/${enc(name)}`, okSchema); }

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
  /** Every tool the core registers (GET /tools: [{name, description, safety, …}]; a bare name
   *  list is read too), for the custom-agent tools picker (C-7). null from a core without the
   *  route (404): the picker then falls back to the tools the agents list. */
  async tools(): Promise<ToolInfo[] | null> {
    try {
      return await this.json("GET", "/tools", toolListSchema);
    } catch (e) {
      if (e instanceof CoreError && (e.status === 404 || e.status === 405)) return null;
      throw e;
    }
  }
  models(): Promise<ModelsInfo> { return this.json("GET", "/models", modelsInfoSchema); }
  /** The part tree of a build (without one: the core's active build). */
  parts(projectId?: string | null): Promise<PartSummary[]> {
    return this.json("GET", "/parts", z.array(partSummarySchema), { query: { project_id: projectId ?? undefined } });
  }

  partGlb(partId: string, etag?: string): Promise<{ bytes: ArrayBuffer; etag: string } | "not-modified"> {
    return this.glb(`/parts/${enc(partId)}/model.glb`, etag);
  }

  /** A part's shaded thumbnail as a PNG Blob (rendered in the sandboxed worker, cached by
   *  version). CoreError not_found until the part has a built mesh. */
  partThumb(partId: string, size = 256): Promise<Blob> {
    return this.blob(`/parts/${enc(partId)}/thumb.png`, "image/png", { size });
  }

  /** A build's composite tile (up to four part previews) as a PNG Blob. CoreError not_found
   *  until at least one part has a built mesh. */
  /** The build's layout skeleton (G3; core skeleton_tools.skeleton_view): what skeleton_get
   *  answers, read by viewer/skeleton.ts parseSkeleton (loose: an older core lacks the route). */
  projectSkeleton(projectId: string): Promise<unknown> { return this.json("GET", `/projects/${enc(projectId)}/skeleton`, anyBody); }
  /** The build's Home cover (D24): one 16:10 render of its assembly, transparent background;
   *  `size` is its width. */
  projectThumb(projectId: string, size = 640): Promise<Blob> {
    return this.blob(`/projects/${enc(projectId)}/thumb.png`, "image/png", { size });
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

  // ---- every study type (Plan 5 C7: core sim/studies.py, server/sim_api.py; Task A8)

  studyTypes(): Promise<StudyTypeInfo[]> { return this.json("GET", "/sims/types", z.array(studyTypeInfoSchema)); }
  /** Plan 9 U-D: the studies agent's build context and proposed next studies. */
  studiesContext(projectId: string): Promise<StudiesContext> {
    return this.json("GET", "/studies/context", studiesContextSchema, { query: { project_id: projectId } });
  }
  // ---- Plan 13 H4: whole-assembly studies (walk / launch / drop / run mechanism)
  async assemblyStudyKinds(projectId: string): Promise<AssemblyKind[]> {
    return (await this.json("GET", "/assembly_studies/kinds", assemblyKindsSchema, { query: { project_id: projectId } })).kinds;
  }
  listAssemblyStudies(projectId: string): Promise<AssemblyStudySummary[]> {
    return this.json("GET", "/assembly_studies", z.array(assemblyStudySummarySchema), { query: { project_id: projectId } });
  }
  getAssemblyStudy(studyId: string): Promise<AssemblyStudyRecord> { return this.json("GET", `/assembly_studies/${enc(studyId)}`, assemblyStudyRecordSchema); }
  /** 202: a "studies" run drives assembly_study through the Gate. Bad settings: 400 before any run. */
  startAssemblyStudy(body: { project_id: string; kind: AssemblyStudyKind; settings: Record<string, unknown> }): Promise<{ run_id: string; status: string }> {
    return this.json("POST", "/assembly_studies", assemblyStartedSchema, { body });
  }
  /** Check part stresses: the study's peak loads to per-part FEM (a "studies" run). */
  assemblyStudyFem(studyId: string): Promise<{ run_id: string; status: string }> {
    return this.json("POST", `/assembly_studies/${enc(studyId)}/fem`, assemblyStartedSchema, { body: {} });
  }
  studySolvers(): Promise<StudySolvers> { return this.json("GET", "/sims/solvers", studySolversSchema); }
  /** `projectId`: that build's assembly studies (the core lists a part's by part_id). */
  listStudies(filter: { partId?: string; runId?: string; projectId?: string } = {}): Promise<StudySummary[]> {
    return this.json("GET", "/sims", z.array(studySummarySchema), { query: { part_id: filter.partId, run_id: filter.runId, project_id: filter.projectId } });
  }
  getStudy(simId: string): Promise<StudyRecord> { return this.json("GET", `/sims/${enc(simId)}`, studyRecordSchema); }
  /** 202: a "studies" run drives sim_run through the Gate. Bad settings: 400 before any run. */
  startStudy(body: StudyStartBody): Promise<SimStarted> { return this.json("POST", "/sims", simStartedSchema, { body }); }
  cancelStudy(simId: string): Promise<{ sim_id: string; status: "cancelling" }> {
    return this.json("POST", `/sims/${enc(simId)}/cancel`, studyCancelSchema);
  }
  /** Plan 9 D4: an agent's running background jobs (studies, reviews, design studies). */
  listJobs(projectId?: string | null): Promise<BackgroundJob[]> {
    return this.json("GET", "/jobs", z.array(backgroundJobSchema), { query: { project_id: projectId ?? undefined } });
  }
  /** Stop one background job; its agent gets no turn for it (the note rides along later). */
  cancelJob(jobId: string): Promise<JobCancel> { return this.json("POST", `/jobs/${enc(jobId)}/cancel`, jobCancelSchema); }
  studyCharts(simId: string): Promise<StudyCharts> { return this.json("GET", `/sims/${enc(simId)}/charts`, studyChartsSchema); }
  studyProbe(simId: string, p: readonly [number, number, number]): Promise<StudyProbe> {
    return this.json("GET", `/sims/${enc(simId)}/probe`, studyProbeSchema, { query: { x: p[0], y: p[1], z: p[2] } });
  }
  studySection(simId: string, q: { axis: "x" | "y" | "z"; offset: number; channel: string; n?: number }): Promise<StudySection> {
    return this.json("GET", `/sims/${enc(simId)}/section`, studySectionSchema, { query: { axis: q.axis, offset: q.offset, channel: q.channel, n: q.n } });
  }
  /** A wind study's velocity slice image (slice_xz | slice_xy). */
  studySlice(simId: string, name: "slice_xz" | "slice_xy"): Promise<Blob> { return this.blob(`/sims/${enc(simId)}/slices/${name}.png`, "image/png"); }
  /** A render_views image (Plan 8 G1: critique-round before/after, a presented view's thumbnail). */
  renderImage(imageId: string): Promise<Blob> { return this.blob(`/renders/${enc(imageId)}.png`, "image/png"); }
  // ---- Plan 9 G-A: the concept stage (core concept/api.py)
  concept(projectId: string): Promise<ConceptView> { return this.json("GET", `/projects/${enc(projectId)}/concept`, conceptViewSchema); }
  conceptSketch(sketchId: string): Promise<ConceptSketch> { return this.json("GET", `/concept/sketches/${enc(sketchId)}`, conceptSketchSchema); }
  sketchFeedback(sketchId: string, body: SketchFeedbackBody): Promise<FeedbackSent> {
    return this.json("POST", `/concept/sketches/${enc(sketchId)}/feedback`, feedbackSentSchema, { body });
  }
  approveConcept(projectId: string, sketchId: string | null): Promise<ConceptDecision> {
    return this.json("POST", `/projects/${enc(projectId)}/concept/approve`, conceptDecisionSchema, { body: sketchId ? { sketch_id: sketchId } : {} });
  }
  skipConcept(projectId: string): Promise<ConceptDecision> { return this.json("POST", `/projects/${enc(projectId)}/concept/skip`, conceptDecisionSchema); }
  choosePitch(pitchId: string, body: ChooseBody): Promise<PitchChosen> {
    return this.json("POST", `/concept/pitches/${enc(pitchId)}/choose`, pitchChosenSchema, { body });
  }
  compareStudies(ids: readonly string[]): Promise<StudyCompare> { return this.json("POST", "/sims/compare", studyCompareSchema, { body: { ids } }); }
  studyTemplates(): Promise<StudyTemplate[]> { return this.json("GET", "/sims/templates", z.array(studyTemplateSchema)); }
  addStudyTemplate(body: { name: string; type: StudyType; settings: Record<string, unknown> }): Promise<StudyTemplate> {
    return this.json("POST", "/sims/templates", studyTemplateSchema, { body });
  }
  deleteStudyTemplate(id: string): Promise<unknown> { return this.json("DELETE", `/sims/templates/${enc(id)}`, anyBody); }
  studyMaterials(): Promise<StudyMaterials> { return this.json("GET", "/sims/materials", studyMaterialsSchema); }
  addStudyMaterial(body: CustomMaterialBody): Promise<StudyMaterial> { return this.json("POST", "/sims/materials", studyMaterialSchema, { body }); }
  deleteStudyMaterial(name: string): Promise<unknown> { return this.json("DELETE", `/sims/materials/${enc(name)}`, anyBody); }

  // ---- material calibration (Plan 6 S3: core server/sim_api.py, sim/calibration.py)

  calibrations(): Promise<Calibration[]> { return this.json("GET", "/sims/calibrations", z.array(calibrationSchema)); }
  /** A preview: fits the tests, saves nothing. */
  fitCalibration(body: CalibrationBody): Promise<CalibrationPreview> {
    return this.json("POST", "/sims/calibrations/fit", calibrationPreviewSchema, { body });
  }
  /** Saves (replaces) the record of this material and printer: from then on every study of that
   *  material on that printer uses it. Only after the user confirms. */
  saveCalibration(body: CalibrationBody): Promise<Calibration> { return this.json("POST", "/sims/calibrations", calibrationSchema, { body }); }
  deleteCalibration(id: string): Promise<unknown> { return this.json("DELETE", `/sims/calibrations/${enc(id)}`, anyBody); }

  // ---- Plan 9 D14: generative design / topology optimization (core server/topopt_api.py)

  /** 202: topopt_run (or topopt_generate with variants / sweep) through the Gate; the run goes on
   *  in the background (topopt_progress / topopt_finished events, or poll topoptGet). */
  topoptStart(body: TopoptStartBody): Promise<TopoptRecord> { return this.json("POST", "/topopt", topoptRecordSchema, { body }); }
  topoptList(filter: { partId?: string; projectId?: string } = {}): Promise<TopoptRecord[]> {
    return this.json("GET", "/topopt", z.array(topoptRecordSchema), { query: { part_id: filter.partId, project_id: filter.projectId } });
  }
  topoptGet(id: string): Promise<TopoptRecord> { return this.json("GET", `/topopt/${enc(id)}`, topoptRecordSchema); }
  topoptCancel(id: string): Promise<unknown> { return this.json("POST", `/topopt/${enc(id)}/cancel`, anyBody); }
  topoptAccept(id: string, body: TopoptAcceptBody): Promise<TopoptAcceptResult> {
    return this.json("POST", `/topopt/${enc(id)}/accept`, topoptAcceptResultSchema, { body });
  }
  /** A variant's live density preview / result / stress GLB (same frame as a part's model.glb). */
  topoptGlb(id: string, variant: string, name: "preview" | "result" | "stress", etag?: string): Promise<{ bytes: ArrayBuffer; etag: string } | "not-modified"> {
    return this.glb(`/topopt/${enc(id)}/variants/${enc(variant)}/${name}.glb`, etag);
  }
  /** One kept iteration of a variant's density (a path from its urls.evolution). */
  topoptEvoGlb(path: string, etag?: string): Promise<{ bytes: ArrayBuffer; etag: string } | "not-modified"> {
    if (!/^\/topopt\/[\w-]+\/variants\/[\w-]+\/evo_\d{3}\.glb$/.test(path)) return Promise.reject(new Error(`not an evolution frame: ${path}`));
    return this.glb(path, etag);
  }
  /** A variant's iso render (PNG), for the variant grid. */
  topoptRender(id: string, variant: string): Promise<Blob> { return this.blob(`/topopt/${enc(id)}/variants/${enc(variant)}/render.png`, "image/png"); }

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

  // ---- Plan 5 A6: Onshape browser, live budget, parts directory (server/onshape_api.py, catalog_api.py)

  /** One page (≤ 20), newest change first; a shorter page is the last. One Onshape call. */
  onshapeDocuments(opts: { q?: string; offset?: number; limit?: number } = {}): Promise<OnshapeDoc[]> {
    return this.json("GET", "/onshape/documents", z.array(onshapeDocSchema), { query: { q: opts.q || undefined, owner: "me", offset: opts.offset, limit: opts.limit } });
  }
  /** Part Studios and Assemblies of a workspace. One Onshape call. */
  onshapeElements(did: string, wid: string): Promise<OnshapeElement[]> {
    return this.json("GET", `/onshape/documents/${enc(did)}/elements`, z.array(onshapeElementSchema), { query: { wid } });
  }
  /** A document thumbnail through the core's proxy (cached there; the first fetch costs one call).
   *  Only the core's own `/onshape/thumbnail?href=` paths from onshapeDocuments() are fetched. */
  async onshapeThumbnail(thumbnailUrl: string): Promise<Blob> {
    const m = /^\/onshape\/thumbnail\?href=([^&#]+)$/.exec(thumbnailUrl);
    if (!m) throw new CoreError("invalid_input", "not a core thumbnail path");
    const res = await this.request("GET", "/onshape/thumbnail", { query: { href: decodeURIComponent(m[1]) } });
    if (!res.ok) throw await toError(res);
    const type = (res.headers.get("content-type") ?? "").split(";")[0].trim();
    if (!/^image\/(png|jpeg|gif|webp)$/.test(type)) throw new CoreError("validation", "thumbnail is not an image");
    return new Blob([await res.arrayBuffer()], { type });
  }
  /** Mirrors a Part Studio or Assembly into a build as a bounding-box envelope (two Onshape calls). */
  onshapeImport(body: { did: string; wid: string; eid: string; project_id: string | null }): Promise<PartResult> {
    return this.json("POST", "/onshape/import", partResultSchema, { body });
  }
  onshapeBudget(runId?: string | null): Promise<OnshapeBudget> {
    return this.json("GET", "/onshape/budget", onshapeBudgetSchema, { query: { run_id: runId ?? undefined } });
  }

  catalogCategories(): Promise<string[]> { return this.json("GET", "/catalog/categories", z.array(z.string())); }
  catalogSearch(opts: { q?: string; category?: string | null; limit?: number } = {}): Promise<CatalogItem[]> {
    return this.json("GET", "/catalog/search", z.array(catalogItemSchema), { query: { q: opts.q || undefined, category: opts.category || undefined, limit: opts.limit } });
  }
  catalogItem(id: string): Promise<CatalogItem> { return this.json("GET", `/catalog/items/${enc(id)}`, catalogItemSchema); }
  /** Fetches the page in the core and SAVES the item at once (review edits go through
   *  catalogEdit, Discard through catalogDelete). A URL already in the directory comes back
   *  unchanged with `existing: true` unless `refresh` (only the app may ask for that). */
  catalogImport(body: { url: string; project_id?: string | null; refresh?: boolean }): Promise<CatalogItem> {
    return this.json("POST", "/catalog/import", catalogItemSchema, { body });
  }
  catalogEdit(id: string, patch: CatalogEdit): Promise<CatalogItem> {
    return this.json("PATCH", `/catalog/items/${enc(id)}`, catalogItemSchema, { body: patch });
  }
  /** URL items only (built-ins are refused). */
  async catalogDelete(id: string): Promise<void> { await this.json("DELETE", `/catalog/items/${enc(id)}`, okSchema); }
  catalogInsert(id: string, projectId: string | null): Promise<PartResult> {
    return this.json("POST", `/catalog/items/${enc(id)}/insert`, partResultSchema, { body: projectId ? { project_id: projectId } : {} });
  }
  /** Domains an agent may import from without an approval card (profile catalog_allowed_domains). */
  allowedDomains(): Promise<string[]> { return this.json("GET", "/catalog/allowed_domains", z.array(z.string())); }
  allowDomain(domain: string): Promise<string[]> { return this.json("POST", "/catalog/allowed_domains", z.array(z.string()), { body: { domain } }); }
  disallowDomain(domain: string): Promise<string[]> { return this.json("DELETE", `/catalog/allowed_domains/${enc(domain)}`, z.array(z.string())); }

  /** Live builds for the pickers (GET /projects without the trashed ones) and the active build. */
  async buildChoices(): Promise<{ builds: BuildRef[]; active: string | null }> {
    const [all, prof] = await Promise.all([
      this.json("GET", "/projects", z.array(buildRefSchema)),
      this.json("GET", "/profile", activeBuildSchema).catch(() => ({ active_project_id: null })),
    ]);
    return { builds: all.filter((b) => b.trashed_at === null), active: prof.active_project_id };
  }

  // ---- Plan 5 A4: assemblies (core server/assembly_api.py); every mutation emits assembly_changed

  /** Live builds (Trash left out); the assembly uses it to find the active build. */
  async projects(): Promise<ProjectRow[]> {
    return (await this.json("GET", "/projects", z.array(projectRowSchema))).filter((p) => p.trashed_at === null);
  }
  assemblyParts(projectId: string): Promise<AssemblyPart[]> {
    return this.json("GET", "/parts", z.array(assemblyPartSchema), { query: { project_id: projectId } });
  }
  assembly(projectId: string): Promise<Assembly> { return this.json("GET", asm(projectId), assemblySchema); }
  addInstance(projectId: string, body: NewInstanceBody): Promise<Instance> {
    return this.json("POST", `${asm(projectId)}/instances`, instanceSchema, { body });
  }
  patchInstance(projectId: string, iid: string, patch: InstancePatch): Promise<Instance> {
    return this.json("PATCH", `${asm(projectId)}/instances/${enc(iid)}`, instanceSchema, { body: patch });
  }
  /** Also removes every joint on the instance; returns their ids. */
  async deleteInstance(projectId: string, iid: string): Promise<string[]> {
    return (await this.json("DELETE", `${asm(projectId)}/instances/${enc(iid)}`, instanceDeletedSchema)).removed_joints;
  }
  /** 400 invalid_input when the joint would close a loop (nothing is written). */
  addJoint(projectId: string, body: NewJointBody): Promise<Joint> {
    return this.json("POST", `${asm(projectId)}/joints`, jointSchema, { body });
  }
  patchJoint(projectId: string, jid: string, patch: JointPatch): Promise<Joint> {
    return this.json("PATCH", `${asm(projectId)}/joints/${enc(jid)}`, jointSchema, { body: patch });
  }
  async deleteJoint(projectId: string, jid: string): Promise<void> {
    await this.json("DELETE", `${asm(projectId)}/joints/${enc(jid)}`, jointDeletedSchema);
  }
  /** Stores the joint value (clamped to its limits) and returns every solved transform. */
  drive(projectId: string, jointId: string, value: JointValue): Promise<DriveResult> {
    return this.json("POST", `${asm(projectId)}/drive`, driveResultSchema, { body: { joint_id: jointId, value } });
  }
  /** OCCT common volume per instance pair over 0.01 mm³ (sandboxed worker; can take seconds). */
  /** Plan 9 G-D: the build's saved animations, oldest first. Untrusted: callers parse the steps
   *  (state/assemblyAnimation parseAnimateSteps). */
  assemblyAnimations(projectId: string): Promise<{ id: string; name: string; steps: unknown; loops: number }[]> {
    return this.json("GET", `${asm(projectId)}/animations`, z.array(z.object({
      id: z.string(), name: z.string(), steps: z.unknown(), loops: z.number().catch(1),
    })));
  }

  interference(projectId: string): Promise<Interference[]> {
    return this.json("GET", `${asm(projectId)}/interference`, z.array(interferenceSchema));
  }

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
  listDesignStudies(projectId: string): Promise<Study[]> { return this.json("GET", `/projects/${enc(projectId)}/studies`, z.array(studySchema)); }
  /** "Use this variant": study_apply through the Gate. 409 stale_version when the part moved on. */
  applyStudy(projectId: string, studyId: string, body: StudyApplyBody): Promise<StudyApplied> {
    return this.json("POST", `/projects/${enc(projectId)}/studies/${enc(studyId)}/apply`, studyAppliedSchema, { body });
  }

  // ---- Plan 5 A3: builds, the part tree, live part edits (server/projects_api.py, edit_api.py)

  /** Every build, the trashed ones included (trashed_at set). */
  listBuilds(): Promise<Build[]> { return this.json("GET", "/projects", z.array(buildSchema)); }
  createBuild(name: string): Promise<Build> { return this.json("POST", "/projects", buildSchema, { body: { name } }); }
  renameBuild(id: string, name: string): Promise<Build> { return this.json("PATCH", `/projects/${enc(id)}`, buildSchema, { body: { name } }); }
  /** Plan 9 G-B: the user's design class for a build ("auto" hands it back to the classifier).
   *  Answers the build view plus `design_class` ({cls, source: "user", ...} or null). */
  setDesignClass(id: string, cls: "engineering" | "mixed" | "design" | "auto"): Promise<unknown> {
    return this.json("PATCH", `/projects/${enc(id)}`, anyBody, { body: { design_class: cls } });
  }
  /** Moves the build to the Trash (nothing is deleted until emptyTrash). */
  trashBuild(id: string): Promise<Build> { return this.json("DELETE", `/projects/${enc(id)}`, buildSchema); }
  restoreBuild(id: string): Promise<Build> { return this.json("POST", `/projects/${enc(id)}/restore`, buildSchema); }
  /** Purges the Trash. A 409 {reason, run_ids, …} (a trashed build still has live work; nothing
   *  was deleted) comes back as `{ refused }`; any other failure throws. */
  async emptyTrash(): Promise<EmptyTrashResult | { refused: EmptyTrashRefused }> {
    const res = await this.request("POST", "/projects/trash/empty", {});
    if (res.status === 409) {
      let body: unknown = null;
      try { body = await res.clone().json(); } catch { /* fall through to the generic error */ }
      const r = emptyTrashRefusedSchema.safeParse(body);
      if (r.success) return { refused: r.data };
    }
    if (!res.ok) throw await toError(res);
    let data: unknown;
    try { data = await res.json(); } catch { throw new CoreError("validation", "POST /projects/trash/empty: response is not JSON", { status: res.status }); }
    const parsed = emptyTrashSchema.safeParse(data);
    if (!parsed.success) throw new CoreError("validation", "POST /projects/trash/empty: unexpected response shape", { status: res.status });
    return parsed.data;
  }
  /** Deletes one trashed build for good (D30 "Delete forever"; core POST /projects/{id}/purge).
   *  A 409 (it still has live work; nothing was deleted) comes back as `{ refused }`. */
  async purgeBuild(id: string): Promise<EmptyTrashResult | { refused: EmptyTrashRefused }> {
    const path = `/projects/${enc(id)}/purge`;
    const res = await this.request("POST", path, {});
    if (res.status === 409) {
      let body: unknown = null;
      try { body = await res.clone().json(); } catch { /* fall through to the generic error */ }
      const r = emptyTrashRefusedSchema.safeParse(body);
      if (r.success) return { refused: r.data };
    }
    if (!res.ok) throw await toError(res);
    let data: unknown;
    try { data = await res.json(); } catch { throw new CoreError("validation", `POST ${path}: response is not JSON`, { status: res.status }); }
    const parsed = emptyTrashSchema.safeParse(data);
    if (!parsed.success) throw new CoreError("validation", `POST ${path}: unexpected response shape`, { status: res.status });
    return parsed.data;
  }
  buildTabs(): Promise<BuildTabsProfile> { return this.json("GET", "/profile", buildTabsProfileSchema); }
  /** The active build (a profile field; PUT /profile merges, nothing else changes). Plan 9: the
   *  old open_project_ids is read (buildTabs) but never written — there are no build tabs. */
  setActiveBuild(id: string | null): Promise<BuildTabsProfile> {
    return this.json("PUT", "/profile", buildTabsProfileSchema, { body: { active_project_id: id } });
  }

  /** PATCH semantics: a key left out is unchanged; `color: null` / `parent_id: null` clear it.
   *  A move that would form a cycle is refused (400). */
  patchPart(id: string, patch: { label?: string; color?: string | null; parent_id?: string | null; appearance?: Appearance | null }): Promise<PartSummary> {
    return this.json("PATCH", `/parts/${enc(id)}`, partSummarySchema, { body: patch });
  }
  // ---- Plan 7 U8: the parts manager's menu
  /** A copy named "<label> copy", right after the original. */
  duplicatePart(id: string): Promise<PartSummary> {
    return this.json("POST", `/parts/${enc(id)}/duplicate`, partSummarySchema);
  }
  /** Moves the part's files to the core's trash; refused (400) while the assembly uses it, unless
   *  `cascade` (Plan 9 D29): then its instances leave the assembly too. `undo` restores it all. */
  deletePart(id: string, opts: { cascade?: boolean } = {}): Promise<{ deleted: string; removed_instances: string[]; undo: string | null }> {
    return this.json("DELETE", `/parts/${enc(id)}${opts.cascade ? "?cascade=true" : ""}`, z.object({
      deleted: z.string(), removed_instances: z.array(z.string()).default([]), undo: z.string().nullish().transform((v) => v ?? null),
    }));
  }
  /** Plan 9 D29: the tree's "Remove from assembly": every instance of these parts (and the joints
   *  that come loose) leaves the build's assembly; `undo` goes back to restoreAssembly as is. */
  removePartsFromAssembly(projectId: string, partIds: string[]): Promise<{ removed_instances: string[]; removed_joints: string[]; undo: AssemblyUndo }> {
    return this.json("POST", `/projects/${enc(projectId)}/assembly/remove_parts`, z.object({
      removed_instances: z.array(z.string()), removed_joints: z.array(z.string()), undo: assemblyUndoSchema,
    }), { body: { part_ids: partIds } });
  }
  restoreAssembly(projectId: string, undo: AssemblyUndo): Promise<{ restored_instances: number; restored_joints: number }> {
    return this.json("POST", `/projects/${enc(projectId)}/assembly/restore`,
      z.object({ restored_instances: z.number(), restored_joints: z.number() }), { body: undo });
  }
  /** Plan 9 D29: undo a delete (the files, the tree row and the assembly instances). */
  restorePart(id: string, undo: string): Promise<PartSummary> {
    return this.json("POST", `/parts/${enc(id)}/restore`, partSummarySchema, { body: { undo } });
  }
  /** Saves model.step / model.stl to the export folder (~/Downloads) as "<label>.<fmt>". */
  exportPart(id: string, format: "step" | "stl"): Promise<{ path: string; name: string; format: string }> {
    return this.json("POST", `/parts/${enc(id)}/export`, z.object({ path: z.string(), name: z.string(), format: z.string() }), { body: { format } });
  }
  /** The listed siblings take order 0..n-1 under `parentId` (re-parenting them there). */
  reorderParts(projectId: string, parentId: string | null, ids: string[]): Promise<PartSummary[]> {
    return this.json("POST", "/parts/reorder", z.array(partSummarySchema), { body: { project_id: projectId, parent_id: parentId, ids } });
  }

  /** The script (data, never instructions), its var schema and version. Read it before a PUT. */
  partSource(id: string): Promise<PartSource> { return this.json("GET", `/parts/${enc(id)}/source`, partSourceSchema); }
  /** The user's var edit: Gate (ui run) → rebuild. 409 (stale_version, body.current_version) when
   *  `baseVersion` is no longer current; 422 when the build fails. */
  setPartVars(id: string, vars: Record<string, number>, baseVersion: number): Promise<PartEditResult> {
    return this.json("POST", `/parts/${enc(id)}/vars`, partEditResultSchema, { body: { vars, base_version: baseVersion } });
  }
  putPartSource(id: string, source: string, baseVersion: number): Promise<PartEditResult> {
    return this.json("PUT", `/parts/${enc(id)}/source`, partEditResultSchema, { body: { source, base_version: baseVersion } });
  }
  partOp(id: string, op: OpRequest, baseVersion: number): Promise<PartEditResult> {
    return this.json("POST", `/parts/${enc(id)}/ops`, partEditResultSchema, { body: { ...op, base_version: baseVersion } });
  }

  // ---- drawing import (core imports_api.py): a drawing file → a part, with a dimension match table
  importDrawing(file: Blob, name: string, opts: { projectId?: string | null } = {}): Promise<DrawingImport> {
    const form = new FormData();
    form.append("file", file, name);
    if (opts.projectId) form.append("project_id", opts.projectId);
    return this.json("POST", "/imports/drawing", drawingImportSchema, { body: form });
  }
  drawingImport(id: string): Promise<DrawingImport> { return this.json("GET", `/imports/${enc(id)}`, drawingImportSchema); }
  /** The original drawing as a picture (page 1, or an SVG of a DXF): shown via a blob: URL in an <img>. */
  drawingImportOriginal(id: string, type: string): Promise<Blob> { return this.blob(`/imports/${enc(id)}/original`, type); }
  // ---- Plan 7 U2a: measure and feedback pins (server/measure_api.py, server/feedback_api.py)

  /** Exact B-rep values for the picks; 409 (stale_version) when the part changed since `baseVersion`. */
  measurePart(id: string, refs: MeasureRef[], baseVersion?: number): Promise<MeasureResult> {
    return this.json("POST", `/parts/${enc(id)}/measure`, measureResultSchema, { body: { refs, ...(baseVersion === undefined ? {} : { base_version: baseVersion }) } });
  }
  /** Measure mode opened on a part: warm the core's measure worker (best effort). */
  warmMeasure(id: string): Promise<{ part_id: string; warm: boolean }> {
    return this.json("POST", `/parts/${enc(id)}/measure`, measureWarmSchema, { body: { warm: true } });
  }
  /** A comment pinned on the model: a message to the build's agent with the location as data. */
  sendFeedback(projectId: string, body: FeedbackBody): Promise<FeedbackResult> {
    return this.json("POST", `/projects/${enc(projectId)}/feedback`, feedbackResultSchema, { body });
  }

  // ---- Plan 7 (R3/R4/R5): agent browser, part cards, the build parts list (BOM); core U4c
  //      (server/browser_api.py). Everything these return is untrusted page/agent data.

  /** The pages this run read (web_open and web_search pages), oldest first, as whole snapshots
   *  (`?full=1`: text, tables, results, proxied images). */
  runBrowser(runId: string): Promise<BrowserSnapshot[]> {
    return this.json("GET", `/runs/${enc(runId)}/browser`, browserListSchema, { query: { full: 1 } });
  }
  /** One stored snapshot. */
  browserSnapshot(sid: string): Promise<BrowserSnapshot> {
    return this.json("GET", `/browser/snapshots/${enc(sid)}`, browserSnapshotSchema);
  }
  /** A part card an agent showed (`part_show`'s result carries only its id). */
  partCard(cardId: string): Promise<PartCard> {
    return this.json("GET", `/browser/cards/${enc(cardId)}`, partCardSchema);
  }
  /** A snapshot or part-card image through the core's proxy. Only a core-relative path from a
   *  parsed snapshot/card is fetched, never a page's own image host (R3 safety). */
  async snapshotImage(proxyUrl: string): Promise<Blob> {
    if (!isProxyPath(proxyUrl)) throw new CoreError("invalid_input", "not a core proxy path");
    const res = await this.request("GET", proxyUrl, {});
    if (!res.ok) throw await toError(res);
    const type = (res.headers.get("content-type") ?? "").split(";")[0].trim();
    if (!/^image\/(png|jpeg|gif|webp)$/.test(type)) throw new CoreError("validation", "proxied file is not an image");
    return new Blob([await res.arrayBuffer()], { type });
  }
  /** Mises's own suggested sites (what "Restore defaults" restores). */
  defaultSites(): Promise<SuggestedSite[]> { return this.json("GET", "/browser/sites/defaults", suggestedSitesSchema); }
  /** The profile's suggested sites become Mises's current defaults again; returns the new list. */
  restoreDefaultSites(): Promise<SuggestedSite[]> {
    return this.json("POST", "/browser/sites/restore_defaults", suggestedSitesSchema);
  }

  /** The build's parts list: made rows first, then bought, plus per-currency totals. */
  bom(projectId: string): Promise<Bom> { return this.json("GET", bomPath(projectId), bomSchema); }
  /** Add a row (source "user"). Only the fields the core accepts are sent. */
  bomAdd(projectId: string, row: NewBomRow): Promise<BomRow> {
    return this.json("POST", bomPath(projectId), bomRowSchema, { body: bomBody(row, "new") });
  }
  /** Edit a row (an automatic row is stored with the edit). Only the fields the core accepts. */
  bomPatch(projectId: string, itemId: string, patch: BomPatch): Promise<BomRow> {
    return this.json("PATCH", `${bomPath(projectId)}/${enc(itemId)}`, bomRowSchema, { body: bomBody(patch, "patch") });
  }
  /** Remove a row (a part still in the build is hidden, not deleted). */
  async bomDelete(projectId: string, itemId: string): Promise<void> {
    await this.json("DELETE", `${bomPath(projectId)}/${enc(itemId)}`, okSchema);
  }
  /** The parts list as CSV (formulas neutralized by the core); saved by the app. */
  bomCsv(projectId: string): Promise<Blob> { return this.blob(`/projects/${enc(projectId)}/bom.csv`, "text/csv"); }

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
    if (opts.body instanceof FormData) {
      init.body = opts.body;            // multipart: fetch sets the boundary header itself
    } else if (opts.body !== undefined) {
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

/** A route this core doesn't have: 404 / 405 from the framework itself ({"detail": …}), not one
 *  of the core's own error answers ({error, message}). Reads a clone, so `res` stays readable. */
async function routeMissing(res: Response): Promise<boolean> {
  if (res.status !== 404 && res.status !== 405) return false;
  let body: unknown = null;
  try { body = await res.clone().json(); } catch { /* not JSON */ }
  return !(typeof body === "object" && body !== null && "error" in body);
}

export async function toError(res: Response): Promise<CoreError> {
  let body: unknown = null;
  try { body = await res.json(); } catch { /* not JSON */ }
  const message = typeof (body as { message?: unknown })?.message === "string"
    ? (body as { message: string }).message : `HTTP ${res.status}`;
  if (res.status === 401) return new CoreError("unauthorized", message, { status: 401 });
  const parsed = coreErrorBodySchema.safeParse(body);
  // a part edit whose sandboxed rebuild failed (C6 edit_api): {error: "build_failed", message:
  // the worker's report, ending in the op / error line}; the part is unchanged
  if (res.status === 422 && (body as { error?: unknown })?.error === "build_failed") {
    return new CoreError("tool_failed", message, { status: 422, body: body as Record<string, unknown> });
  }
  // a 422 is pydantic's {"detail": [...]}, except where the core answers with its own error JSON
  // (PUT /profile: "invalid profile: max_parallel_agents: ...")
  if (res.status === 422 && !parsed.success) {
    return new CoreError("validation", `request rejected: ${summarizeDetail(body)}`, { status: 422 });
  }
  const raw = typeof body === "object" && body !== null && !Array.isArray(body) ? (body as Record<string, unknown>) : null;
  if (parsed.success) {
    return new CoreError(parsed.data.error, parsed.data.message, { status: res.status, hint: parsed.data.hint, retryable: parsed.data.retryable, body: raw });
  }
  const code = FALLBACK[res.status] ?? "tool_failed";
  const detail = typeof (body as { detail?: unknown })?.detail === "string" ? (body as { detail: string }).detail : message;
  return new CoreError(code, detail, { status: res.status, retryable: code === "transient", body: raw });
}

function summarizeDetail(body: unknown): string {
  const detail = (body as { detail?: unknown })?.detail;
  if (!Array.isArray(detail)) return "invalid request";
  return detail.slice(0, 3).map((d: { loc?: unknown[]; msg?: string }) =>
    `${Array.isArray(d.loc) ? d.loc.join(".") : "?"}: ${d.msg ?? "invalid"}`).join("; ");
}

/** GET /tools item. The core answers `[{name, description, safety, implied_classes, open_world}]`
 *  (server/app.py list_tools); `safety_class` is read too, and a bare name list. */
export interface ToolInfo { name: string; description: string; safety_class: string | null }
const strOrNull = (v: unknown): string | null => (typeof v === "string" ? v : null);
const toolInfo = (t: string | { name: string; [k: string]: unknown }): ToolInfo => (typeof t === "string"
  ? { name: t, description: "", safety_class: null }
  : { name: t.name, description: strOrNull(t.description) ?? "", safety_class: strOrNull(t.safety) ?? strOrNull(t.safety_class) });
export const toolListSchema: z.ZodType<ToolInfo[]> = z.array(z.union([z.string(), z.looseObject({ name: z.string() })])).transform((ts) => ts.map(toolInfo))
  .or(z.looseObject({ tools: z.array(z.unknown()) }).transform((o, ctx) => {
    const r = z.array(z.union([z.string(), z.looseObject({ name: z.string() })])).safeParse(o.tools);
    if (!r.success) { ctx.addIssue({ code: "custom", message: "bad tools list" }); return z.NEVER; }
    return r.data.map(toolInfo);
  }));

/** GET /runs/{id} seen live: see CoreClient.runLive. */
export interface RunLive { activity: unknown; questions: Question[] | null }

/** Pending questions in the question_asked shape (`id` accepted for `question_id`); a malformed
 *  one is dropped, not the rest. */
export function parsePendingQuestions(runId: string, items: readonly unknown[]): Question[] {
  const out: Question[] = [];
  for (const item of items) {
    if (typeof item !== "object" || item === null) continue;
    const o = item as Record<string, unknown>;
    const q = questionAskedSchema.safeParse({ ...o, question_id: o.question_id ?? o.id });
    if (!q.success) continue;
    if (typeof o.status === "string" && o.status !== "pending") continue;
    out.push({ question_id: q.data.question_id, run_id: typeof o.run_id === "string" ? o.run_id : runId, question: q.data.question,
      options: q.data.options, ...(q.data.questions.length ? { items: q.data.questions } : {}) });
  }
  return out;
}

const lenientSteps: z.ZodType<Step[]> =z.array(z.unknown()).transform((items) => {
  const out: Step[] = [];
  for (const item of items) {
    const r = stepSchema.safeParse(item);
    if (r.success) out.push(r.data);
    else debug("dropping malformed step", (item as { id?: unknown } | null)?.id, r.error.issues[0]?.message);
  }
  return out;
});

const enc = encodeURIComponent;
const asm = (projectId: string) => `/projects/${enc(projectId)}/assembly`;
/** remove_parts' undo: the removed pieces as the core stored them (with their list places and
 *  marks); the app only sends it back. */
export const assemblyUndoSchema = z.object({ instances: z.array(z.record(z.string(), z.unknown())), joints: z.array(z.record(z.string(), z.unknown())) });
export type AssemblyUndo = z.infer<typeof assemblyUndoSchema>;

const bomPath = (projectId: string) => `/projects/${enc(projectId)}/bom`;
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));
