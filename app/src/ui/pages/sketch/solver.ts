// A lightweight iterative constraint solver (pure): position-based projection, Gauss–Seidel
// style. Each pass nudges the points (and radii) a constraint touches until it holds, weighting
// fixed points (the one being dragged) as immovable; passes repeat until nothing moves. Enough for
// horizontal / vertical / equal / coincident / distance / diameter / radius on lines, polylines,
// rectangles (whose sides are implicitly horizontal / vertical), circles, arcs and slots.
import type { SketchConstraint, SketchConstraintKind, SketchRef, SketchStroke } from "../../../api/types";
import { circumcircle, dist, mid, sub, turn, unit, type Pt } from "./geom";

export type Constraint = SketchConstraint;
export type Ref = SketchRef;

export const SOLVE_TOL = 1e-6;
const MAX_PASSES = 400;
const CIRCLE_SEGMENTS = 64;

/** Strokes the solver can move (freehand is kept as drawn). */
export function solvable(s: SketchStroke): boolean {
  return s.kind !== "freehand";
}

export function edgeCount(s: SketchStroke): number {
  if (s.kind === "circle" || s.kind === "arc" || s.kind === "slot" || s.kind === "freehand") return 0;
  const n = s.points.length;
  return (s.kind === "rect" || s.closed) && n > 2 ? n : Math.max(0, n - 1);
}

export function vertexCount(s: SketchStroke): number {
  return s.kind === "circle" ? 1 : s.points.length;
}

/** Whether the stroke has a radius a diameter / radius / equal constraint can set. */
export function hasRadius(s: SketchStroke): boolean {
  return s.kind === "circle" || s.kind === "arc" || s.kind === "slot";
}

export function refKey(r: Ref): string {
  return r.vertex !== undefined ? `${r.stroke_id}:v${r.vertex}` : r.edge !== undefined ? `${r.stroke_id}:e${r.edge}` : r.stroke_id;
}

/** Why a constraint can't apply to these refs, or null when it can. */
export function checkConstraint(kind: SketchConstraintKind, refs: readonly Ref[], strokes: readonly SketchStroke[]): string | null {
  const by = new Map(strokes.map((s) => [s.id, s]));
  const st = refs.map((r) => by.get(r.stroke_id));
  if (st.some((s) => !s)) return "That geometry is gone.";
  if (st.some((s) => !solvable(s as SketchStroke))) return "Freehand strokes can't be constrained.";
  const isEdge = (i: number) => refs[i]?.edge !== undefined && (refs[i].edge as number) < edgeCount(st[i] as SketchStroke);
  const isVertex = (i: number) => refs[i]?.vertex !== undefined && (refs[i].vertex as number) < vertexCount(st[i] as SketchStroke);
  const isRadial = (i: number) => refs[i]?.vertex === undefined && refs[i]?.edge === undefined && hasRadius(st[i] as SketchStroke);
  switch (kind) {
    case "horizontal": case "vertical":
      return (refs.length === 1 && isEdge(0)) || (refs.length === 2 && isVertex(0) && isVertex(1)) ? null : "Pick a line, or two points.";
    case "coincident":
      return refs.length === 2 && isVertex(0) && isVertex(1) && refKey(refs[0]) !== refKey(refs[1]) ? null : "Pick two points.";
    case "equal":
      if (refs.length !== 2 || refKey(refs[0]) === refKey(refs[1])) return "Pick two lines or two circles.";
      return (isEdge(0) && isEdge(1)) || (isRadial(0) && isRadial(1)) ? null : "Pick two lines or two circles.";
    case "distance":
      return (refs.length === 1 && isEdge(0)) || (refs.length === 2 && isVertex(0) && isVertex(1)) ? null : "Pick a line, or two points.";
    case "diameter": case "radius":
      return refs.length === 1 && isRadial(0) ? null : "Pick a circle, arc or slot.";
  }
}

// ---- working copy

interface Body {
  s: SketchStroke;
  pts: Pt[];          // movable points: a circle's [centre]; others their points
  r: number;          // circle / slot radius (arcs derive theirs)
  fixed: boolean[];
  fixedR: boolean;
}

interface Ctx { bodies: Map<string, Body> }

function bodyOf(s: SketchStroke, fixed: ReadonlySet<string>): Body {
  const pts: Pt[] = s.kind === "circle" ? [[...(s.center ?? s.points[0])] as Pt] : s.points.map((p) => [p[0], p[1]] as Pt);
  return {
    s, pts, r: s.radius_mm ?? 0,
    fixed: pts.map((_, i) => fixed.has(`${s.id}:v${i}`) || fixed.has(s.id)),
    fixedR: fixed.has(`${s.id}:r`) || fixed.has(s.id),
  };
}

function edgePts(b: Body, e: number): [number, number] {
  return [e, (e + 1) % b.pts.length];
}

