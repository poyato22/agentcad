// The constraint solver (pure): damped least squares (Levenberg–Marquardt) over the free point
// coordinates and radii of the strokes the constraints touch, one connected group of strokes at a
// time. Each constraint is one or two scalar residuals (0 when it holds); the Jacobian is built
// per residual by finite differences over the few variables it touches, and each step solves the
// small dense system (JᵀJ + λ·D) δ = −Jᵀr by Cholesky. Starting from the current geometry, the
// damped steps move things as little as they can, so a drag or a new dimension changes only
// what it must.
//
// Three outcomes: "ok" (everything holds), "not_converged" (still improving when the time budget
// ran out; the residual says how far off) and "conflict" (no further improvement possible while
// something is still off: the constraints contradict each other; `worst` names the one most off).
import type { SketchConstraint, SketchConstraintKind, SketchRef, SketchStroke } from "../../../api/types";
import { circumcircle, dist, type Pt } from "./geom";

export type Constraint = SketchConstraint;
export type Ref = SketchRef;

/** A constraint holds when it is off by less than this (mm). */
export const SOLVE_TOL = 1e-6;
/** Default time budget for one solve (ms). */
export const SOLVE_BUDGET_MS = 150;
const MAX_ITERS = 200;
const CIRCLE_SEGMENTS = 64;

/** Strokes the solver can move (freehand is kept as drawn). */
export function solvable(s: SketchStroke): boolean {
  return s.kind !== "freehand";
}

/** Edges: points[i] → points[i+1]; a rectangle (always) or closed polyline also closes the loop.
 *  Same rule as core sketch._edge_count. */
export function edgeCount(s: SketchStroke): number {
  if (s.kind === "circle" || s.kind === "arc" || s.kind === "slot" || s.kind === "freehand") return 0;
  const n = s.points.length;
  return (s.kind === "rect" || s.closed) && n > 2 ? n : Math.max(0, n - 1);
}

export function vertexCount(s: SketchStroke): number {
  return s.kind === "circle" ? 1 : s.points.length;
}

/** Whether the stroke has a radius a diameter / radius / equal / on-circle constraint can use. */
export function hasRadius(s: SketchStroke): boolean {
  return s.kind === "circle" || s.kind === "arc" || s.kind === "slot";
}

export function refKey(r: Ref): string {
  return r.vertex !== undefined ? `${r.stroke_id}:v${r.vertex}` : r.edge !== undefined ? `${r.stroke_id}:e${r.edge}` : r.stroke_id;
}

/** Whether two vertices of one stroke may be joined: only the two ends of an open chain (closing it). */
function joinableEnds(s: SketchStroke, a: number, b: number): boolean {
  if (s.kind !== "polyline" || s.closed || s.points.length < 3) return false;
  const last = s.points.length - 1;
  return (a === 0 && b === last) || (a === last && b === 0);
}

/** Why a constraint can't apply to these refs, or null when it can. Same rules as core sketch.py. */
export function checkConstraint(kind: SketchConstraintKind, refs: readonly Ref[], strokes: readonly SketchStroke[]): string | null {
  const by = new Map(strokes.map((s) => [s.id, s]));
  const st = refs.map((r) => by.get(r.stroke_id));
  if (st.some((s) => !s)) return "That geometry is gone.";
  if (st.some((s) => !solvable(s as SketchStroke))) return "Freehand strokes can't be constrained.";
  const isEdge = (i: number) => refs[i]?.edge !== undefined && refs[i]?.vertex === undefined && (refs[i].edge as number) < edgeCount(st[i] as SketchStroke);
  const isVertex = (i: number) => refs[i]?.vertex !== undefined && refs[i]?.edge === undefined && (refs[i].vertex as number) < vertexCount(st[i] as SketchStroke);
  const isRadial = (i: number) => refs[i]?.vertex === undefined && refs[i]?.edge === undefined && hasRadius(st[i] as SketchStroke);
  const twoVertices = () => refs.length === 2 && isVertex(0) && isVertex(1) && refKey(refs[0]) !== refKey(refs[1]);
  switch (kind) {
    case "horizontal": case "vertical": case "distance":
      return (refs.length === 1 && isEdge(0)) || twoVertices() ? null : "Pick a line, or two different points.";
    case "distance_x": case "distance_y":
      return twoVertices() ? null : "Pick two different points.";
    case "coincident": {
      if (!twoVertices()) return "Pick two different points.";
      if (refs[0].stroke_id === refs[1].stroke_id && !joinableEnds(st[0] as SketchStroke, refs[0].vertex as number, refs[1].vertex as number)) {
        return "Those points are on the same shape; joining them would collapse it.";
      }
      return null;
    }
    case "equal":
      if (refs.length !== 2 || refKey(refs[0]) === refKey(refs[1])) return "Pick two lines or two circles.";
      return (isEdge(0) && isEdge(1)) || (isRadial(0) && isRadial(1)) ? null : "Pick two lines or two circles.";
    case "diameter": case "radius":
      return refs.length === 1 && isRadial(0) ? null : "Pick a circle, arc or slot.";
    case "point_on_circle":
      return refs.length === 2 && isVertex(0) && isRadial(1) && refs[0].stroke_id !== refs[1].stroke_id ? null : "Pick a point, then a circle.";
  }
}

