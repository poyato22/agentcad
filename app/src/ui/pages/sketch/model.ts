// Sketch page model: the document (strokes in millimetres, origin bottom-left, +y up, like the CAD
// sketch plane; dimension labels; constraints), stroke builders, dimension parsing, undo history
// and the upload body. No DOM here, so all of it is unit-tested directly. Geometry helpers live
// in geom.ts, snapping in snap.ts, the solver in solver.ts, the view (zoom / pan) in view.ts.
import type { SketchConstraint, SketchDimension, SketchStroke, SketchStrokeKind, SketchUpload } from "../../../api/types";
import { dist, outline, round6, segDist } from "./geom";

export type Pt = [number, number];
export type Stroke = SketchStroke;
export type Dimension = SketchDimension;
export type ParsedDimension = Omit<Dimension, "stroke_id">;

export interface SketchDoc {
  readonly strokes: readonly Stroke[];
  readonly dimensions: readonly Dimension[];
  readonly notes: string;
  readonly constraints: readonly SketchConstraint[];
}

/** The drawing area: a 200 x 150 mm sheet (the core accepts any size; the page uses this one). */
export const SHEET = { width_mm: 200, height_mm: 150 } as const;
/** Same caps as core sketch.py. */
export const MAX_POINTS_PER_STROKE = 2000;
export const MAX_STROKES = 500;
/** Same as core sketch.MAX_TOTAL_POINTS (keeps the agent's context small). */
export const MAX_TOTAL_POINTS = 5000;
const CIRCLE_SEGMENTS = 64;

export function emptyDoc(): SketchDoc {
  return { strokes: [], dimensions: [], notes: "", constraints: [] };
}

export interface Box { left: number; top: number; width: number; height: number }

/** mm → SVG user units (the SVG viewBox is the sheet in mm with y down). */
export function toSvg([x, y]: Pt): Pt {
  return [x, SHEET.height_mm - y];
}

// ---- strokes

export function lineStroke(id: string, a: Pt, b: Pt): Stroke {
  return { id, kind: "line", points: [[a[0], a[1]], [b[0], b[1]]], closed: false, label: "" };
}

/** Axis-aligned rectangle from two opposite corners; points run counter-clockwise from bottom-left. */
export function rectStroke(id: string, a: Pt, b: Pt): Stroke {
  const x0 = Math.min(a[0], b[0]);
  const x1 = Math.max(a[0], b[0]);
  const y0 = Math.min(a[1], b[1]);
  const y1 = Math.max(a[1], b[1]);
  return { id, kind: "rect", points: [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], closed: true, label: "" };
}

export function circleStroke(id: string, center: Pt, radius: number): Stroke {
  const points: Pt[] = [];
  for (let i = 0; i < CIRCLE_SEGMENTS; i++) {
    const a = (2 * Math.PI * i) / CIRCLE_SEGMENTS;
    points.push([center[0] + radius * Math.cos(a), center[1] + radius * Math.sin(a)]);
  }
  return { id, kind: "circle", points, closed: true, center: [center[0], center[1]], radius_mm: radius, label: "" };
}

/** A pen / mouse / trackpad stroke: simplified (Ramer–Douglas–Peucker) and capped in size. */
export function freehandStroke(id: string, raw: readonly Pt[], tolerance = 0.3): Stroke {
  let tol = tolerance;
  let points = simplify(raw, tol);
  while (points.length > MAX_POINTS_PER_STROKE) {
    tol *= 2;
    points = simplify(raw, tol);
  }
  return { id, kind: "freehand", points, closed: false, label: "" };
}

/** Closes a freehand stroke whose end came back within `radius` mm of its start. */
export function closeIfNear(s: Stroke, radius: number): Stroke {
  if (s.kind !== "freehand" || s.points.length < 4) return s;
  const a = s.points[0];
  const b = s.points[s.points.length - 1];
  if (Math.hypot(a[0] - b[0], a[1] - b[1]) > radius) return s;
  return { ...s, points: s.points.slice(0, -1), closed: true };
}

