// Plan 8 announcements in the thread. Shared contract with G1 (fix round 1): the core stores each
// `agent_announce` (U3c) as a decision step {agent_announce: {run_id, kind, title, detail, ref}}
// with the kind's fields under `ref`:
//
//   critique_round: ref {parts, round, max_rounds, score, threshold, done, stop_reason, mode,
//                        issues_fixed: [{view, issue, severity, suggested_fix}], issues_open: [...],
//                        iou: {front: 0.91, ...}, before: {view: render_id}, after: {view: render_id}}
//   view_presented: ref {view_spec: {..., target: {part_id} | {project_id}, ghost?}, thumbnail: render_id}
//
// Render ids are file stems served by GET /renders/{id}.png. G1's first cut (flat fields on the
// event, one `issues` list, absolute PNG paths, a string `target` with `is_project`) is read too,
// so a card never silently goes blank while the two sides converge.
//
// Everything here is model or tool output: plain data, rendered as text, never run. Images are
// either a render id or an inline PNG / JPEG / WebP data URL; anything else is dropped.
import type { Step } from "../../../../api/types";
import { parseViewSpec, type ViewSpec } from "../../../viewer/modes/viewSpec";

export type ImageRef = { id: string } | { dataUrl: string };

export type Severity = "high" | "medium" | "low";
export interface CritiqueIssue { view: string | null; issue: string; severity: Severity | null; fix: string | null }

export interface CritiqueRound {
  runId: string; title: string; detail: string;
  round: number | null; maxRounds: number | null;
  score: number | null; threshold: number | null;
  done: boolean | null;
  stopReason: string | null;
  before: ImageRef | null; after: ImageRef | null;
  fixed: CritiqueIssue[]; open: CritiqueIssue[];
  iou: { view: string; value: number }[];
  parts: string[];
}

export interface PresentedView {
  /** The thread row key (also the live-card key). */
  key: string;
  runId: string; title: string; detail: string;
  spec: ViewSpec;
  thumbnail: ImageRef | null;
}

export type G4Announcement = { kind: "critique_round"; round: CritiqueRound } | { kind: "view_presented"; view: Omit<PresentedView, "key"> };

const TEXT = 400;
const ISSUES = 20;
const IMG_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
const DATA_URL_RE = /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/]+={0,2}$/;
/** An absolute render path (G1's first cut): its file stem is the render id. */
const RENDER_PATH_RE = /[\\/]renders[\\/]([A-Za-z0-9_-]{1,64})\.png$/;
/** The view a before/after card shows when it is given one image per view. */
const VIEW_ORDER = ["iso", "front", "top", "side"];

function rec(v: unknown): Record<string, unknown> | null { return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : null; }
function str(v: unknown, n = TEXT): string { return typeof v === "string" ? v.replace(/\s+/g, " ").trim().slice(0, n) : ""; }
function strOrNull(v: unknown, n = TEXT): string | null { const s = str(v, n); return s ? s : null; }
function num(v: unknown): number | null { return typeof v === "number" && Number.isFinite(v) ? v : null; }
function ids(v: unknown): string[] { return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && IMG_ID_RE.test(x)).slice(0, 64) : []; }

/** A render id, a data URL, {image_id|id}, or a per-view map {iso: id, front: id, ...} (iso first). */
export function imageRef(v: unknown): ImageRef | null {
  if (typeof v === "string") {
    if (DATA_URL_RE.test(v) && v.length < 400_000) return { dataUrl: v };
    if (IMG_ID_RE.test(v)) return { id: v };
    const m = RENDER_PATH_RE.exec(v);
    return m ? { id: m[1] } : null;
  }
  const r = rec(v);
  if (!r) return null;
  const direct = r.image_id ?? r.id ?? r.data_url;
  if (direct !== undefined) return imageRef(direct);
  for (const view of [...VIEW_ORDER, ...Object.keys(r)]) {
    const got = typeof r[view] === "string" ? imageRef(r[view]) : null;
    if (got) return got;
  }
  return null;
}

function issues(v: unknown): CritiqueIssue[] {
  if (!Array.isArray(v)) return [];
  const out: CritiqueIssue[] = [];
  for (const x of v.slice(0, ISSUES)) {
    const r = rec(x);
    const issue = typeof x === "string" ? str(x) : str(r?.issue);
    if (!issue) continue;
    const sev = r?.severity;
    out.push({ view: strOrNull(r?.view, 40), issue, severity: sev === "high" || sev === "medium" || sev === "low" ? sev : null, fix: strOrNull(r?.suggested_fix) });
  }
  return out;
}

/** The announcement body from a stored step: {..., ref: {...}}; fields under `ref` win, and a
 *  flat body (no `ref`) is read as is. */
function body(a: Record<string, unknown>): Record<string, unknown> {
  return { ...a, ...(rec(a.ref) ?? {}) };
}

export function critiqueRound(a: Record<string, unknown>, runId: string): CritiqueRound {
  const f = body(a);
  const iou = rec(f.iou) ?? {};
  const split = Array.isArray(f.issues_fixed) || Array.isArray(f.issues_open);
  return {
    runId: str(a.run_id, 64) || runId, title: str(a.title, 120), detail: str(a.detail),
    round: num(f.round), maxRounds: num(f.max_rounds),
    score: num(f.score), threshold: num(f.threshold) ?? num(f.score_threshold),
    done: typeof f.done === "boolean" ? f.done : null,
    stopReason: strOrNull(f.stop_reason, 80),
    before: imageRef(f.before_image_id ?? f.before), after: imageRef(f.after_image_id ?? f.after),
    // one unsplit `issues` list (G1's first cut) is what this round still found
    fixed: split ? issues(f.issues_fixed) : [], open: split ? issues(f.issues_open) : issues(f.issues),
    iou: Object.entries(iou).flatMap(([view, v]) => (num(v) === null ? [] : [{ view: str(view, 40), value: Math.min(1, Math.max(0, v as number)) }])).slice(0, 8),
    parts: ids(f.parts),
  };
}

/** The G4 announcement a step carries, else null (other kinds are U3a's cards). */
export function g4Announcement(step: Step): G4Announcement | null {
  if (step.kind !== "decision") return null;
  const a = rec(step.payload["agent_announce"]);
  if (!a) return null;
  if (a.kind === "critique_round") return { kind: "critique_round", round: critiqueRound(a, step.run_id) };
  if (a.kind === "view_presented") {
    const f = body(a);
    const spec = parseViewSpec(f.view_spec);
    if (!spec) return null;
    const vs = rec(f.view_spec) ?? {};
    return {
      kind: "view_presented",
      view: {
        runId: str(a.run_id, 64) || step.run_id, title: str(a.title, 120), detail: str(a.detail), spec,
        thumbnail: imageRef(f.thumbnail_id ?? f.thumbnail ?? f.image_id ?? vs.thumbnail),
      },
    };
  }
  return null;
}