// ---- variables

interface Body {
  s: SketchStroke;
  /** Variable index of each point coordinate [x, y] (−1 when fixed), and of the radius. */
  pi: [number, number][];
  ri: number;
  /** Current values (for fixed coordinates and the radius when fixed). */
  pts: Pt[];
  r: number;
}

interface Sys {
  bodies: Map<string, Body>;
  x: Float64Array;
}

function bodyPts(s: SketchStroke): Pt[] {
  return s.kind === "circle" ? [[...(s.center ?? s.points[0])] as Pt] : s.points.map((p) => [p[0], p[1]] as Pt);
}

function pt(b: Body, i: number, x: Float64Array): Pt {
  const [ix, iy] = b.pi[i];
  return [ix >= 0 ? x[ix] : b.pts[i][0], iy >= 0 ? x[iy] : b.pts[i][1]];
}

function radius(b: Body, x: Float64Array): number {
  if (b.s.kind === "arc") {
    const cc = b.pts.length === 3 ? circumcircle(pt(b, 0, x), pt(b, 1, x), pt(b, 2, x)) : null;
    return cc ? cc.r : 1e6;
  }
  return b.ri >= 0 ? x[b.ri] : b.r;
}

function centre(b: Body, x: Float64Array): Pt {
  if (b.s.kind === "arc") {
    const cc = circumcircle(pt(b, 0, x), pt(b, 1, x), pt(b, 2, x));
    return cc ? cc.c : pt(b, 1, x);
  }
  return pt(b, 0, x);
}

/** Variables of a ref (for the Jacobian's sparsity). */
function varsOf(b: Body, r: Ref): number[] {
  const out: number[] = [];
  const add = (i: number) => { for (const v of b.pi[i] ?? []) if (v >= 0) out.push(v); };
  if (r.vertex !== undefined) add(r.vertex);
  else if (r.edge !== undefined) { add(r.edge); add((r.edge + 1) % b.pts.length); }
  else {
    if (b.s.kind === "arc") b.pts.forEach((_, i) => add(i));
    else { add(0); if (b.ri >= 0) out.push(b.ri); }
  }
  return out;
}

type Item = { id: string; kind: SketchConstraintKind; refs: Ref[]; value?: number };

function ends(b: Body, r: Ref, x: Float64Array): [Pt, Pt] {
  const e = r.edge as number;
  return [pt(b, e, x), pt(b, (e + 1) % b.pts.length, x)];
}

