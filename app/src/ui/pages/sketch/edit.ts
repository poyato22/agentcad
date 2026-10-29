// Editing operations (pure): hit-testing vertices and edges, move, mirror about a line, offset,
// fillet a corner, construction toggle. Each returns new strokes; the page runs the solver after.
import type { SketchStroke } from "../../../api/types";
import {
  add, circumcircle, dist, lineLine, mul, outline, perp, reflect, segDist, signedArea, strokeSegments, sub, unit, type Pt,
} from "./geom";
import type { Ref } from "./solver";

type Stroke = SketchStroke;
const CIRCLE_SEGMENTS = 64;

function circlePts(c: Pt, r: number): [number, number][] {
  return Array.from({ length: CIRCLE_SEGMENTS }, (_, i) => {
    const a = (2 * Math.PI * i) / CIRCLE_SEGMENTS;
    return [c[0] + r * Math.cos(a), c[1] + r * Math.sin(a)];
  });
}

/** The draggable points of a stroke: a circle's centre; otherwise its points (freehand: none). */
export function handles(s: Stroke): Pt[] {
  if (s.kind === "freehand") return [];
  if (s.kind === "circle") return s.center ? [s.center] : [];
  return s.points as Pt[];
}

/** The vertex nearest p within tol (strokes listed later win ties: they're drawn on top). */
export function hitVertex(strokes: readonly Stroke[], p: Pt, tol: number): Ref | null {
  let best: Ref | null = null;
  let bestD = tol;
  for (const s of strokes) {
    handles(s).forEach((q, i) => {
      const d = dist(p, q);
      if (d <= bestD) { best = { stroke_id: s.id, vertex: i }; bestD = d; }
    });
  }
  return best;
}

/** Distance from p to what the stroke draws. */
export function strokeDist(s: Stroke, p: Pt): number {
  const o = outline(s);
  if (o.length === 1) return dist(p, o[0]);
  const closed = s.kind === "rect" || s.kind === "circle" || s.kind === "slot" || s.closed;
  let d = Infinity;
  for (let i = 0; i + 1 < o.length; i++) d = Math.min(d, segDist(p, o[i], o[i + 1]));
  if (closed && o.length > 2) d = Math.min(d, segDist(p, o[o.length - 1], o[0]));
  return d;
}

export function hitStroke(strokes: readonly Stroke[], p: Pt, tol: number): Stroke | null {
  let best: Stroke | null = null;
  let bestD = tol;
  for (const s of strokes) {
    const d = strokeDist(s, p);
    if (d <= bestD) { best = s; bestD = d; }
  }
  return best;
}

/** The edge (points[i] → points[i+1]) of a line / polyline / rectangle nearest p, or null. */
export function nearestEdge(s: Stroke, p: Pt): number | null {
  if (s.kind !== "line" && s.kind !== "polyline" && s.kind !== "rect") return null;
  const pts = s.points as Pt[];
  const closed = (s.kind === "rect" || s.closed) && pts.length > 2;
  let best: number | null = null;
  let bestD = Infinity;
  const n = closed ? pts.length : pts.length - 1;
  for (let i = 0; i < n; i++) {
    const d = segDist(p, pts[i], pts[(i + 1) % pts.length]);
    if (d < bestD) { best = i; bestD = d; }
  }
  return best;
}

export function moveVertex(s: Stroke, i: number, p: Pt): Stroke {
  if (s.kind === "circle") return { ...s, center: [p[0], p[1]], points: circlePts(p, s.radius_mm ?? 1) };
  const points = s.points.map((q, k) => (k === i ? [p[0], p[1]] : [q[0], q[1]])) as [number, number][];
  return { ...s, points };
}

export function translate(s: Stroke, d: Pt): Stroke {
  const out: Stroke = { ...s, points: s.points.map((q) => [q[0] + d[0], q[1] + d[1]]) };
  if (s.center) out.center = [s.center[0] + d[0], s.center[1] + d[1]];
  return out;
}

const axisAligned = (a: Pt, b: Pt) => Math.abs(a[0] - b[0]) < 1e-9 || Math.abs(a[1] - b[1]) < 1e-9;

/** A mirrored copy about the line through a and b. A rectangle mirrored about a slanted line becomes
 *  a closed polyline (it is no longer axis-aligned). */
