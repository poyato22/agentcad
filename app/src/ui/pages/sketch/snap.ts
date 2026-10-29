// Snapping and inference (pure): where the pointer lands and why. In priority order:
//  1. points: endpoints, centres, intersections, midpoints (within the snap radius);
//  2. inference guides, SketchUp-style: from the point being drawn from — horizontal, vertical,
//     45°, parallel / perpendicular to an existing edge, tangent to a circle — and alignment with
//     existing points; two guides that cross near the pointer snap to their crossing;
//  3. on a curve (nearest point on an edge or circle);
//  4. the grid.
// Guides come back as dashed lines with a short label for the canvas to draw.
import type { SketchRef, SketchStroke } from "../../../api/types";
import {
  circleCircle, circumcircle, cross, dist, dot, lineLine, mid, nearestOnSeg, perp, segCircle, segSeg, sub, tangentPoints, unit, add, mul,
  round6, type Pt,
} from "./geom";

export type SnapKind = "endpoint" | "center" | "intersection" | "midpoint" | "guide" | "on" | "grid" | null;
export type GuideKind = "horizontal" | "vertical" | "angle" | "parallel" | "perpendicular" | "tangent" | "aligned";
export interface Guide { kind: GuideKind; a: Pt; b: Pt; label: string }
export interface Snap { point: Pt; kind: SnapKind; label: string | null; guides: Guide[]; ref?: SketchRef }

export interface Targets {
  endpoints: { p: Pt; ref?: SketchRef }[];
  centers: { p: Pt; ref?: SketchRef }[];
  midpoints: Pt[];
  intersections: Pt[];
  edges: [Pt, Pt][];
  circles: { c: Pt; r: number }[];
}

const MAX_INTERSECT_EDGES = 300;
const MAX_ALIGN_POINTS = 200;
const NEAR_PARALLEL = Math.sin((2 * Math.PI) / 180);
const FROM_ANGLE_TOL = Math.sin((3 * Math.PI) / 180);

export const POINT_LABEL: Record<"endpoint" | "center" | "intersection" | "midpoint" | "on", string> = {
  endpoint: "Endpoint", center: "Center", intersection: "Intersection", midpoint: "Midpoint", on: "On edge",
};

/** What a new point can snap to, from the existing strokes (`exclude`: strokes being edited). */
export function snapTargets(strokes: readonly SketchStroke[], exclude: ReadonlySet<string> = new Set()): Targets {
  const t: Targets = { endpoints: [], centers: [], midpoints: [], intersections: [], edges: [], circles: [] };
  for (const s of strokes) {
    if (exclude.has(s.id)) continue;
    const p = s.points as Pt[];
    if (!p.length) continue;
    switch (s.kind) {
      case "freehand":
        t.endpoints.push({ p: p[0], ref: { stroke_id: s.id, vertex: 0 } });
        if (!s.closed && p.length > 1) t.endpoints.push({ p: p[p.length - 1], ref: { stroke_id: s.id, vertex: p.length - 1 } });
        break;
      case "circle":
        if (s.center && s.radius_mm) {
          t.centers.push({ p: s.center, ref: { stroke_id: s.id, vertex: 0 } });
          t.circles.push({ c: s.center, r: s.radius_mm });
        }
        break;
      case "arc": {
        t.endpoints.push({ p: p[0], ref: { stroke_id: s.id, vertex: 0 } }, { p: p[2] ?? p[0], ref: { stroke_id: s.id, vertex: 2 } });
        const cc = p.length === 3 ? circumcircle(p[0], p[1], p[2]) : null;
        if (cc) { t.centers.push({ p: cc.c }); t.circles.push(cc); }
        break;
      }
      case "slot":
        p.forEach((q, i) => t.centers.push({ p: q, ref: { stroke_id: s.id, vertex: i } }));
        if (p.length === 2 && s.radius_mm) {
          const n = mul(perp(unit(sub(p[1], p[0]))), s.radius_mm);
          t.edges.push([sub(p[0], n), sub(p[1], n)], [add(p[0], n), add(p[1], n)]);
        }
        break;
      default: {
        p.forEach((q, i) => t.endpoints.push({ p: q, ref: { stroke_id: s.id, vertex: i } }));
        const closed = (s.kind === "rect" || s.closed) && p.length > 2;
        for (let i = 0; i + 1 < p.length; i++) t.edges.push([p[i], p[i + 1]]);
        if (closed) t.edges.push([p[p.length - 1], p[0]]);
      }
    }
  }
  t.midpoints = t.edges.map(([a, b]) => mid(a, b));
  const edges = t.edges.slice(0, MAX_INTERSECT_EDGES);
  for (let i = 0; i < edges.length; i++) {
    for (let j = i + 1; j < edges.length; j++) {
      const x = segSeg(edges[i][0], edges[i][1], edges[j][0], edges[j][1]);
      if (x) t.intersections.push(x);
    }
    for (const c of t.circles) t.intersections.push(...segCircle(edges[i][0], edges[i][1], c.c, c.r));
  }
  for (let i = 0; i < t.circles.length; i++) {
    for (let j = i + 1; j < t.circles.length; j++) t.intersections.push(...circleCircle(t.circles[i].c, t.circles[i].r, t.circles[j].c, t.circles[j].r));
  }
  return t;
}