/** The residuals of one constraint at x (1 or 2 numbers). */
function residuals(c: Item, sys: Sys, x: Float64Array): number[] {
  const b = c.refs.map((r) => sys.bodies.get(r.stroke_id) as Body);
  const pair = (): [Pt, Pt] => (c.refs.length === 1 ? ends(b[0], c.refs[0], x) : [pt(b[0], c.refs[0].vertex as number, x), pt(b[1], c.refs[1].vertex as number, x)]);
  switch (c.kind) {
    case "horizontal": { const [p, q] = pair(); return [q[1] - p[1]]; }
    case "vertical": { const [p, q] = pair(); return [q[0] - p[0]]; }
    case "coincident": { const [p, q] = pair(); return [q[0] - p[0], q[1] - p[1]]; }
    case "distance": { const [p, q] = pair(); return [dist(p, q) - (c.value as number)]; }
    case "distance_x": { const [p, q] = pair(); return [Math.abs(q[0] - p[0]) - (c.value as number)]; }
    case "distance_y": { const [p, q] = pair(); return [Math.abs(q[1] - p[1]) - (c.value as number)]; }
    case "diameter": return [2 * radius(b[0], x) - (c.value as number)];
    case "radius": return [radius(b[0], x) - (c.value as number)];
    case "point_on_circle": return [dist(pt(b[0], c.refs[0].vertex as number, x), centre(b[1], x)) - radius(b[1], x)];
    case "equal":
      if (c.refs[0].edge !== undefined) {
        const [p, q] = ends(b[0], c.refs[0], x);
        const [u, v] = ends(b[1], c.refs[1], x);
        return [dist(p, q) - dist(u, v)];
      }
      return [radius(b[0], x) - radius(b[1], x)];
  }
}

function itemVars(c: Item, sys: Sys): number[] {
  const out = new Set<number>();
  c.refs.forEach((r, k) => {
    const b = sys.bodies.get(r.stroke_id) as Body;
    // a circle's centre matters for point-on-circle; an arc's radius depends on all its points
    for (const v of varsOf(b, k === 1 && c.kind === "point_on_circle" && b.s.kind !== "arc" ? { stroke_id: r.stroke_id } : r)) out.add(v);
  });
  return [...out];
}

/** Rectangles keep their sides horizontal / vertical (by how each side runs at the start). */
function implicit(bodies: Iterable<Body>): Item[] {
  const out: Item[] = [];
  for (const b of bodies) {
    if (b.s.kind !== "rect" || b.pts.length !== 4) continue;
    for (let e = 0; e < 4; e++) {
      const [p, q] = [b.pts[e], b.pts[(e + 1) % 4]];
      out.push({ id: `rect:${b.s.id}:e${e}`, kind: Math.abs(q[0] - p[0]) >= Math.abs(q[1] - p[1]) ? "horizontal" : "vertical", refs: [{ stroke_id: b.s.id, edge: e }] });
    }
  }
  return out;
}

// ---- linear algebra

/** Solves A y = b in place for symmetric positive definite A (n×n, row-major); false if not SPD. */
function cholSolve(A: Float64Array, b: Float64Array, n: number): boolean {
  for (let j = 0; j < n; j++) {
    let d = A[j * n + j];
    for (let k = 0; k < j; k++) d -= A[j * n + k] * A[j * n + k];
    if (!(d > 1e-300)) return false;
    const l = Math.sqrt(d);
    A[j * n + j] = l;
    for (let i = j + 1; i < n; i++) {
      let s = A[i * n + j];
      for (let k = 0; k < j; k++) s -= A[i * n + k] * A[j * n + k];
      A[i * n + j] = s / l;
    }
  }
  for (let i = 0; i < n; i++) {
    let s = b[i];
    for (let k = 0; k < i; k++) s -= A[i * n + k] * b[k];
    b[i] = s / A[i * n + i];
  }
  for (let i = n - 1; i >= 0; i--) {
    let s = b[i];
    for (let k = i + 1; k < n; k++) s -= A[k * n + i] * b[k];
    b[i] = s / A[i * n + i];
  }
  return true;
}

const now = () => (typeof performance !== "undefined" ? performance.now() : Date.now());

type Status = "ok" | "not_converged" | "conflict";

