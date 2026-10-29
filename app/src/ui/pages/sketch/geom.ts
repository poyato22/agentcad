// Sketch geometry (pure, mm, +y up): vectors, circles through 3 points, fillets, the exact
// segments (lines, arcs, circles) a stroke stands for, sampling for display, and intersections.
// core sketch.py derives the same segments (closed profiles for the agent context).
import type { SketchStroke } from "../../../api/types";

export type Pt = [number, number];
export type Seg =
  | { t: "line"; a: Pt; b: Pt }
  | { t: "arc"; c: Pt; r: number; a: Pt; b: Pt; ccw: boolean }
  | { t: "circle"; c: Pt; r: number };

export const EPS = 1e-9;
/** Samples per full turn when an arc or circle is drawn as a polyline. */
export const TURN_SAMPLES = 64;

export const add = (a: Pt, b: Pt): Pt => [a[0] + b[0], a[1] + b[1]];
export const sub = (a: Pt, b: Pt): Pt => [a[0] - b[0], a[1] - b[1]];
export const mul = (a: Pt, k: number): Pt => [a[0] * k, a[1] * k];
export const dot = (a: Pt, b: Pt): number => a[0] * b[0] + a[1] * b[1];
export const cross = (a: Pt, b: Pt): number => a[0] * b[1] - a[1] * b[0];
export const len = (a: Pt): number => Math.hypot(a[0], a[1]);
export const dist = (a: Pt, b: Pt): number => Math.hypot(a[0] - b[0], a[1] - b[1]);
export const mid = (a: Pt, b: Pt): Pt => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
export const perp = (a: Pt): Pt => [-a[1], a[0]];
export function unit(a: Pt): Pt {
  const l = len(a);
  return l < EPS ? [1, 0] : [a[0] / l, a[1] / l];
}
/** Turn of a → b → c: > 0 counter-clockwise. */
export const turn = (a: Pt, b: Pt, c: Pt): number => cross(sub(b, a), sub(c, a));

/** The circle through 3 points, or null when they are (nearly) in a line. */
export function circumcircle(a: Pt, b: Pt, c: Pt): { c: Pt; r: number } | null {
  const d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]));
  if (Math.abs(d) < 1e-9) return null;
  const a2 = a[0] * a[0] + a[1] * a[1];
  const b2 = b[0] * b[0] + b[1] * b[1];
  const c2 = c[0] * c[0] + c[1] * c[1];
  const ux = (a2 * (b[1] - c[1]) + b2 * (c[1] - a[1]) + c2 * (a[1] - b[1])) / d;
  const uy = (a2 * (c[0] - b[0]) + b2 * (a[0] - c[0]) + c2 * (b[0] - a[0])) / d;
  return { c: [ux, uy], r: dist([ux, uy], a) };
}

/** Signed sweep (radians) of an arc segment: > 0 counter-clockwise. */
export function arcSweep(s: Extract<Seg, { t: "arc" }>): number {
  const a0 = Math.atan2(s.a[1] - s.c[1], s.a[0] - s.c[0]);
  const a1 = Math.atan2(s.b[1] - s.c[1], s.b[0] - s.c[0]);
  const TAU = 2 * Math.PI;
  const m = (v: number) => ((v % TAU) + TAU) % TAU;
  const sw = s.ccw ? m(a1 - a0) : -m(a0 - a1);
  return Math.abs(sw) > 1e-12 ? sw : s.ccw ? TAU : -TAU;
}

export interface Fillet { t1: Pt; t2: Pt; c: Pt; r: number; ccw: boolean }

/** A fillet of radius r at corner p between prev and next (shrunk to fit half of each edge). */
export function fillet(prev: Pt, p: Pt, next: Pt, r: number): Fillet | null {
  const l1 = dist(prev, p);
  const l2 = dist(next, p);
  if (r <= 0 || l1 < EPS || l2 < EPS) return null;
  const u1 = mul(sub(prev, p), 1 / l1);
  const u2 = mul(sub(next, p), 1 / l2);
  const theta = Math.acos(Math.max(-1, Math.min(1, dot(u1, u2))));
  if (theta < 1e-6 || theta > Math.PI - 1e-6) return null;
  const t = Math.min(r / Math.tan(theta / 2), l1 / 2, l2 / 2);
  const rr = t * Math.tan(theta / 2);
  const bis = unit(add(u1, u2));
  return { t1: add(p, mul(u1, t)), t2: add(p, mul(u2, t)), c: add(p, mul(bis, rr / Math.sin(theta / 2))), r: rr, ccw: turn(prev, p, next) > 0 };
}