export function simplify(pts: readonly Pt[], tolerance: number): Pt[] {
  if (pts.length <= 2) return pts.map((p) => [p[0], p[1]]);
  const keep = new Uint8Array(pts.length);
  keep[0] = 1;
  keep[pts.length - 1] = 1;
  const stack: [number, number][] = [[0, pts.length - 1]];
  while (stack.length) {
    const [i, j] = stack.pop() as [number, number];
    let maxD = -1;
    let idx = -1;
    for (let k = i + 1; k < j; k++) {
      const d = segDist(pts[k], pts[i], pts[j]);
      if (d > maxD) { maxD = d; idx = k; }
    }
    if (idx >= 0 && maxD > tolerance) {
      keep[idx] = 1;
      stack.push([i, idx], [idx, j]);
    }
  }
  return pts.filter((_, i) => keep[i]).map((p) => [p[0], p[1]]);
}

/** Drawn length: the perimeter of a closed stroke, the run of an open one. */
export function measuredLength(s: Stroke): number {
  const p = outline(s);
  const closed = s.kind === "rect" || s.kind === "circle" || s.kind === "slot" || s.closed;
  let total = 0;
  for (let i = 0; i + 1 < p.length; i++) total += dist(p[i], p[i + 1]);
  if (closed && p.length > 2) total += dist(p[p.length - 1], p[0]);
  return total;
}

/** Where a stroke's dimension label goes (mm). */
export function labelAnchor(s: Stroke): Pt {
  if (s.kind === "circle" && s.center && s.radius_mm) return [s.center[0], s.center[1] + s.radius_mm];
  const p = s.points as readonly Pt[];
  if (s.kind === "rect") return [(p[0][0] + p[1][0]) / 2, p[2][1]];
  if (s.kind === "arc" && p.length === 3) return [p[1][0], p[1][1]];
  if (s.kind === "slot" && p.length === 2) return [(p[0][0] + p[1][0]) / 2, (p[0][1] + p[1][1]) / 2 + (s.radius_mm ?? 0)];
  const o = outline(s);
  const half = measuredLength(s) / 2;
  let run = 0;
  for (let i = 0; i + 1 < o.length; i++) {
    const [a, b] = [o[i], o[i + 1]];
    const l = dist(a, b);
    if (run + l >= half && l > 0) {
      const t = (half - run) / l;
      return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
    }
    run += l;
  }
  return [p[0][0], p[0][1]];
}

// ---- dimensions

const NUM = String.raw`(\d+(?:[.,]\d+)?|[.,]\d+)`;
const UNIT = String.raw`(mm|cm|m|in|"|”|″)?`;
const UNIT_MM: Record<string, number> = { mm: 1, cm: 10, m: 1000, in: 25.4, '"': 25.4, "”": 25.4, "″": 25.4 };

/** A typed dimension label → {text, value_mm, kind}. Plain numbers are mm; a number on a circle is
 *  its diameter. Anything that isn't a number ("clamps here", "M5 thru") is kept as a note. */
export function parseDimension(text: string, strokeKind: SketchStrokeKind): ParsedDimension | null {
  const t = text.trim();
  if (!t) return null;
  const num = (s: string) => Number(s.replace(",", "."));
  const mm = (v: string, u: string | undefined) => round6(num(v) * UNIT_MM[(u ?? "mm").toLowerCase()]);
  let m = new RegExp(String.raw`^(?:Ø|⌀|ø|dia\.?|diam\.?)\s*${NUM}\s*${UNIT}$`, "i").exec(t);
  if (m) return valued(text, mm(m[1], m[2]), "diameter");
  m = new RegExp(String.raw`^R\s*${NUM}\s*${UNIT}$`, "i").exec(t);
  if (m) return valued(text, mm(m[1], m[2]), "radius");
  m = new RegExp(String.raw`^${NUM}\s*(?:°|deg)$`, "i").exec(t);
  if (m) return { text, value_mm: null, kind: "angle" };
  m = new RegExp(String.raw`^${NUM}\s*${UNIT}$`, "i").exec(t);
  if (m) return valued(text, mm(m[1], m[2]), strokeKind === "circle" ? "diameter" : "length");
  return { text, value_mm: null, kind: "note" };
}