/** Moves points a and b so that f(a, b) gives the correction; weights 0 for fixed points. */
function share(b1: Body, i: number, b2: Body, j: number, dx: number, dy: number): void {
  const w1 = b1.fixed[i] ? 0 : 1;
  const w2 = b2.fixed[j] ? 0 : 1;
  const w = w1 + w2;
  if (!w) return;
  const p = b1.pts[i];
  const q = b2.pts[j];
  p[0] += (dx * w1) / w; p[1] += (dy * w1) / w;
  q[0] -= (dx * w2) / w; q[1] -= (dy * w2) / w;
}

function setLength(b1: Body, i: number, b2: Body, j: number, target: number): number {
  const p = b1.pts[i];
  const q = b2.pts[j];
  const cur = dist(p, q);
  const u = cur < 1e-12 ? ([1, 0] as Pt) : unit(sub(q, p));
  const diff = cur - target;
  share(b1, i, b2, j, u[0] * diff, u[1] * diff);
  return Math.abs(diff);
}

function arcRadius(b: Body): number {
  const cc = b.pts.length === 3 ? circumcircle(b.pts[0], b.pts[1], b.pts[2]) : null;
  return cc ? cc.r : 0;
}

/** Re-places an arc's middle point so the arc through its ends has radius r (same bulge side). */
function setArcRadius(b: Body, r: number): number {
  const [a, m, c] = b.pts;
  const half = dist(a, c) / 2;
  if (half < 1e-9 || b.fixed[1]) return 0;
  const target = Math.max(r, half);
  const cur = arcRadius(b);
  const md = mid(a, c);
  const n = unit([-(c[1] - a[1]), c[0] - a[0]]);           // left of the chord a → c
  const sideOf = (p: Pt) => Math.sign((p[0] - md[0]) * n[0] + (p[1] - md[1]) * n[1]);
  const side = sideOf(m) || 1;
  const cc = circumcircle(a, m, c);
  const major = !!cc && sideOf(cc.c) === side;               // centre on the bulge's side
  const h = Math.sqrt(Math.max(0, target * target - half * half));
  const sag = major ? target + h : target - h;
  b.pts[1] = [md[0] + n[0] * sag * side, md[1] + n[1] * sag * side];
  return Math.abs(cur - target);
}

function radiusOf(b: Body): number {
  return b.s.kind === "arc" ? arcRadius(b) : b.r;
}

function setRadius(b: Body, r: number): number {
  if (b.s.kind === "arc") return setArcRadius(b, r);
  if (b.fixedR) return Math.abs(b.r - r);
  const d = Math.abs(b.r - r);
  b.r = r;
  return d;
}

type Item = { kind: SketchConstraintKind; refs: Ref[]; value?: number };

/** One projection pass over a constraint; returns how far it was from holding. */
function project(c: Item, x: Ctx): number {
  const b = c.refs.map((r) => x.bodies.get(r.stroke_id) as Body);
  if (b.some((v) => !v)) return 0;
  const pair = (): [Body, number, Body, number] => {
    if (c.refs.length === 1) {
      const [i, j] = edgePts(b[0], c.refs[0].edge as number);
      return [b[0], i, b[0], j];
    }
    return [b[0], c.refs[0].vertex as number, b[1], c.refs[1].vertex as number];
  };
  switch (c.kind) {
    case "horizontal": case "vertical": {
      const [b1, i, b2, j] = pair();
      const axis = c.kind === "horizontal" ? 1 : 0;
      const d = b1.pts[i][axis] - b2.pts[j][axis];
      share(b1, i, b2, j, axis === 0 ? -d : 0, axis === 1 ? -d : 0);
      return Math.abs(d);
    }
    case "coincident": {
      const [b1, i, b2, j] = pair();
      const dx = b2.pts[j][0] - b1.pts[i][0];
      const dy = b2.pts[j][1] - b1.pts[i][1];
      share(b1, i, b2, j, dx, dy);
      return Math.hypot(dx, dy);
    }
    case "distance": {
      const [b1, i, b2, j] = pair();
      return setLength(b1, i, b2, j, c.value as number);
    }
    case "diameter": case "radius": {
      const r = c.kind === "diameter" ? (c.value as number) / 2 : (c.value as number);
      return setRadius(b[0], r);
    }
    case "equal": {
      if (c.refs[0].edge !== undefined) {
        const [i1, j1] = edgePts(b[0], c.refs[0].edge);
        const [i2, j2] = edgePts(b[1], c.refs[1].edge as number);
        const l1 = dist(b[0].pts[i1], b[0].pts[j1]);
        const l2 = dist(b[1].pts[i2], b[1].pts[j2]);
        const free1 = !(b[0].fixed[i1] && b[0].fixed[j1]);
        const free2 = !(b[1].fixed[i2] && b[1].fixed[j2]);
        const m = free1 && free2 ? (l1 + l2) / 2 : free1 ? l2 : l1;
        return setLength(b[0], i1, b[0], j1, m) + setLength(b[1], i2, b[1], j2, m);
      }
      const r1 = radiusOf(b[0]);
      const r2 = radiusOf(b[1]);
      const m = b[0].fixedR ? r1 : b[1].fixedR ? r2 : (r1 + r2) / 2;
      return setRadius(b[0], m) + setRadius(b[1], m);
    }
  }
}