/** Lines (and fillet arcs at corners with a radius) along a chain of points. */
export function chainSegments(pts: readonly Pt[], closed: boolean, cornerR?: readonly number[]): Seg[] {
  const n = pts.length;
  const fil = pts.map((p, i) => {
    const r = cornerR?.[i] ?? 0;
    return r > 0 && (closed || (i > 0 && i < n - 1)) ? fillet(pts[(i - 1 + n) % n], p, pts[(i + 1) % n], r) : null;
  });
  const enter = pts.map((p, i) => fil[i]?.t1 ?? p);
  const leave = pts.map((p, i) => fil[i]?.t2 ?? p);
  const out: Seg[] = [];
  for (let i = 0; i < n; i++) {
    const f = fil[i];
    if (f) out.push({ t: "arc", c: f.c, r: f.r, a: f.t1, b: f.t2, ccw: f.ccw });
    if (closed || i < n - 1) {
      const j = (i + 1) % n;
      if (dist(leave[i], enter[j]) > 1e-12) out.push({ t: "line", a: leave[i], b: enter[j] });
    }
  }
  return out;
}

/** Whether a stroke bounds a region on its own. */
export function isClosedKind(s: SketchStroke): boolean {
  return s.kind === "rect" || s.kind === "circle" || s.kind === "slot" || (s.closed && s.points.length > 2);
}

/** The exact geometry a stroke stands for. */
export function strokeSegments(s: SketchStroke): Seg[] {
  const p = s.points as Pt[];
  switch (s.kind) {
    case "circle":
      if (s.center && s.radius_mm) return [{ t: "circle", c: s.center, r: s.radius_mm }];
      return p.length > 2 ? chainSegments(p, true) : [];
    case "arc": {
      const cc = p.length === 3 ? circumcircle(p[0], p[1], p[2]) : null;
      if (!cc) return p.length > 1 ? chainSegments(p, false) : [];
      return [{ t: "arc", c: cc.c, r: cc.r, a: p[0], b: p[2], ccw: turn(p[0], p[1], p[2]) > 0 }];
    }
    case "slot": {
      const [a, b] = p;
      const r = s.radius_mm ?? 0;
      if (!b || dist(a, b) < EPS) return [{ t: "circle", c: a, r }];
      const n = mul(perp(unit(sub(b, a))), r);
      const p1 = sub(a, n); const p2 = sub(b, n); const p3 = add(b, n); const p4 = add(a, n);
      return [
        { t: "line", a: p1, b: p2 }, { t: "arc", c: b, r, a: p2, b: p3, ccw: true },
        { t: "line", a: p3, b: p4 }, { t: "arc", c: a, r, a: p4, b: p1, ccw: true },
      ];
    }
    default:
      return chainSegments(p, (s.kind === "rect" || s.closed) && p.length > 2, s.corner_r);
  }
}

export function segEnds(s: Seg): [Pt, Pt] {
  return s.t === "circle" ? [[s.c[0] + s.r, s.c[1]], [s.c[0] + s.r, s.c[1]]] : [s.a, s.b];
}

export function reverseSegs(segs: readonly Seg[]): Seg[] {
  return [...segs].reverse().map((s): Seg => (s.t === "line" ? { t: "line", a: s.b, b: s.a } : s.t === "arc" ? { ...s, a: s.b, b: s.a, ccw: !s.ccw } : s));
}

/** Points along segments (each segment's start, arcs sampled); for a loop the last point is not repeated. */
export function sampleSegs(segs: readonly Seg[], perTurn = TURN_SAMPLES): Pt[] {
  const out: Pt[] = [];
  for (const s of segs) {
    if (s.t === "line") out.push(s.a);
    else if (s.t === "circle") {
      for (let i = 0; i < perTurn; i++) out.push([s.c[0] + s.r * Math.cos((2 * Math.PI * i) / perTurn), s.c[1] + s.r * Math.sin((2 * Math.PI * i) / perTurn)]);
    } else {
      const sw = arcSweep(s);
      const a0 = Math.atan2(s.a[1] - s.c[1], s.a[0] - s.c[0]);
      const k = Math.max(2, Math.ceil((Math.abs(sw) / (2 * Math.PI)) * perTurn));
      for (let i = 0; i < k; i++) out.push([s.c[0] + s.r * Math.cos(a0 + (sw * i) / k), s.c[1] + s.r * Math.sin(a0 + (sw * i) / k)]);
    }
  }
  return out;
}

/** What to draw for a stroke: a polyline (closed strokes don't repeat their first point). */
export function outline(s: SketchStroke): Pt[] {
  if (s.kind === "freehand" || (s.kind !== "arc" && s.kind !== "slot" && s.kind !== "circle" && !s.corner_r?.some((r) => r > 0))) {
    return s.points.map((p) => [p[0], p[1]]);
  }
  const segs = strokeSegments(s);
  const pts = sampleSegs(segs);
  if (!isClosedKind(s) && segs.length) pts.push(segEnds(segs[segs.length - 1])[1]);
  return pts;
}

