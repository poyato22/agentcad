// Which tool calls in the thread are studies, and what their card shows (pure, no DOM):
// sim_run / sim_static_run → a study card (type, status, live phase, metrics, checks, the
// fidelity note, the agent's reason), design_review → a review card (requirement table, each row
// linking to its study). The card itself comes from the tool result (core studies.compact():
// data.card, the checks kept once at the top level when it had to be trimmed); a running call has
// only its input, plus the live sim_progress of its study.
import { designReviewSchema, isStudyType, studyCardSchema, studyCheckSchema, type DesignReview, type StudyCard, type StudyCheck } from "../../../api/studies";
import type { LogItem } from "../logModel";

type Call = Extract<LogItem, { kind: "call" }>;

const PREFIX = /^mcp__mises__/;
export const bareToolName = (name: string): string => name.replace(PREFIX, "");

const STUDY_TOOLS = new Set(["sim_run", "sim_static_run"]);
/** Calls that render as a study or review card (and never fold away in the thread). */
export function isStudyCallName(name: string): boolean {
  const n = bareToolName(name);
  return STUDY_TOOLS.has(n) || n === "design_review";
}

const rec = (v: unknown): Record<string, unknown> => (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
const str = (v: unknown): string | null => (typeof v === "string" && v.trim() !== "" ? v : null);

export interface StudyCallView {
  kind: "study";
  callId: string;
  runId: string | null;
  type: string;
  partId: string | null;
  reason: string | null;
  /** The core's card once the result landed (null while running, or for an old result without one). */
  card: StudyCard | null;
  /** Checks of the card (or the top-level ones when compact() trimmed the card). */
  checks: StudyCheck[];
  running: boolean;
  failed: boolean;
  /** One line: the failure's message, or the result summary. */
  summary: string | null;
  hint: string | null;
  startedTs: number;
  simId: string | null;
}

export interface ReviewCallView {
  kind: "review";
  callId: string;
  runId: string | null;
  partId: string | null;
  running: boolean;
  failed: boolean;
  summary: string | null;
  hint: string | null;
  review: DesignReview | null;
}

/** The data a result step carries: output.data (a ToolOutput) or the payload itself. */
function resultData(call: Call): Record<string, unknown> {
  const p = call.result?.payload ?? {};
  const out = rec(p.output);
  if (Object.keys(rec(out.data)).length) return rec(out.data);
  return rec(p.data);
}

export function studyCallView(call: Call, runId: string | null = call.runId ?? null): StudyCallView | ReviewCallView | null {
  const name = bareToolName(call.name);
  if (!isStudyCallName(name)) return null;
  const running = call.result === null;
  const failed = call.ok === false;
  const data = running ? {} : resultData(call);
  const partId = str(call.input.part_id) ?? str(data.part_id);
  if (name === "design_review") {
    const r = designReviewSchema.safeParse(data);
    return { kind: "review", callId: call.callId, runId, partId, running, failed, summary: call.summary, hint: call.hint, review: r.success ? r.data : null };
  }
  const type = name === "sim_static_run" ? "static" : isStudyType(call.input.type) ? call.input.type : (str(data.type) ?? "study");
  const c = studyCardSchema.safeParse(data.card);
  const card = c.success ? c.data : null;
  const top = Array.isArray(data.checks) ? data.checks.map((x) => studyCheckSchema.safeParse(x)).filter((x) => x.success).map((x) => x.data) : [];
  const checks = card && card.checks.length ? card.checks : top;
  return {
    kind: "study", callId: call.callId, runId, type, partId,
    reason: str(call.input.reason) ?? card?.reason ?? null, card, checks, running, failed,
    summary: call.summary, hint: call.hint, startedTs: call.startedTs,
    simId: card?.sim_id ?? str(data.sim_id),
  };
}

/** Requirement kinds as words ("min safety factor"). */
export function kindText(kind: string | null | undefined): string {
  const map: Record<string, string> = {
    min_safety_factor: "safety factor ≥", max_deflection_mm: "deflection ≤", max_mass_g: "mass ≤", envelope_mm: "fits in",
    min_natural_freq_hz: "first frequency ≥", avoid_band_hz: "no mode in", max_drag_n: "drag ≤", max_cd: "Cd ≤",
    min_buckling_factor: "buckling factor ≥", max_temp_c: "peak temperature ≤", survive_drop_m: "survives a drop of",
    printable: "printable", mount_pattern: "mount pattern", custom: "custom",
  };
  return kind ? map[kind] ?? kind.replace(/_/g, " ") : "requirement";
}
