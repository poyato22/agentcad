// A click on the model → the face and the edge it names in the part's metrics (face_list /
// edge_list, cad/worker.py). The viewer's world frame is the part's CAD frame in mm (loadPart
// undoes glTF's metres and Y-up), so a hit point compares directly with the metrics.
import type { PartEdge } from "../../api/builds";
import type { PartFace, Vec3 } from "../../api/types";

const sub = (a: Vec3, b: Vec3): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a: Vec3, b: Vec3): number => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const len = (a: Vec3): number => Math.sqrt(dot(a, a));
const add = (a: Vec3, b: Vec3, k = 1): Vec3 => [a[0] + b[0] * k, a[1] + b[1] * k, a[2] + b[2] * k];

/** Cosine of 15°: a hit's surface normal must be this close to the face's own normal. */
const NORMAL_COS = Math.cos((15 * Math.PI) / 180);

/**
 * The face under a hit. Planar faces: same orientation as the hit's surface and the hit on their
 * plane (within `tol`, e.g. 1% of the part's size); among several (coplanar faces), the nearest
 * centre. Curved faces (a hole's wall) have no single normal: the nearest centre among the
 * non-planar faces, when no planar face matches.
 */
export function matchFace(faces: readonly PartFace[], point: Vec3, normal: Vec3, tol: number): PartFace | null {
  let best: PartFace | null = null;
  let bestD = Infinity;
  for (const f of faces) {
    if (f.type !== "plane" || !f.normal) continue;
    if (dot(f.normal, normal) < NORMAL_COS) continue;
    if (Math.abs(dot(sub(point, f.center), f.normal)) > tol) continue;
    const d = len(sub(point, f.center));
    if (d < bestD) { best = f; bestD = d; }
  }
  if (best) return best;
  for (const f of faces) {
    if (f.type === "plane") continue;
    const d = len(sub(point, f.center));
    if (d < bestD) { best = f; bestD = d; }
  }
  return best;
}

/** The point of an edge closest to `point`: a line edge is the segment through its midpoint
 *  along its direction; any other edge is approximated by its midpoint. */
export function closestOnEdge(e: PartEdge, point: Vec3): Vec3 | null {
  if (!e.center) return null;
  if (e.type !== "line" || !e.direction) return e.center;
  const half = e.length / 2;
  const t = Math.max(-half, Math.min(half, dot(sub(point, e.center), e.direction)));
  return add(e.center, e.direction, t);
}

/** Line edge → its two ends (for drawing the pick marker); null for curved edges. */
export function edgeEnds(e: PartEdge): [Vec3, Vec3] | null {
  if (e.type !== "line" || !e.center || !e.direction) return null;
  return [add(e.center, e.direction, -e.length / 2), add(e.center, e.direction, e.length / 2)];
}

/** The edge nearest the hit, if it is within `maxPx` on screen (`toScreen` projects to px). */
export function nearestEdge(edges: readonly PartEdge[], point: Vec3, toScreen: (p: Vec3) => [number, number], maxPx: number): PartEdge | null {
  const [hx, hy] = toScreen(point);
  let best: PartEdge | null = null;
  let bestD = maxPx;
  for (const e of edges) {
    const q = closestOnEdge(e, point);
    if (!q) continue;
    const [x, y] = toScreen(q);
    const d = Math.hypot(x - hx, y - hy);
    if (d <= bestD) { best = e; bestD = d; }
  }
  return best;
}

/** "f5 · plane facing +Z · 960 mm²" */
export function describeFace(f: PartFace): string {
  const n = f.normal;
  let facing = "";
  if (n) {
    const i = [0, 1, 2].reduce((a, b) => (Math.abs(n[b]) > Math.abs(n[a]) ? b : a), 0);
    if (Math.abs(n[i]) > 0.98) facing = ` facing ${n[i] > 0 ? "+" : "−"}${"XYZ"[i]}`;
  }
  return `${f.id} · ${f.type}${facing} · ${Number(f.area.toFixed(1))} mm²`;
}

/** "e0 · line · 5 mm" */
export function describeEdge(e: PartEdge): string {
  return `${e.id} · ${e.type} · ${Number(e.length.toFixed(2))} mm`;
}