function valued(text: string, v: number, kind: Dimension["kind"]): ParsedDimension {
  return v > 0 && Number.isFinite(v) ? { text, value_mm: v, kind } : { text, value_mm: null, kind: "note" };
}

// ---- history

/** Undo/redo over immutable docs. Clearing is an ordinary (undoable) edit. */
export class History {
  private past: SketchDoc[] = [];
  private future: SketchDoc[] = [];
  private current: SketchDoc;
  private readonly limit: number;

  constructor(doc: SketchDoc, limit = 200) {
    this.current = doc;
    this.limit = limit;
  }

  get doc(): SketchDoc { return this.current; }
  get canUndo(): boolean { return this.past.length > 0; }
  get canRedo(): boolean { return this.future.length > 0; }

  push(doc: SketchDoc): void {
    if (doc === this.current) return;
    this.past.push(this.current);
    if (this.past.length > this.limit) this.past.shift();
    this.current = doc;
    this.future = [];
  }

  undo(): void {
    const prev = this.past.pop();
    if (!prev) return;
    this.future.push(this.current);
    this.current = prev;
  }

  redo(): void {
    const next = this.future.pop();
    if (!next) return;
    this.past.push(this.current);
    this.current = next;
  }
}

// ---- upload

const r2 = (v: number) => Math.round(v * 100) / 100 + 0; // + 0 turns -0 into 0

/** The JSON the core's POST /sketches takes (sketch part). Constraints (on existing strokes) and
 *  the extrude depth are added when there are any. */
export function toUpload(doc: SketchDoc, extrude_mm: number | null = null): SketchUpload["sketch"] {
  const ids = new Set(doc.strokes.map((s) => s.id));
  const constraints = doc.constraints.filter((c) => c.refs.every((r) => ids.has(r.stroke_id))).map((c) => {
    const out: SketchConstraint = { id: c.id, kind: c.kind, refs: c.refs.map((r) => ({ ...r })) };
    if (c.value_mm !== undefined) out.value_mm = r2(c.value_mm);
    return out;
  });
  const sketch: SketchUpload["sketch"] = {
    units: "mm", width_mm: SHEET.width_mm, height_mm: SHEET.height_mm,
    strokes: fitPoints(doc.strokes).map((s) => {
      const out: Stroke = { id: s.id, kind: s.kind, points: s.points.map(([x, y]) => [r2(x), r2(y)]), closed: s.closed, label: s.label };
      if (s.center) out.center = [r2(s.center[0]), r2(s.center[1])];
      if (s.radius_mm) out.radius_mm = r2(s.radius_mm);
      if (s.construction) out.construction = true;
      if (s.corner_r?.some((r) => r > 0)) out.corner_r = s.corner_r.map(r2);
      return out;
    }),
    dimensions: doc.dimensions.filter((d) => ids.has(d.stroke_id)).map((d) => ({ ...d })),
    notes: doc.notes.trim(),
  };
  if (constraints.length) sketch.constraints = constraints;
  if (extrude_mm !== null && extrude_mm > 0) sketch.extrude_mm = r2(extrude_mm);
  return sketch;
}

export function totalPoints(strokes: readonly Stroke[]): number {
  return strokes.reduce((n, s) => n + s.points.length, 0);
}

/** Freehand strokes re-simplified (coarser each pass, up to 8 mm) until the whole sketch fits
 *  MAX_TOTAL_POINTS; lines, rectangles and circles are kept exactly. */
export function fitPoints(strokes: readonly Stroke[]): readonly Stroke[] {
  let out = strokes;
  for (let tol = 0.6; totalPoints(out) > MAX_TOTAL_POINTS && tol <= 8; tol *= 2) {
    out = strokes.map((s) => (s.kind === "freehand" ? { ...s, points: simplify(s.points as Pt[], tol) } : s));
  }
  return out;
}

/** Whether the sketch can be sent (the core refuses more than MAX_TOTAL_POINTS). */
export function withinPointBudget(doc: SketchDoc): boolean {
  return totalPoints(fitPoints(doc.strokes)) <= MAX_TOTAL_POINTS;
}