/** Exact signed area of a loop (> 0 counter-clockwise). */
export function signedArea(segs: readonly Seg[]): number {
  if (segs.length === 1 && segs[0].t === "circle") return Math.PI * segs[0].r ** 2;
  let a = 0;
  for (const s of segs) {
    const [p, q] = segEnds(s);
    a += (p[0] * q[1] - q[0] * p[1]) / 2;
    if (s.t === "arc") {
      const sw = arcSweep(s);
      a += Math.sign(sw) * ((s.r ** 2) / 2) * (Math.abs(sw) - Math.sin(Math.abs(sw)));
    }
  }
  return a;
}

export function pointInPolygon(p: Pt, poly: readonly Pt[]): boolean {
  let hit = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [x1, y1] = poly[i];
    const [x2, y2] = poly[j];
    if ((y1 > p[1]) !== (y2 > p[1]) && p[0] < ((x2 - x1) * (p[1] - y1)) / (y2 - y1) + x1) hit = !hit;
  }
  return hit;
}

/** Nearest point on segment ab to p, and its parameter t in [0, 1]. */
export function nearestOnSeg(p: Pt, a: Pt, b: Pt): { point: Pt; t: number } {
  const d = sub(b, a);
  const l2 = dot(d, d);
  const t = l2 < EPS ? 0 : Math.max(0, Math.min(1, dot(sub(p, a), d) / l2));
  return { point: add(a, mul(d, t)), t };
}

export function segDist(p: Pt, a: Pt, b: Pt): number {
  return dist(p, nearestOnSeg(p, a, b).point);
}

/** Intersection of segments ab and cd (proper or touching), or null. */
export function segSeg(a: Pt, b: Pt, c: Pt, d: Pt): Pt | null {
  const r = sub(b, a);
  const s = sub(d, c);
  const den = cross(r, s);
  if (Math.abs(den) < EPS) return null;
  const t = cross(sub(c, a), s) / den;
  const u = cross(sub(c, a), r) / den;
  if (t < -1e-9 || t > 1 + 1e-9 || u < -1e-9 || u > 1 + 1e-9) return null;
  return add(a, mul(r, t));
}

/** Intersection of the infinite lines through a (direction u) and b (direction v), or null if parallel. */
export function lineLine(a: Pt, u: Pt, b: Pt, v: Pt): Pt | null {
  const den = cross(u, v);
  if (Math.abs(den) < 1e-9) return null;
  return add(a, mul(u, cross(sub(b, a), v) / den));
}

/** Where segment ab crosses the circle (c, r). */
export function segCircle(a: Pt, b: Pt, c: Pt, r: number): Pt[] {
  const d = sub(b, a);
  const f = sub(a, c);
  const A = dot(d, d);
  if (A < EPS) return [];
  const B = 2 * dot(f, d);
  const C = dot(f, f) - r * r;
  const disc = B * B - 4 * A * C;
  if (disc < 0) return [];
  const sq = Math.sqrt(disc);
  const out: Pt[] = [];
  for (const t of disc < EPS ? [-B / (2 * A)] : [(-B - sq) / (2 * A), (-B + sq) / (2 * A)]) {
    if (t >= -1e-9 && t <= 1 + 1e-9) out.push(add(a, mul(d, t)));
  }
  return out;
}

export function circleCircle(c1: Pt, r1: number, c2: Pt, r2: number): Pt[] {
  const d = dist(c1, c2);
  if (d < EPS || d > r1 + r2 + 1e-9 || d < Math.abs(r1 - r2) - 1e-9) return [];
  const a = (r1 * r1 - r2 * r2 + d * d) / (2 * d);
  const h = Math.sqrt(Math.max(0, r1 * r1 - a * a));
  const u = unit(sub(c2, c1));
  const m = add(c1, mul(u, a));
  if (h < 1e-9) return [m];
  return [add(m, mul(perp(u), h)), sub(m, mul(perp(u), h))];
}

/** Tangent points on circle (c, r) seen from p (none when p is inside). */
export function tangentPoints(p: Pt, c: Pt, r: number): Pt[] {
  const d = dist(p, c);
  if (d <= r + 1e-9) return [];
  const a = Math.acos(r / d);
  const base = Math.atan2(p[1] - c[1], p[0] - c[0]);
  return [base + a, base - a].map((t): Pt => [c[0] + r * Math.cos(t), c[1] + r * Math.sin(t)]);
}

/** p reflected about the line through a and b. */
export function reflect(p: Pt, a: Pt, b: Pt): Pt {
  const u = unit(sub(b, a));
  const v = sub(p, a);
  const along = mul(u, dot(v, u));
  return add(a, sub(mul(along, 2), v));
}

export function round6(v: number): number {
  return Math.round(v * 1e6) / 1e6 + 0;
}