export interface SnapOptions {
  targets: Targets;
  /** Snap radius in mm (a screen-sized tolerance). */
  radius: number;
  /** Grid step in mm, or null when off. */
  grid: number | null;
  /** The point being drawn from: enables the from-guides. */
  from?: Pt;
  /** Point snaps and inference guides (default on). */
  points?: boolean;
  guides?: boolean;
  /** The guide shown last time: it stays a little stickier (hysteresis), so it doesn't flicker. */
  prev?: Guide | null;
}

interface Line { o: Pt; u: Pt; kind: GuideKind; label: string; fromLine: boolean }

function fromLines(from: Pt, t: Targets): Line[] {
  const out: Line[] = [
    { o: from, u: [1, 0], kind: "horizontal", label: "Horizontal", fromLine: true },
    { o: from, u: [0, 1], kind: "vertical", label: "Vertical", fromLine: true },
    { o: from, u: [Math.SQRT1_2, Math.SQRT1_2], kind: "angle", label: "45°", fromLine: true },
    { o: from, u: [Math.SQRT1_2, -Math.SQRT1_2], kind: "angle", label: "45°", fromLine: true },
  ];
  const taken = out.map((l) => l.u);
  // directions within ~2° of one already offered add nothing but flicker (horizontal wins over a near-horizontal edge)
  const fresh = (u: Pt) => !taken.some((v) => Math.abs(cross(u, v)) < NEAR_PARALLEL);
  for (const [a, b] of t.edges) {
    if (dist(a, b) < 1e-6) continue;
    const d = unit(sub(b, a));
    for (const [u, kind, label] of [[d, "parallel", "Parallel"], [perp(d), "perpendicular", "Perpendicular"]] as const) {
      if (fresh(u)) { out.push({ o: from, u, kind, label, fromLine: true }); taken.push(u); }
    }
  }
  for (const c of t.circles) {
    for (const tp of tangentPoints(from, c.c, c.r)) out.push({ o: from, u: unit(sub(tp, from)), kind: "tangent", label: "Tangent", fromLine: true });
  }
  return out;
}

function alignLines(p: Pt, t: Targets, radius: number): Line[] {
  const pts = [...t.endpoints, ...t.centers].map((e) => e.p);
  const near = pts.filter((q) => Math.abs(q[0] - p[0]) <= radius || Math.abs(q[1] - p[1]) <= radius).slice(0, MAX_ALIGN_POINTS);
  const out: Line[] = [];
  for (const q of near) {
    if (Math.abs(q[0] - p[0]) <= radius) out.push({ o: q, u: [0, 1], kind: "aligned", label: "Aligned", fromLine: false });
    if (Math.abs(q[1] - p[1]) <= radius) out.push({ o: q, u: [1, 0], kind: "aligned", label: "Aligned", fromLine: false });
  }
  return out;
}

const lineDist = (p: Pt, l: Line) => Math.abs(cross(l.u, sub(p, l.o)));
const project = (p: Pt, l: Line): Pt => add(l.o, mul(l.u, dot(sub(p, l.o), l.u)));

function nearestPoint(p: Pt, list: readonly { p: Pt; ref?: SketchRef }[], radius: number): { p: Pt; ref?: SketchRef } | null {
  let best: { p: Pt; ref?: SketchRef } | null = null;
  let bestD = radius;
  for (const e of list) {
    const d = dist(e.p, p);
    if (d <= bestD) { best = e; bestD = d; }
  }
  return best;
}

