// Sketch page model: pure geometry in millimetres (origin bottom-left, +y up, like the CAD sketch
// plane), screen → mm conversion, snapping, stroke builders, dimension parsing and undo history.
// No DOM here, so all of it is unit-tested directly.
import type { SketchDimension, SketchStroke, SketchStrokeKind, SketchUpload } from "../../../api/types";

export type Pt = [number, number];
export type Stroke = SketchStroke;
export type Dimension = SketchDimension;
export type ParsedDimension = Omit<Dimension, "stroke_id">;

export interface SketchDoc {
  readonly strokes: readonly Stroke[];
  readonly dimensions: readonly Dimension[];
  readonly notes: string;
}

/** The drawing area: a 200 x 150 mm sheet (the core accepts any size; the page uses this one). */
export const SHEET = { width_mm: 200, height_mm: 150 } as const;
/** Same caps as core sketch.py. */
export const MAX_POINTS_PER_STROKE = 2000;
export const MAX_STROKES = 500;
const CIRCLE_SEGMENTS = 64;
const ANGLE_SNAP_DEG = 6;

export function emptyDoc(): SketchDoc {
  return { strokes: [], dimensions: [], notes: "" };
}

export interface Box { left: number; top: number; width: number; height: number }

/** The sheet's placement inside an element box (SVG preserveAspectRatio="xMidYMin meet": centred across, top-aligned). */
export function sheetPlacement(rect: Box): { scale: number; x0: number; y0: number } {
  const scale = Math.min(rect.width / SHEET.width_mm, rect.height / SHEET.height_mm);
  return {
    scale,
    x0: rect.left + (rect.width - SHEET.width_mm * scale) / 2,
    y0: rect.top,
  };
}

/** Screen (client) px → sheet mm, +y up, clamped to the sheet. */
export function clientToMm(clientX: number, clientY: number, rect: Box): Pt {
  const { scale, x0, y0 } = sheetPlacement(rect);
  const x = (clientX - x0) / scale;
  const yDown = (clientY - y0) / scale;
  return [clamp(x, 0, SHEET.width_mm), clamp(SHEET.height_mm - yDown, 0, SHEET.height_mm)];
}

/** mm → SVG user units (the SVG viewBox is the sheet in mm with y down). */
export function toSvg([x, y]: Pt): Pt {
  return [x, SHEET.height_mm - y];
}

/** px → mm at the current zoom (for screen-sized tolerances like the snap radius). */
export function pxToMm(px: number, rect: Box): number {
  const { scale } = sheetPlacement(rect);
  return scale > 0 ? px / scale : px;
}

// ---- snapping

export type SnapKind = "endpoint" | "angle" | "grid" | null;
export interface SnapContext {
  /** Grid step in mm, or null when grid snapping is off. */
  grid: number | null;
  /** Points a new point may snap onto (stroke ends, corners, circle centres). */
  endpoints: readonly Pt[];
  /** Endpoint snap radius in mm. */
  radius: number;
  /** The start of the segment being drawn: enables 0/45/90° angle snapping. */
  from?: Pt;
  /** Angle snapping on (default true when `from` is given). */
  angles?: boolean;
}

export function snapPoint(p: Pt, ctx: SnapContext): { point: Pt; kind: SnapKind } {
  let best: Pt | null = null;
  let bestD = ctx.radius;
  for (const e of ctx.endpoints) {
    const d = Math.hypot(e[0] - p[0], e[1] - p[1]);
    if (d <= bestD) { best = e; bestD = d; }
  }
  if (best) return { point: [best[0], best[1]], kind: "endpoint" };

  if (ctx.from && ctx.angles !== false) {
    const [fx, fy] = ctx.from;
    const dx = p[0] - fx;
    const dy = p[1] - fy;
    const len = Math.hypot(dx, dy);
    if (len > 0) {
      const deg = (Math.atan2(dy, dx) * 180) / Math.PI;
      const snapped = Math.round(deg / 45) * 45;
      if (Math.abs(deg - snapped) <= ANGLE_SNAP_DEG) {
        const ux = round6(Math.cos((snapped * Math.PI) / 180));
        const uy = round6(Math.sin((snapped * Math.PI) / 180));
        let t = len * (ux !== 0 && uy !== 0 ? Math.SQRT1_2 : 1); // per-axis travel along the snapped direction
        if (ctx.grid) t = Math.max(ctx.grid, Math.round(t / ctx.grid) * ctx.grid);
        const sx = Math.sign(ux);
        const sy = Math.sign(uy);
        return { point: [fx + sx * t, fy + sy * t], kind: "angle" };
      }
    }
  }
  if (ctx.grid) return { point: [snapTo(p[0], ctx.grid), snapTo(p[1], ctx.grid)], kind: "grid" };
  return { point: [p[0], p[1]], kind: null };
}

