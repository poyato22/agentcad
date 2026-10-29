// The core's assembly solve (core assembly/solve.py), ported line for line so the app can pose
// parts at 60 fps while a slider, a drag or Animate moves a joint. The core stays the truth: every
// committed value goes through POST /assembly/drive and its answer replaces the local pose.
//
// 4x4 row-major mm transforms. A joint couples frame F_a on instance a with frame F_b on b:
//     T_b = T_a · F_a · M(value) · F_b⁻¹
// F's local Z is the frame axis; M rotates about / translates along that local Z.
import type { Frame, Joint, JointKind, JointValue, Mat4 } from "../../api/assembly";

export type Vec3 = [number, number, number];

export function identity(): Mat4 {
  return [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];
}

export function matmul(a: Mat4, b: Mat4): Mat4 {
  const out = identity();
  for (let i = 0; i < 4; i++) {
    for (let j = 0; j < 4; j++) {
      let s = 0;
      for (let k = 0; k < 4; k++) s += a[i][k] * b[k][j];
      out[i][j] = s;
    }
  }
  return out;
}

export function translate(v: readonly number[]): Mat4 {
  const m = identity();
  m[0][3] = v[0]; m[1][3] = v[1]; m[2][3] = v[2];
  return m;
}

function normalize(v: readonly number[]): Vec3 {
  const n = Math.hypot(v[0], v[1], v[2]);
  if (n < 1e-12) throw new Error("frame axis is zero-length");
  return [v[0] / n, v[1] / n, v[2] / n];
}