/** LM on one group. Returns the status and the worst residual (and which constraint). */
function lm(items: Item[], sys: Sys, deadline: number): { status: Status; residual: number; worst: string | null } {
  const n = sys.x.length;
  const vars = items.map((c) => itemVars(c, sys));
  const evalAll = (x: Float64Array) => items.map((c) => residuals(c, sys, x));
  const cost = (rs: number[][]) => rs.reduce((a, r) => a + r.reduce((s, v) => s + v * v, 0), 0);
  const worstOf = (rs: number[][]) => {
    let w = 0; let id: string | null = null;
    rs.forEach((r, i) => r.forEach((v) => { if (Math.abs(v) > w) { w = Math.abs(v); id = items[i].id; } }));
    return { w, id };
  };
  let rs = evalAll(sys.x);
  let f = cost(rs);
  let lambda = 1e-3;
  for (let it = 0; it < MAX_ITERS; it++) {
    const { w, id } = worstOf(rs);
    if (w < SOLVE_TOL) return { status: "ok", residual: w, worst: null };
    if (n === 0) return { status: "conflict", residual: w, worst: id };
    if (now() > deadline) return { status: "not_converged", residual: w, worst: id };
    // JᵀJ and Jᵀr, from per-residual finite differences over each constraint's own variables
    const A = new Float64Array(n * n);
    const g = new Float64Array(n);
    items.forEach((c, k) => {
      const vs = vars[k];
      const r0 = rs[k];
      const cols = vs.map((v) => {
        const old = sys.x[v];
        const h = 1e-7 * Math.max(1, Math.abs(old));
        sys.x[v] = old + h;
        const r1 = residuals(c, sys, sys.x);
        sys.x[v] = old;
        return r1.map((val, m) => (val - r0[m]) / h);
      });
      for (let m = 0; m < r0.length; m++) {
        for (let a = 0; a < vs.length; a++) {
          const ja = cols[a][m];
          if (ja === 0) continue;
          g[vs[a]] += ja * r0[m];
          for (let bb = 0; bb < vs.length; bb++) A[vs[a] * n + vs[bb]] += ja * cols[bb][m];
        }
      }
    });
    let gmax = 0;
    for (let i = 0; i < n; i++) gmax = Math.max(gmax, Math.abs(g[i]));
    if (gmax < 1e-12) return { status: "conflict", residual: w, worst: id };
    // try steps with growing damping until the cost drops
    let improved = false;
    for (let tries = 0; tries < 12 && !improved; tries++) {
      const M = new Float64Array(A);
      for (let i = 0; i < n; i++) M[i * n + i] += lambda * (1 + A[i * n + i]);
      const step = new Float64Array(n);
      for (let i = 0; i < n; i++) step[i] = -g[i];
      if (!cholSolve(M, step, n)) { lambda *= 10; continue; }
      const xn = new Float64Array(sys.x);
      for (let i = 0; i < n; i++) xn[i] += step[i];
      const rn = evalAll(xn);
      const fn = cost(rn);
      if (fn < f) {
        const small = Math.sqrt(step.reduce((s, v) => s + v * v, 0)) < 1e-12;
        sys.x = xn; rs = rn;
        const drop = f - fn;
        f = fn;
        lambda = Math.max(lambda / 3, 1e-12);
        improved = true;
        if (small || drop <= 1e-10 * Math.max(fn + drop, 1e-300)) {
          const ww = worstOf(rs);
          return ww.w < SOLVE_TOL ? { status: "ok", residual: ww.w, worst: null } : { status: "conflict", residual: ww.w, worst: ww.id };
        }
      } else lambda *= 4;
    }
    if (!improved) {
      const ww = worstOf(rs);
      return ww.w < SOLVE_TOL ? { status: "ok", residual: ww.w, worst: null } : { status: "conflict", residual: ww.w, worst: ww.id };
    }
  }
  const ww = worstOf(rs);
  return ww.w < SOLVE_TOL ? { status: "ok", residual: ww.w, worst: null } : { status: "not_converged", residual: ww.w, worst: ww.id };
}

export interface SolveResult {
  strokes: SketchStroke[];
  /** Largest remaining violation (mm). */
  residual: number;
  ok: boolean;
  status: Status;
  /** The constraint most off (an id; "rect:<stroke>:e<i>" for a rectangle's own sides), when not ok. */
  worst: string | null;
}

export interface SolveOptions {
  /** refKey()s of vertices (and stroke ids) to keep where they are. */
  fixed?: ReadonlySet<string>;
  budgetMs?: number;
}

/** Solve; `fixed` holds refKey()s of vertices (and stroke ids) to keep where they are. Strokes no
 *  constraint touches are returned as they are. */
