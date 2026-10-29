// Joint frames from picks. A part offers its flat faces (centre + outward normal, from the core's
// metrics face_list) and both ends of each hole (end-cap centre + the outward hole axis). A click
// in 3D snaps to the nearest of those; the keyboard picks the same list from a select.
//
// Ruling (mate convention): joint frames from two picks MATE the faces — b's axis is the negated
// outward normal, so face b lies against face a (a plate set on a plate, not inside it). "Flip"
// aligns the normals instead. The core then poses b so its frame coincides with a's at value 0.
import type { JointKind, JointRef, NewJointBody } from "../../api/assembly";
import type { PartMetrics } from "../../api/types";
import type { Vec3 } from "./kinematics";

export interface FaceOption {
  /** "f2" (face id) · "h0+" / "h0-" (hole 0, end along / against its axis) · "pick" (a raw click). */
  key: string;
  label: string;
  origin: Vec3;
  axis: Vec3;
}

const AXES: [Vec3, string][] = [[[1, 0, 0], "+X"], [[-1, 0, 0], "−X"], [[0, 1, 0], "+Y"], [[0, -1, 0], "−Y"], [[0, 0, 1], "+Z"], [[0, 0, -1], "−Z"]];

const dot = (a: readonly number[], b: readonly number[]) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const sub = (a: readonly number[], b: readonly number[]): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const len = (a: readonly number[]) => Math.hypot(a[0], a[1], a[2]);
const unit = (a: readonly number[]): Vec3 => { const n = len(a) || 1; return [a[0] / n, a[1] / n, a[2] / n]; };
const neg = (a: readonly number[]): Vec3 => [-a[0] + 0, -a[1] + 0, -a[2] + 0];
const r3 = (a: readonly number[]): Vec3 => a.map((v) => Number(v.toFixed(4)) + 0) as Vec3;

/** "+Z" for an axis-aligned direction, else "angled". */
export function directionName(n: readonly number[]): string {
  const u = unit(n);
  for (const [a, name] of AXES) if (dot(a, u) > 0.999) return name;
  return "angled";
}

export function faceOptions(metrics: PartMetrics | null | undefined): FaceOption[] {
  if (!metrics) return [];
  const out: FaceOption[] = [];
  for (const f of metrics.face_list ?? []) {
    if (f.type !== "plane" || !f.normal) continue;
    out.push({ key: f.id, label: `${directionName(f.normal)} face · ${Math.round(f.area)} mm² · ${f.id}`, origin: r3(f.center), axis: r3(unit(f.normal)) });
  }
  metrics.holes.forEach((h, i) => {
    const ax = unit(h.axis);
    for (const s of [1, -1] as const) {
      const dir: Vec3 = s === 1 ? ax : neg(ax);
      const end = h.center.map((c, k) => c + dir[k] * (h.depth_mm / 2));
      out.push({ key: `h${i}${s === 1 ? "+" : "-"}`, label: `Hole ⌀${h.diameter_mm} · ${directionName(dir)} end`, origin: r3(end), axis: r3(dir) });
    }
  });
  return out;
}

/**
 * The option a click lands on. `p` / `n` are the hit point and surface normal in part coordinates;
 * `size` is the part's bbox diagonal (sets the tolerance). A click on a hole's wall picks the hole
 * end nearer the click; a click on a flat face picks the face whose plane it lies on (the nearest
 * centre when several are coplanar). Anything else becomes a raw "pick" at the click.
 */
export function pickOption(options: readonly FaceOption[], metrics: PartMetrics | null | undefined, p: Vec3, n: Vec3, size = 100): FaceOption {
  const nu = unit(n);
  const tol = 0.05 + size * 0.002;
  if (metrics) {
    let best: { i: number; d: number } | null = null;
    metrics.holes.forEach((h, i) => {
      const ax = unit(h.axis);
      if (Math.abs(dot(nu, ax)) > 0.3) return; // not a wall hit
      const v = sub(p, h.center);
      const along = dot(v, ax);
      if (Math.abs(along) > h.depth_mm / 2 + tol) return;
      const radial = len(sub(v, ax.map((c) => c * along)));
      const d = Math.abs(radial - h.diameter_mm / 2);
      if (d < 0.3 + tol && (!best || d < best.d)) best = { i, d };
    });
    if (best) {
      const { i } = best as { i: number };
      const along = dot(sub(p, metrics.holes[i].center), unit(metrics.holes[i].axis));
      const want = `h${i}${along >= 0 ? "+" : "-"}`;
      const hit = options.find((o) => o.key === want);
      if (hit) return hit;
    }
  }
  let face: { o: FaceOption; d: number } | null = null;
  for (const o of options) {
    if (o.key.startsWith("h") || dot(o.axis, nu) < 0.985) continue;
    if (Math.abs(dot(sub(p, o.origin), o.axis)) > tol) continue;
    const d = len(sub(p, o.origin));
    if (!face || d < face.d) face = { o, d };
  }
  if (face) return face.o;
  return { key: "pick", label: `Picked point (${p.map((v) => v.toFixed(1)).join(", ")})`, origin: r3(p), axis: r3(nu) };
}

export interface JointDraft {
  kind: JointKind;
  a: { instanceId: string; option: FaceOption } | null;
  b: { instanceId: string; option: FaceOption } | null;
  /** Align the normals instead of mating the faces. */
  flip: boolean;
  limited: boolean;
  lo: number;
  hi: number;
}

/** Default travel per kind: a hinge swings ±90°, a slide runs 0–20 mm. */
export function defaultLimits(kind: JointKind): [number, number] {
  return kind === "slider" ? [0, 20] : [-90, 90];
}

export function emptyDraft(kind: JointKind = "revolute"): JointDraft {
  const [lo, hi] = defaultLimits(kind);
  return { kind, a: null, b: null, flip: false, limited: kind !== "fixed" && kind !== "ball", lo, hi };
}

/** Why the draft can't be created yet, or null when it can. */
export function draftProblem(d: JointDraft): string | null {
  if (!d.a) return "Pick a face on the first part.";
  if (!d.b) return "Pick a face on the second part.";
  if (d.a.instanceId === d.b.instanceId) return "Pick the second face on a different part.";
  if (d.limited && !(Number.isFinite(d.lo) && Number.isFinite(d.hi))) return "Limits need two numbers.";
  if (d.limited && d.lo > d.hi) return "The lower limit is above the upper one.";
  return null;
}

/** The value a new joint starts at: 0 when 0 is inside its travel, else the nearer limit. */
function startValue(d: JointDraft): number | number[] {
  const z = d.limited ? Math.min(Math.max(0, d.lo), d.hi) : 0;
  if (d.kind === "cylindrical") return [z, z];
  if (d.kind === "ball") return [z, z, z];
  return z;
}

export function jointBody(d: JointDraft): NewJointBody {
  if (draftProblem(d)) throw new Error(draftProblem(d) ?? "incomplete joint");
  const a = d.a as NonNullable<JointDraft["a"]>;
  const b = d.b as NonNullable<JointDraft["b"]>;
  const ref = (instanceId: string, o: FaceOption, mate: boolean): JointRef => ({
    instance_id: instanceId, frame: { origin: o.origin, axis: mate ? neg(o.axis) : o.axis },
  });
  const body: NewJointBody = {
    kind: d.kind, a: ref(a.instanceId, a.option, false), b: ref(b.instanceId, b.option, !d.flip), value: startValue(d),
  };
  if (d.limited && d.kind !== "fixed") body.limits = [d.lo, d.hi];
  return body;
}