function onGrid(v: number, step: number): number {
  return round6(Math.round(v / step) * step);
}

function guideOf(l: Line, at: Pt): Guide {
  const span = dot(sub(at, l.o), l.u);
  return { kind: l.kind, a: l.fromLine || Math.abs(span) > 1e-9 ? l.o : at, b: at, label: l.label };
}

export function snap(p: Pt, o: SnapOptions): Snap {
  const t = o.targets;
  const r = o.radius;
  if (o.points !== false) {
    for (const [kind, list] of [["endpoint", t.endpoints], ["center", t.centers], ["intersection", t.intersections.map((q) => ({ p: q }))], ["midpoint", t.midpoints.map((q) => ({ p: q }))]] as const) {
      const hit = nearestPoint(p, list, r);
      if (hit) return { point: [hit.p[0], hit.p[1]], kind, label: POINT_LABEL[kind], guides: [], ...(hit.ref ? { ref: hit.ref } : {}) };
    }
  }
  if (o.guides !== false) {
    const lines = [...(o.from ? fromLines(o.from, t) : []), ...alignLines(p, t, r)];
    const tol = (l: Line) => (l.fromLine && o.from ? Math.max(r, dist(p, o.from) * FROM_ANGLE_TOL) : r);
    const pv = o.prev;
    const pu = pv && dist(pv.a, pv.b) > 1e-9 ? unit(sub(pv.b, pv.a)) : null;
    const isPrev = (l: Line) => !!pv && !!pu && l.kind === pv.kind && Math.abs(cross(l.u, pu)) < 1e-6 && (!l.fromLine || dist(l.o, pv.a) < 1e-9);
    let best: Line | null = null;
    let bestD = Infinity;
    for (const l of lines) {
      const d = lineDist(p, l);
      const sticky = isPrev(l);
      const score = sticky ? d * 0.5 : d;
      if (d <= tol(l) * (sticky ? 1.5 : 1) && score < bestD - 1e-9) { best = l; bestD = score; }
    }
    if (best) {
      let q = project(p, best);
      // a second guide crossing near the pointer: snap to the crossing
      let second: { l: Line; x: Pt } | null = null;
      for (const l of lines) {
        if (l === best || Math.abs(cross(l.u, best.u)) < 1e-6) continue;
        const x = lineLine(best.o, best.u, l.o, l.u);
        if (x && dist(x, q) <= r && (!second || dist(x, q) < dist(second.x, q))) second = { l, x };
      }
      if (second) {
        q = second.x;
        return { point: q, kind: "guide", label: `${best.label} · ${second.l.label}`, guides: [guideOf(best, q), guideOf(second.l, q)] };
      }
      if (o.grid) {
        if (best.fromLine) {
          const span = dot(sub(q, best.o), best.u);
          q = add(best.o, mul(best.u, Math.sign(span) * Math.max(o.grid, onGrid(Math.abs(span), o.grid))));
        } else if (best.u[0] === 0) q = [best.o[0], onGrid(q[1], o.grid)];
        else q = [onGrid(q[0], o.grid), best.o[1]];
      }
      return { point: [round6(q[0]), round6(q[1])], kind: "guide", label: best.label, guides: [guideOf(best, q)] };
    }
  }
  if (o.points !== false) {
    let on: Pt | null = null;
    let onD = r;
    for (const [a, b] of t.edges) {
      const n = nearestOnSeg(p, a, b).point;
      const d = dist(n, p);
      if (d <= onD) { on = n; onD = d; }
    }
    for (const c of t.circles) {
      const d = Math.abs(dist(p, c.c) - c.r);
      if (d <= onD && dist(p, c.c) > 1e-9) { on = add(c.c, mul(unit(sub(p, c.c)), c.r)); onD = d; }
    }
    if (on) return { point: on, kind: "on", label: POINT_LABEL.on, guides: [] };
  }
  if (o.grid) return { point: [onGrid(p[0], o.grid), onGrid(p[1], o.grid)], kind: "grid", label: null, guides: [] };
  return { point: [p[0], p[1]], kind: null, label: null, guides: [] };
}