export function solve(strokes: readonly SketchStroke[], constraints: readonly Constraint[], fixed: ReadonlySet<string> = new Set(), opts: { budgetMs?: number } = {}): SolveResult {
  const deadline = now() + (opts.budgetMs ?? SOLVE_BUDGET_MS);
  const by = new Map(strokes.map((s) => [s.id, s]));
  const items: Item[] = constraints
    .filter((c) => checkConstraint(c.kind, c.refs, strokes) === null)
    .map((c) => ({ id: c.id, kind: c.kind, refs: c.refs, value: c.value_mm }));
  // groups of strokes joined by constraints (union-find)
  const parent = new Map<string, string>();
  const find = (a: string): string => { let p = a; while (parent.get(p) !== p) p = parent.get(p) as string; parent.set(a, p); return p; };
  const touch = (id: string) => { if (!parent.has(id)) parent.set(id, id); };
  for (const s of strokes) if (s.kind === "rect") touch(s.id);
  for (const c of items) {
    c.refs.forEach((r) => touch(r.stroke_id));
    for (let i = 1; i < c.refs.length; i++) parent.set(find(c.refs[i].stroke_id), find(c.refs[0].stroke_id));
  }
  const groups = new Map<string, string[]>();
  for (const id of parent.keys()) { const g = find(id); groups.set(g, [...(groups.get(g) ?? []), id]); }

  const out = new Map<string, SketchStroke>();
  let status: Status = "ok";
  let residual = 0;
  let worst: string | null = null;
  const rank: Record<Status, number> = { ok: 0, not_converged: 1, conflict: 2 };
  for (const ids of groups.values()) {
    const members = new Set(ids);
    const gItems = items.filter((c) => members.has(c.refs[0].stroke_id));
    const xs: number[] = [];
    const bodies = new Map<string, Body>();
    for (const id of ids) {
      const s = by.get(id);
      if (!s || !solvable(s)) continue;
      const pts = bodyPts(s);
      const isFixed = (i: number) => fixed.has(`${s.id}:v${i}`) || fixed.has(s.id);
      const pi = pts.map((p, i): [number, number] => (isFixed(i) ? [-1, -1] : [xs.push(p[0]) - 1, xs.push(p[1]) - 1]));
      const hasR = s.kind === "circle" || s.kind === "slot";
      const ri = hasR && !fixed.has(s.id) && !fixed.has(`${s.id}:r`) ? xs.push(s.radius_mm ?? 0) - 1 : -1;
      bodies.set(id, { s, pi, ri, pts, r: s.radius_mm ?? 0 });
    }
    const sys: Sys = { bodies, x: Float64Array.from(xs) };
    const all = [...implicit(bodies.values()), ...gItems];
    if (!all.length) continue;
    const res = lm(all, sys, deadline);
    if (rank[res.status] > rank[status] || (res.status === status && res.residual > residual)) worst = res.worst ?? worst;
    if (rank[res.status] > rank[status]) status = res.status;
    residual = Math.max(residual, res.residual);
    for (const b of bodies.values()) out.set(b.s.id, writeBack(b, sys.x));
  }
  return { strokes: strokes.map((s) => out.get(s.id) ?? s), residual, ok: status === "ok", status, worst };
}

function circlePoints(c: Pt, r: number): [number, number][] {
  const pts: [number, number][] = [];
  for (let i = 0; i < CIRCLE_SEGMENTS; i++) {
    const a = (2 * Math.PI * i) / CIRCLE_SEGMENTS;
    pts.push([c[0] + r * Math.cos(a), c[1] + r * Math.sin(a)]);
  }
  return pts;
}

const clean = (v: number) => Math.round(v * 1e9) / 1e9 + 0;

function writeBack(b: Body, x: Float64Array): SketchStroke {
  const s = b.s;
  const pts = b.pts.map((_, i) => pt(b, i, x).map(clean) as [number, number]);
  if (s.kind === "circle") {
    const r = clean(radius(b, x));
    return { ...s, center: pts[0], radius_mm: r, points: circlePoints(pts[0], r) };
  }
  if (s.kind === "slot") return { ...s, points: pts, radius_mm: clean(radius(b, x)) };
  return { ...s, points: pts };
}