export function mirror(s: Stroke, a: Pt, b: Pt, id: string): Stroke {
  const pts = s.points.map((q) => reflect(q as Pt, a, b)) as [number, number][];
  // reflection reverses the winding; keep each kind's point order meaningful
  const out: Stroke = { ...s, id, points: pts, construction: s.construction };
  if (s.center) out.center = reflect(s.center, a, b);
  // the corners keep their order (and fillets); only the winding flips, which nothing depends on
  if (s.kind === "rect" && !axisAligned(a, b)) { out.kind = "polyline"; out.closed = true; }
  if (s.corner_r) out.corner_r = [...s.corner_r];
  if (!out.construction) delete out.construction;
  if (!out.corner_r) delete out.corner_r;
  return out;
}

/** An offset copy, `d` mm outward (closed: away from the inside; open: to the left), or null. */
export function offset(s: Stroke, d: number, id: string): Stroke | null {
  const base = { ...s, id };
  if (s.kind === "circle" && s.center && s.radius_mm) {
    const r = s.radius_mm + d;
    return r > 0 ? { ...base, radius_mm: r, points: circlePts(s.center, r) } : null;
  }
  if (s.kind === "slot" && s.radius_mm) {
    const r = s.radius_mm + d;
    return r > 0 ? { ...base, radius_mm: r } : null;
  }
  if (s.kind === "arc") {
    const p = s.points as Pt[];
    const cc = circumcircle(p[0], p[1], p[2]);
    if (!cc) return null;
    // outward = away from the centre (the arc's convex side)
    if (cc.r + d <= 0) return null;
    return { ...base, points: p.map((q) => add(cc.c, mul(unit(sub(q, cc.c)), cc.r + d))) as [number, number][] };
  }
  if (s.kind === "freehand") return null;
  const pts = s.points as Pt[];
  const closed = (s.kind === "rect" || s.closed) && pts.length > 2;
  if (pts.length < 2) return null;
  // closed: outward means right of a counter-clockwise loop; open: left of travel
  const sign = closed ? (signedArea(strokeSegments({ ...s, corner_r: undefined })) > 0 ? -1 : 1) : 1;
  const n = pts.length;
  const lines: { o: Pt; u: Pt }[] = [];
  const m = closed ? n : n - 1;
  for (let i = 0; i < m; i++) {
    const a = pts[i];
    const b = pts[(i + 1) % n];
    const u = unit(sub(b, a));
    lines.push({ o: add(a, mul(perp(u), d * sign)), u });
  }
  const out: Pt[] = [];
  for (let i = 0; i < n; i++) {
    const prev = closed ? lines[(i - 1 + m) % m] : lines[i - 1];
    const next = closed ? lines[i % m] : lines[i];
    if (prev && next) out.push(lineLine(prev.o, prev.u, next.o, next.u) ?? next.o);   // collinear: just shift
    else if (next) out.push(next.o);
    else if (prev) out.push(add(prev.o, mul(prev.u, dist(pts[i - 1], pts[i]))));
  }
  const res: Stroke = { ...base, points: out.map((q) => [q[0], q[1]]) };
  if (s.corner_r) res.corner_r = s.corner_r.map((r) => (r > 0 ? Math.max(0, r + d) : 0));
  return res;
}

/** Sets the fillet radius at a polyline / rectangle corner (0 removes it); null when not a corner. */
export function setCorner(s: Stroke, i: number, r: number): Stroke | null {
  if (s.kind !== "polyline" && s.kind !== "rect") return null;
  const n = s.points.length;
  const closed = s.kind === "rect" || s.closed;
  if (i < 0 || i >= n || (!closed && (i === 0 || i === n - 1))) return null;
  const corner_r = s.corner_r ? [...s.corner_r] : new Array<number>(n).fill(0);
  corner_r[i] = Math.max(0, r);
  const out: Stroke = { ...s, corner_r };
  if (!corner_r.some((x) => x > 0)) delete out.corner_r;
  return out;
}

/** The corner (vertex of a polyline or rectangle) nearest p within tol. */
export function hitCorner(strokes: readonly Stroke[], p: Pt, tol: number): Ref | null {
  return hitVertex(strokes.filter((s) => s.kind === "polyline" || s.kind === "rect"), p, tol);
}

export function toggleConstruction(s: Stroke): Stroke {
  const out: Stroke = { ...s, construction: !s.construction };
  if (!out.construction) delete out.construction;
  return out;
}

/** A mirror axis: a line, or a polyline's first edge. */
export function axisOf(s: Stroke): [Pt, Pt] | null {
  if ((s.kind === "line" || s.kind === "polyline") && s.points.length >= 2) return [s.points[0] as Pt, s.points[1] as Pt];
  return null;
}
