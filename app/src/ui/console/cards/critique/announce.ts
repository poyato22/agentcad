// Plan 8 announcements in the thread. The core stores each `agent_announce` (U3c) as a decision
// step {agent_announce: {run_id, kind, title, detail, ref}}; G1 adds two kinds:
//
//   critique_round: ref {round, max_rounds, score, threshold, stop_reason,
//                        before_image_id, after_image_id (or before/after: {image_id} | data URL),
//                        issues_fixed: [{view, issue, severity, suggested_fix}], issues_open: [...],
//                        iou: {front: 0.91, ...}, part_id | project_id}
//   view_presented: ref {view_spec: {...}, thumbnail_id?}  (view_spec may also sit at the top level)
//
// Everything here is model or tool output: plain data, rendered as text, never run. Images are
// either a render id (fetched from the core) or an inline PNG / JPEG / WebP data URL; anything else
// is dropped.
import type { Step } from "../../../../api/types";
import { parseViewSpec, type ViewSpec } from "../../../viewer/modes/viewSpec";

export type ImageRef = { id: string } | { dataUrl: string };

export type Severity = "high" | "medium" | "low";
export interface CritiqueIssue { view: string | null; issue: string; severity: Severity | null; fix: string | null }

export interface CritiqueRound {
  runId: string; title: string; detail: string;
  round: number | null; maxRounds: number | null;
  score: number | null; threshold: number | null;
  stopReason: string | null;
  before: ImageRef | null; after: ImageRef | null;
  fixed: CritiqueIssue[]; open: CritiqueIssue[];
  iou: { view: string; value: number }[];
  partId: string | null; projectId: string | null;
}

export interface PresentedView {
  /** The thread row key (also the live-card key). */
  key: string;
  runId: string; title: string; detail: string;
  spec: ViewSpec;
  thumbnail: ImageRef | null;
}

export type G4Announcement = { kind: "critique_round"; round: CritiqueRound } | { kind: "view_presented"; view: Omit<PresentedView, "key"> };

const TEXT = 300;
const ISSUES = 20;
const IMG_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
const DATA_URL_RE = /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/]+={0,2}$/;

function rec(v: unknown): Record<string, unknown> | null { return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : null; }
function str(v: unknown, n = TEXT): string { return typeof v === "string" ? v.replace(/\s+/g, " ").trim().slice(0, n) : ""; }
function strOrNull(v: unknown, n = TEXT): string | null { const s = str(v, n); return s ? s : null; }
function num(v: unknown): number | null { return typeof v === "number" && Number.isFinite(v) ? v : null; }

export function imageRef(v: unknown): ImageRef | null {
  if (typeof v === "string") {
    if (DATA_URL_RE.test(v) && v.length < 400_000) return { dataUrl: v };
    return IMG_ID_RE.test(v) ? { id: v } : null;
  }
  const r = rec(v);
  if (!r) return null;
  return imageRef(r.image_id ?? r.id ?? r.data_url ?? r.thumbnail);
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

/** The G4 announcement a step carries, else null (other kinds are U3a's cards). */
export function g4Announcement(step: Step): G4Announcement | null {
  if (step.kind !== "decision") return null;
  const a = rec(step.payload["agent_announce"]);
  if (!a) return null;
  const ref = rec(a.ref) ?? {};
  const base = { runId: str(a.run_id, 64) || step.run_id, title: str(a.title, 120), detail: str(a.detail) };
  if (a.kind === "critique_round") {
    const iou = rec(ref.iou) ?? {};
    return {
      kind: "critique_round",
      round: {
        ...base,
        round: num(ref.round), maxRounds: num(ref.max_rounds),
        score: num(ref.score), threshold: num(ref.threshold),
        stopReason: strOrNull(ref.stop_reason, 80),
        before: imageRef(ref.before_image_id ?? ref.before), after: imageRef(ref.after_image_id ?? ref.after),
        fixed: issues(ref.issues_fixed), open: issues(ref.issues_open),
        iou: Object.entries(iou).flatMap(([view, v]) => (num(v) === null ? [] : [{ view: str(view, 40), value: Math.min(1, Math.max(0, v as number)) }])).slice(0, 8),
        partId: strOrNull(ref.part_id, 128), projectId: strOrNull(ref.project_id, 128),
      },
    };
  }
  if (a.kind === "view_presented") {
    const spec = parseViewSpec(ref.view_spec ?? a.view_spec);
    if (!spec) return null;
    return { kind: "view_presented", view: { ...base, spec, thumbnail: imageRef(ref.thumbnail_id ?? ref.thumbnail ?? ref.image_id) } };
  }
  return null;
}