/** Rectangles keep their sides horizontal / vertical (by how each side runs now). */
function implicit(bodies: Iterable<Body>): Item[] {
  const out: Item[] = [];
  for (const b of bodies) {
    if (b.s.kind !== "rect" || b.pts.length !== 4) continue;
    for (let e = 0; e < 4; e++) {
      const [p, q] = [b.pts[e], b.pts[(e + 1) % 4]];
      out.push({ kind: Math.abs(q[0] - p[0]) >= Math.abs(q[1] - p[1]) ? "horizontal" : "vertical", refs: [{ stroke_id: b.s.id, edge: e }] });
    }
  }
  return out;
}

export interface SolveResult {
  strokes: SketchStroke[];
  /** Largest remaining violation (mm). */
  residual: number;
  ok: boolean;
}

/** Solve: `fixed` holds refKey()s of vertices (and stroke ids) to keep where they are. */
export function solve(strokes: readonly SketchStroke[], constraints: readonly Constraint[], fixed: ReadonlySet<string> = new Set()): SolveResult {
  const by = new Map(strokes.map((s) => [s.id, s]));
  const items: Item[] = constraints
    .filter((c) => checkConstraint(c.kind, c.refs, strokes) === null)
    .map((c) => ({ kind: c.kind, refs: c.refs, value: c.value_mm }));
  const touched = new Set(items.flatMap((c) => c.refs.map((r) => r.stroke_id)));
  for (const s of strokes) if (s.kind === "rect") touched.add(s.id);
  const bodies = new Map<string, Body>();
  for (const id of touched) {
    const s = by.get(id);
    if (s && solvable(s)) bodies.set(id, bodyOf(s, fixed));
  }
  const all = [...implicit(bodies.values()), ...items];
  const x: Ctx = { bodies };
  let residual = 0;
  for (let pass = 0; pass < MAX_PASSES; pass++) {
    residual = 0;
    for (const c of all) residual = Math.max(residual, project(c, x));
    if (residual < SOLVE_TOL) break;
  }
  // final residual (after the last pass)
  residual = 0;
  for (const c of all) residual = Math.max(residual, measure(c, x));
  const out = strokes.map((s) => {
    const b = bodies.get(s.id);
    return b ? writeBack(b) : s;
  });
  return { strokes: out, residual, ok: residual < 1e-3 };
}

function measure(c: Item, x: Ctx): number {
  // a projection on a throwaway copy tells how far off it is
  const copy: Ctx = { bodies: new Map() };
  for (const r of c.refs) {
    const b = x.bodies.get(r.stroke_id);
    if (b && !copy.bodies.has(r.stroke_id)) copy.bodies.set(r.stroke_id, { ...b, pts: b.pts.map((p) => [p[0], p[1]] as Pt) });
  }
  return project(c, copy);
}

function circlePoints(c: Pt, r: number): [number, number][] {
  const pts: [number, number][] = [];
  for (let i = 0; i < CIRCLE_SEGMENTS; i++) {
    const a = (2 * Math.PI * i) / CIRCLE_SEGMENTS;
    pts.push([c[0] + r * Math.cos(a), c[1] + r * Math.sin(a)]);
  }
  return pts;
}

function writeBack(b: Body): SketchStroke {
  const s = b.s;
  if (s.kind === "circle") return { ...s, center: [b.pts[0][0], b.pts[0][1]], radius_mm: b.r, points: circlePoints(b.pts[0], b.r) };
  if (s.kind === "slot") return { ...s, points: b.pts.map((p) => [p[0], p[1]]), radius_mm: b.r };
  return { ...s, points: b.pts.map((p) => [p[0], p[1]]) };
}

/** The current value a dimension constraint would hold now (for the badge before it drives). */
export function measured(kind: "distance" | "diameter" | "radius", refs: readonly Ref[], strokes: readonly SketchStroke[]): number | null {
  const by = new Map(strokes.map((s) => [s.id, s]));
  const b = refs.map((r) => by.get(r.stroke_id));
  if (b.some((s) => !s)) return null;
  const body = (s: SketchStroke) => bodyOf(s, new Set());
  if (kind === "distance") {
    if (refs.length === 1 && refs[0].edge !== undefined) {
      const bb = body(b[0] as SketchStroke);
      const [i, j] = edgePts(bb, refs[0].edge);
      return dist(bb.pts[i], bb.pts[j]);
    }
    if (refs.length === 2) return dist(body(b[0] as SketchStroke).pts[refs[0].vertex ?? 0], body(b[1] as SketchStroke).pts[refs[1].vertex ?? 0]);
    return null;
  }
  const r = radiusOf(body(b[0] as SketchStroke));
  return kind === "diameter" ? r * 2 : r;
}