function snapTo(v: number, step: number): number {
  return round6(Math.round(v / step) * step);
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

function segments(s: Stroke): [Pt, Pt][] {
  const p = s.points as readonly Pt[];
  const out: [Pt, Pt][] = [];
  for (let i = 0; i + 1 < p.length; i++) out.push([p[i], p[i + 1]]);
  if (s.closed && p.length > 2) out.push([p[p.length - 1], p[0]]);
  return out;
}

export function measuredLength(s: Stroke): number {
  return segments(s).reduce((acc, [a, b]) => acc + Math.hypot(b[0] - a[0], b[1] - a[1]), 0);
}

/** Snap targets: line/freehand ends, rect corners, circle centres. */
export function endpoints(strokes: readonly Stroke[]): Pt[] {
  const out: Pt[] = [];
  for (const s of strokes) {
    const p = s.points as readonly Pt[];
    if (s.kind === "circle") { if (s.center) out.push(s.center as Pt); }
    else if (s.kind === "rect") out.push(...p);
    else if (p.length) {
      out.push(p[0]);
      if (!s.closed && p.length > 1) out.push(p[p.length - 1]);
    }
  }
  return out;
}

/** The stroke nearest to `p` within `tol` mm, or null. */
export function hitStroke(strokes: readonly Stroke[], p: Pt, tol: number): Stroke | null {
  let best: Stroke | null = null;
  let bestD = tol;
  for (const s of strokes) {
    const segs = segments(s);
    const d = segs.length ? Math.min(...segs.map(([a, b]) => segDist(p, a, b))) : Math.hypot(p[0] - s.points[0][0], p[1] - s.points[0][1]);
    if (d <= bestD) { best = s; bestD = d; }
  }
  return best;
}

/** Where a stroke's dimension label goes (mm). */
export function labelAnchor(s: Stroke): Pt {
  if (s.kind === "circle" && s.center && s.radius_mm) return [s.center[0], s.center[1] + s.radius_mm];
  const p = s.points as readonly Pt[];
  if (s.kind === "rect") return [(p[0][0] + p[1][0]) / 2, p[2][1]];
  const half = measuredLength(s) / 2;
  let run = 0;
  for (const [a, b] of segments(s)) {
    const l = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (run + l >= half && l > 0) {
      const t = (half - run) / l;
      return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
    }
    run += l;
  }
  return [p[0][0], p[0][1]];
}

function segDist(p: Pt, a: Pt, b: Pt): number {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const l2 = dx * dx + dy * dy;
  const t = l2 === 0 ? 0 : clamp(((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2, 0, 1);
  return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
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

/** The JSON the core's POST /sketches takes (sketch part). */
export function toUpload(doc: SketchDoc): SketchUpload["sketch"] {
  const ids = new Set(doc.strokes.map((s) => s.id));
  return {
    units: "mm", width_mm: SHEET.width_mm, height_mm: SHEET.height_mm,
    strokes: doc.strokes.map((s) => {
      const out: Stroke = { id: s.id, kind: s.kind, points: s.points.map(([x, y]) => [r2(x), r2(y)]), closed: s.closed, label: s.label };
      if (s.center) out.center = [r2(s.center[0]), r2(s.center[1])];
      if (s.radius_mm) out.radius_mm = r2(s.radius_mm);
      return out;
    }),
    dimensions: doc.dimensions.filter((d) => ids.has(d.stroke_id)).map((d) => ({ ...d })),
    notes: doc.notes.trim(),
  };
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

function round6(v: number): number {
  return Math.round(v * 1e6) / 1e6 + 0;
}