export function cross(a: readonly number[], b: readonly number[]): Vec3 {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

/** Rotation by `deg` about `axis` through the origin (Rodrigues). */
export function rotAxis(axis: readonly number[], deg: number): Mat4 {
  const [x, y, z] = normalize(axis);
  const t = (deg * Math.PI) / 180;
  const c = Math.cos(t); const s = Math.sin(t); const c1 = 1 - c;
  return [
    [c + x * x * c1, x * y * c1 - z * s, x * z * c1 + y * s, 0],
    [y * x * c1 + z * s, c + y * y * c1, y * z * c1 - x * s, 0],
    [z * x * c1 - y * s, z * y * c1 + x * s, c + z * z * c1, 0],
    [0, 0, 0, 1],
  ];
}

/** A rigid transform whose local Z is `axis` and translation is `origin`; local X is the core's
 *  deterministic perpendicular (up = +Z unless the axis is near Z, then +X). */
export function frameMatrix(origin: readonly number[], axis: readonly number[]): Mat4 {
  const z = normalize(axis);
  const up = Math.abs(z[2]) < 0.9 ? [0, 0, 1] : [1, 0, 0];
  const x = normalize(cross(up, z));
  const y = cross(z, x);
  return [[x[0], y[0], z[0], origin[0]], [x[1], y[1], z[1], origin[1]], [x[2], y[2], z[2], origin[2]], [0, 0, 0, 1]];
}

export function inverseRigid(m: Mat4): Mat4 {
  const r = [0, 1, 2].map((i) => [0, 1, 2].map((j) => m[j][i]));
  const t = [m[0][3], m[1][3], m[2][3]];
  const nt = [0, 1, 2].map((i) => -(r[i][0] * t[0] + r[i][1] * t[1] + r[i][2] * t[2]));
  return [[r[0][0], r[0][1], r[0][2], nt[0]], [r[1][0], r[1][1], r[1][2], nt[1]], [r[2][0], r[2][1], r[2][2], nt[2]], [0, 0, 0, 1]];
}

export function transformPoint(m: Mat4, p: readonly number[]): Vec3 {
  return [0, 1, 2].map((i) => m[i][0] * p[0] + m[i][1] * p[1] + m[i][2] * p[2] + m[i][3]) as Vec3;
}

export function clampValue(value: JointValue, limits: readonly number[] | null | undefined): JointValue {
  if (!limits) return value;
  const [lo, hi] = limits;
  const c = (v: number) => Math.min(Math.max(v, lo), hi);
  return Array.isArray(value) ? value.map(c) : c(value);
}

function comp(value: JointValue, i: number): number {
  if (Array.isArray(value)) return value[i] ?? 0;
  return i === 0 ? value : 0;
}

/** M(value) in the joint frame. revolute deg about Z · slider mm along Z · cylindrical [deg, mm]
 *  · ball [rx, ry, rz] deg (intrinsic X, then Y, then Z) · fixed identity. */
export function jointMotion(kind: JointKind, value: JointValue): Mat4 {
  const z = [0, 0, 1];
  switch (kind) {
    case "fixed": return identity();
    case "revolute": return rotAxis(z, comp(value, 0));
    case "slider": return translate([0, 0, comp(value, 0)]);
    case "cylindrical": return matmul(translate([0, 0, comp(value, 1)]), rotAxis(z, comp(value, 0)));
    case "ball": return matmul(matmul(rotAxis([1, 0, 0], comp(value, 0)), rotAxis([0, 1, 0], comp(value, 1))), rotAxis(z, comp(value, 2)));
  }
}

/** Face id → (centre, outward normal) in part coordinates, or null when unknown. */
export type FaceResolver = (partId: string, faceId: string) => [Vec3, Vec3] | null;

export function resolveFrame(partId: string, frame: Frame, resolveFace?: FaceResolver): Mat4 | null {
  let origin = frame.origin ?? null;
  let axis = frame.axis ?? null;
  if ((!origin || !axis) && frame.face_id) {
    const r = resolveFace?.(partId, frame.face_id) ?? null;
    if (!r) return null;
    [origin, axis] = r;
  }
  if (!origin || !axis) return null;
  return frameMatrix(origin, axis);
}

export interface SolveInput {
  instances: readonly { id: string; part_id: string; transform: Mat4; fixed: boolean }[];
  joints: readonly Pick<Joint, "id" | "kind" | "a" | "b" | "value" | "limits">[];
}

/** How a placed instance got its pose: through `joint`, from the already-placed side `from`. */
export interface Placement { joint: string; from: "a" | "b" }

export interface SolveResult {
  transforms: Record<string, Mat4>;
  applied: Record<string, JointValue>;
  placedBy: Record<string, Placement>;
  /** Joints that could not be applied here (a face frame with no geometry known, a loop). */
  unresolved: string[];
  /** Instances a joint should place but couldn't (its frame names a face the part doesn't have):
   *  instance id → that joint. The core would place them; here they are locked, never free. */
  blocked: Record<string, string>;
}

/**
 * Same tree solve as the core. `stored` transforms are the anchors' poses (fixed instances, then
 * the first instance of each component with no fixed member); everything else follows the joints.
 * Unlike the core it never throws: a joint it can't resolve is skipped and listed in `unresolved`.
 */
export function solve(input: SolveInput, resolveFace?: FaceResolver): SolveResult {
  const byId = new Map(input.instances.map((i) => [i.id, i]));
  const parent = new Map<string, string>();
  const find = (x: string): string => {
    let r = x;
    while (parent.has(r) && parent.get(r) !== r) r = parent.get(r) as string;
    return r;
  };
  const unresolved: string[] = [];
  const blocked: Record<string, string> = {};
  const tree: SolveInput["joints"][number][] = [];
  for (const j of input.joints) {
    const a = j.a.instance_id; const b = j.b.instance_id;
    if (!byId.has(a) || !byId.has(b)) { unresolved.push(j.id); continue; }
    const ra = find(a); const rb = find(b);
    if (ra === rb) { unresolved.push(j.id); continue; }
    parent.set(ra, rb);
    tree.push(j);
  }
  const transforms: Record<string, Mat4> = {};
  const applied: Record<string, JointValue> = {};
  const placedBy: Record<string, Placement> = {};
  const comps = new Map<string, string[]>();
  for (const i of input.instances) {
    const r = find(i.id);
    comps.set(r, [...(comps.get(r) ?? []), i.id]);
    if (i.fixed) transforms[i.id] = i.transform.map((row) => [...row]);
  }
  for (const members of comps.values()) {
    if (!members.some((m) => byId.get(m)?.fixed)) transforms[members[0]] = (byId.get(members[0]) as { transform: Mat4 }).transform.map((row) => [...row]);
  }
  let pending = [...tree];
  let progress = true;
  while (pending.length && progress) {
    progress = false;
    const still: typeof pending = [];
    for (const j of pending) {
      const a = j.a.instance_id; const b = j.b.instance_id;
      const ta = transforms[a]; const tb = transforms[b];
      if (!ta && !tb) { still.push(j); continue; }
      if (ta && tb) continue; // two fixed roots: over-constrained, never move a fixed instance
      const fa = resolveFrame((byId.get(a) as { part_id: string }).part_id, j.a.frame, resolveFace);
      const fb = resolveFrame((byId.get(b) as { part_id: string }).part_id, j.b.frame, resolveFace);
      if (!fa || !fb) { unresolved.push(j.id); blocked[ta ? b : a] = j.id; continue; }
      const value = clampValue(j.value, j.limits);
      applied[j.id] = value;
      const m = jointMotion(j.kind, value);
      if (ta) {
        transforms[b] = matmul(matmul(matmul(ta, fa), m), inverseRigid(fb));
        placedBy[b] = { joint: j.id, from: "a" };
      } else {
        transforms[a] = matmul(matmul(matmul(tb, fb), inverseRigid(m)), inverseRigid(fa));
        placedBy[a] = { joint: j.id, from: "b" };
      }
      progress = true;
    }
    pending = still;
  }
  for (const i of input.instances) transforms[i.id] ??= i.transform.map((row) => [...row]);
  return { transforms, applied, placedBy, unresolved, blocked };
}

/** The world frame a joint moves in: the placed side's world transform times its frame. */
export function jointWorldFrame(input: SolveInput, transforms: Record<string, Mat4>, joint: SolveInput["joints"][number],
  from: "a" | "b", resolveFace?: FaceResolver): Mat4 | null {
  const ref = from === "a" ? joint.a : joint.b;
  const inst = input.instances.find((i) => i.id === ref.instance_id);
  const t = transforms[ref.instance_id];
  if (!inst || !t) return null;
  const f = resolveFrame(inst.part_id, ref.frame, resolveFace);
  return f ? matmul(t, f) : null;
}

/** Which instances a gizmo may move freely: grounded ones never; ones a joint places only
 *  along that joint; the rest (component anchors and free bodies) anywhere. */
export type Mobility = { kind: "grounded" } | { kind: "free" } | { kind: "joint"; joint: string; from: "a" | "b" }
  | { kind: "locked"; joint: string };

export function mobility(inst: { id: string; fixed: boolean }, placedBy: Record<string, Placement>, blocked: Record<string, string> = {}): Mobility {
  if (inst.fixed) return { kind: "grounded" };
  if (blocked[inst.id]) return { kind: "locked", joint: blocked[inst.id] };
  const p = placedBy[inst.id];
  return p ? { kind: "joint", joint: p.joint, from: p.from } : { kind: "free" };
}

/** Whether two transforms are the same pose (within tol mm / tol-ish on rotation terms). */
export function samePose(a: Mat4, b: Mat4, tol = 1e-6): boolean {
  for (let i = 0; i < 3; i++) for (let j = 0; j < 4; j++) if (Math.abs(a[i][j] - b[i][j]) > (j === 3 ? tol * 100 : tol)) return false;
  return true;
}
