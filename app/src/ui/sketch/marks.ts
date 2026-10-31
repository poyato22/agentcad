// The user's marks on a concept sketch image, in normalised image coordinates (x right, y down,
// 0..1 of the image) — what the core stores with the image's camera (core concept FeedbackIn).
// Pure: no DOM, no store; the review surface feeds it pointer positions.
import type { Mark } from "../../api/concept";

export type Tool = "circle" | "lasso" | "pin";
export interface Pt { x: number; y: number }
export interface Box { left: number; top: number; width: number; height: number }

/** A mark's shape alone (what a tool draws), before it gets its image and comment. */
export type MarkShape = { kind: "circle"; cx: number; cy: number; r: number } | { kind: "lasso"; points: [number, number][] } | { kind: "pin"; x: number; y: number };
/** A draft mark while the user works: the core's mark plus a local id. */
export type DraftMark = Mark & { id: string };

export const MAX_MARKS = 40;
export const MAX_LASSO_POINTS = 400;
/** Smallest circle (fraction of the image width) a drag makes; anything smaller is a click. */
export const MIN_RADIUS = 0.012;
const LASSO_STEP = 0.004;

const clamp01 = (v: number): number => (Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : 0);
const r4 = (v: number): number => Math.round(v * 1e4) / 1e4;

/** A client (pointer) position → the image's normalised point, clamped to the image. */
export function normPoint(box: Box, clientX: number, clientY: number): Pt {
  if (box.width <= 0 || box.height <= 0) return { x: 0, y: 0 };
  return { x: r4(clamp01((clientX - box.left) / box.width)), y: r4(clamp01((clientY - box.top) / box.height)) };
}

/** A circle dragged from its centre `a` to a rim point `b` on a w×h image; the radius is a
 *  fraction of the image width (core annotate draws r·w px). null when the drag is too small. */
export function circleFromDrag(a: Pt, b: Pt, w = 1, h = 1): { cx: number; cy: number; r: number } | null {
  const dpx = Math.hypot((b.x - a.x) * w, (b.y - a.y) * h);
  const r = w > 0 ? dpx / w : 0;
  if (!(r >= MIN_RADIUS)) return null;
  return { cx: r4(a.x), cy: r4(a.y), r: r4(Math.min(1, r)) };
}

/** A freehand lasso path: drops points closer than a small step, keeps at most
 *  MAX_LASSO_POINTS (evenly), and needs 3 points to enclose anything (else null). */
export function simplifyLasso(points: readonly Pt[], step = LASSO_STEP): [number, number][] | null {
  const kept: Pt[] = [];
  for (const p of points) {
    const last = kept[kept.length - 1];
    if (!last || Math.hypot(p.x - last.x, p.y - last.y) >= step) kept.push(p);
  }
  if (kept.length < 3) return null;
  let out = kept;
  if (out.length > MAX_LASSO_POINTS) {
    const k = out.length / MAX_LASSO_POINTS;
    out = Array.from({ length: MAX_LASSO_POINTS }, (_, i) => kept[Math.floor(i * k)]);
  }
  return out.map((p) => [r4(clamp01(p.x)), r4(clamp01(p.y))]);
}

/** Where a mark's number badge sits (matches core concept/sketch._draw_marks). */
export function markAnchor(m: Mark, w = 1, h = 1): Pt {
  if (m.kind === "circle") return { x: m.cx + m.r * 0.72, y: m.cy - (m.r * 0.72 * w) / h };
  if (m.kind === "lasso") {
    let best = m.points[0] ?? [0, 0];
    for (const p of m.points) if (p[0] - p[1] > best[0] - best[1]) best = p;
    return { x: best[0], y: best[1] };
  }
  return { x: m.x, y: m.y };
}

/** Plain words for a mark's kind (sentence case). */
export const MARK_WORD: Readonly<Record<Tool, string>> = { circle: "Circle", lasso: "Lasso", pin: "Pin" };

/** The marks of one image, numbered across the whole round (1-based, in order). */
export function numbered(marks: readonly DraftMark[]): { n: number; mark: DraftMark }[] {
  return marks.map((mark, i) => ({ n: i + 1, mark }));
}

/** The POST body: marks without their local ids, trimmed comments. */
export function feedbackBody(marks: readonly DraftMark[], comment: string): { marks: Mark[]; comment: string } {
  return {
    marks: marks.slice(0, MAX_MARKS).map(({ id: _id, ...m }) => ({ ...m, comment: m.comment.trim() }) as Mark),
    comment: comment.trim(),
  };
}

/** Whether a round has anything to send. */
export function canSend(marks: readonly DraftMark[], comment: string): boolean {
  return marks.length > 0 || comment.trim() !== "";
}

/** An SVG path for a lasso, in a viewBox of w×h. */
export function lassoPath(points: readonly (readonly [number, number])[], w: number, h: number): string {
  return points.map((p, i) => `${i ? "L" : "M"}${(p[0] * w).toFixed(1)} ${(p[1] * h).toFixed(1)}`).join(" ") + (points.length > 2 ? " Z" : "");
}

let seq = 0;
export const draftId = (): string => `m${Date.now().toString(36)}${(seq++).toString(36)}`;
