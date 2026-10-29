// Result fields for the 3D views (no Three.js, no DOM): the channels a result GLB lists in
// asset.extras.mises_sim.channels, per-vertex values of a channel (vector channels as their
// magnitude), the colormap as linear-light vertex colors, face picking (a hit → the part's face id),
// face anchors for markers (presets from the bounding box, ids from metrics.face_list), and the
// wind particles: a velocity field sampled on a section plane, advected and respawned at the inlet.
import type { StudySection } from "../../../../api/studies";
import type { PartFace } from "../../../../api/types";
import { colormap } from "../model";

export type V3 = [number, number, number];

export interface Channel {
  name: string; attribute: string; units: string; kind: "scalar" | "vector"; min: number; max: number;
  freq_hz?: number; factor?: number;
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

/** The channels a result GLB carries (core sim/results.write_glb), in file order. */
export function readChannels(extras: unknown): Channel[] {
  const m = isObj(extras) && isObj(extras.mises_sim) ? extras.mises_sim : null;
  const list = m && Array.isArray(m.channels) ? m.channels : [];
  return list.filter(isObj).filter((c) => typeof c.name === "string" && typeof c.attribute === "string").map((c) => ({
    name: String(c.name), attribute: String(c.attribute), units: typeof c.units === "string" ? c.units : "",
    kind: c.kind === "vector" ? "vector" : "scalar",
    min: typeof c.min === "number" ? c.min : 0, max: typeof c.max === "number" ? c.max : 1,
    ...(typeof c.freq_hz === "number" ? { freq_hz: c.freq_hz } : {}),
    ...(typeof c.factor === "number" ? { factor: c.factor } : {}),
  }));
}

/** Three's GLTFLoader names a custom attribute "_VON_MISES" as "_von_mises". */
export const threeAttribute = (attr: string): string => attr.toLowerCase();

/** Per-vertex values: the scalar itself, or a vector's magnitude. */
export function channelValues(arr: ArrayLike<number>, kind: "scalar" | "vector"): Float32Array {
  if (kind === "scalar") return Float32Array.from(arr);
  const n = Math.floor(arr.length / 3);
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) out[i] = Math.hypot(arr[3 * i], arr[3 * i + 1], arr[3 * i + 2]);
  return out;
}

export function finiteRange(values: ArrayLike<number>): [number, number] {
  let lo = Infinity; let hi = -Infinity;
  for (let i = 0; i < values.length; i++) { const v = values[i]; if (Number.isFinite(v)) { if (v < lo) lo = v; if (v > hi) hi = v; } }
  return Number.isFinite(lo) ? [lo, hi] : [0, 1];
}

/** Linear-light RGB per vertex (glTF COLOR_0 is linear) for values mapped lo..hi, clipped.
 *  `reveal` 0..1 blends from a neutral grey (the results animate in), 1 = the colormap. */
export function colorize(values: ArrayLike<number>, lo: number, hi: number, out?: Float32Array, reveal = 1): Float32Array {
  const c = out ?? new Float32Array(values.length * 3);
  const span = hi - lo;
  const g = 0.35 ** 2.2;
  for (let i = 0; i < values.length; i++) {
    const t = span > 0 ? (values[i] - lo) / span : 0;
    const [r, gg, b] = colormap(t);
    c[3 * i] = g + (r ** 2.2 - g) * reveal;
    c[3 * i + 1] = g + (gg ** 2.2 - g) * reveal;
    c[3 * i + 2] = g + (b ** 2.2 - g) * reveal;
  }
  return c;
}

// ---- faces

export interface Bounds { min: V3; max: V3 }
export interface FaceAnchor { center: V3; normal: V3 }

const PRESET: Record<string, [number, 1 | -1]> = {
  "x-min": [0, -1], "x-max": [0, 1], "y-min": [1, -1], "y-max": [1, 1], "z-min": [2, -1], "z-max": [2, 1], bottom: [2, -1], top: [2, 1],
};

/** Where a marker for `face` goes: a preset is the middle of that bounding-box side; a face id is
 *  its metrics.face_list center and normal. Null for an unknown face. */
export function faceAnchor(face: string, b: Bounds, faces: readonly PartFace[] = []): FaceAnchor | null {
  const p = PRESET[face];
  if (p) {
    const [axis, sign] = p;
    const center: V3 = [(b.min[0] + b.max[0]) / 2, (b.min[1] + b.max[1]) / 2, (b.min[2] + b.max[2]) / 2];
    center[axis] = sign > 0 ? b.max[axis] : b.min[axis];
    const normal: V3 = [0, 0, 0];
    normal[axis] = sign;
    return { center, normal };
  }
  const f = faces.find((x) => x.id === face);
  if (!f) return null;
  return { center: [f.center[0], f.center[1], f.center[2]], normal: f.normal ? [f.normal[0], f.normal[1], f.normal[2]] : [0, 0, 1] };
}

const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];

/** The face of the part under a hit (point and outward normal, part mm): a planar face whose plane
 *  holds the point with the same normal, nearest center first; else the nearest face center. */
export function matchFace(point: V3, normal: V3, faces: readonly PartFace[], tolMm = 0.25): string | null {
  let best: { id: string; d: number } | null = null;
  for (const f of faces) {
    if (!f.normal || f.type !== "plane") continue;
    const n = f.normal as V3;
    if (dot(n, normal) < 0.95) continue;
    if (Math.abs(dot(sub(point, f.center as V3), n)) > tolMm) continue;
    const d = Math.hypot(...sub(point, f.center as V3));
    if (!best || d < best.d) best = { id: f.id, d };
  }
  if (best) return best.id;
  for (const f of faces) {
    const d = Math.hypot(...sub(point, f.center as V3));
    if (!best || d < best.d) best = { id: f.id, d };
  }
  return best?.id ?? null;
}

/** A direction name or vector → a unit vector (null if it has no length). */
export function dirVec(d: string | readonly number[] | null | undefined): V3 | null {
  if (typeof d === "string") {
    const m = /^([+-])([xyz])$/.exec(d);
    if (!m) return null;
    const v: V3 = [0, 0, 0];
    v["xyz".indexOf(m[2])] = m[1] === "+" ? 1 : -1;
    return v;
  }
  if (Array.isArray(d) && d.length === 3) {
    const n = Math.hypot(d[0], d[1], d[2]);
    return n > 1e-12 ? [d[0] / n, d[1] / n, d[2] / n] : null;
  }
  return null;
}

// ---- wind particles on a velocity section

export interface FlowField {
  /** Plane axes in part coordinates (0 = x, 1 = y, 2 = z). */
  axis: number; u: number; v: number; offset: number;
  us: number[]; vs: number[];
  /** In-plane velocity components (m/s), [i * nv + j]; NaN inside the part. */
  vu: Float32Array; vv: Float32Array;
  maxSpeed: number;
}

const AX = { x: 0, y: 1, z: 2 } as const;

/** A velocity section of a wind study (values (3, n_u, n_v), m/s) → an in-plane flow field. */
export function flowField(s: StudySection): FlowField | null {
  const comps = s.values;
  if (!Array.isArray(comps) || comps.length !== 3) return null;
  const us = s.u_values.filter((x): x is number => typeof x === "number");
  const vs = s.v_values.filter((x): x is number => typeof x === "number");
  const nu = us.length; const nv = vs.length;
  if (nu < 2 || nv < 2) return null;
  const u = AX[s.u]; const v = AX[s.v];
  const vu = new Float32Array(nu * nv); const vv = new Float32Array(nu * nv);
  let maxSpeed = 0;
  const cu = comps[u] as unknown; const cv = comps[v] as unknown;
  for (let i = 0; i < nu; i++) {
    for (let j = 0; j < nv; j++) {
      const a = (cu as (number | null)[][])?.[i]?.[j];
      const b = (cv as (number | null)[][])?.[i]?.[j];
      const ok = typeof a === "number" && typeof b === "number";
      vu[i * nv + j] = ok ? a : NaN;
      vv[i * nv + j] = ok ? b : NaN;
      if (ok) maxSpeed = Math.max(maxSpeed, Math.hypot(a, b));
    }
  }
  return { axis: AX[s.axis], u, v, offset: s.offset, us, vs, vu, vv, maxSpeed: maxSpeed || 1 };
}

/** Bilinear in-plane velocity at (pu, pv); null outside the plane or inside the part. */
export function sampleFlow(f: FlowField, pu: number, pv: number): [number, number] | null {
  const { us, vs } = f;
  const nu = us.length; const nv = vs.length;
  if (pu < us[0] || pu > us[nu - 1] || pv < vs[0] || pv > vs[nv - 1]) return null;
  const hu = (us[nu - 1] - us[0]) / (nu - 1); const hv = (vs[nv - 1] - vs[0]) / (nv - 1);
  const gi = Math.min(nu - 2, Math.floor((pu - us[0]) / hu)); const gj = Math.min(nv - 2, Math.floor((pv - vs[0]) / hv));
  const fu = (pu - us[gi]) / hu; const fv = (pv - vs[gj]) / hv;
  let a = 0; let b = 0;
  for (const [di, dj, w] of [[0, 0, (1 - fu) * (1 - fv)], [1, 0, fu * (1 - fv)], [0, 1, (1 - fu) * fv], [1, 1, fu * fv]] as const) {
    const k = (gi + di) * nv + gj + dj;
    const x = f.vu[k]; const y = f.vv[k];
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    a += w * x; b += w * y;
  }
  return [a, b];
}

/** Deterministic pseudo-random numbers (the same frames every run, for screenshots and tests). */
export function rng(seed = 1): () => number {
  let s = seed >>> 0 || 1;
  return () => { s ^= s << 13; s ^= s >>> 17; s ^= s << 5; return ((s >>> 0) % 1_000_000) / 1_000_000; };
}

export interface Particles { pu: Float32Array; pv: Float32Array; age: Float32Array; rand: () => number }

/** n particles spread over the plane (where the flow is defined). */
export function seedParticles(f: FlowField, n: number, seed = 7): Particles {
  const rand = rng(seed);
  const p: Particles = { pu: new Float32Array(n), pv: new Float32Array(n), age: new Float32Array(n), rand };
  for (let i = 0; i < n; i++) respawn(f, p, i, true);
  return p;
}

function respawn(f: FlowField, p: Particles, i: number, anywhere: boolean): void {
  const { us, vs } = f;
  for (let tries = 0; tries < 8; tries++) {
    const u = anywhere ? us[0] + p.rand() * (us[us.length - 1] - us[0]) : us[0] + p.rand() * (us[1] - us[0]) * 2;
    const v = vs[0] + p.rand() * (vs[vs.length - 1] - vs[0]);
    if (sampleFlow(f, u, v)) { p.pu[i] = u; p.pv[i] = v; p.age[i] = p.rand() * 3; return; }
  }
  p.pu[i] = us[0]; p.pv[i] = (vs[0] + vs[vs.length - 1]) / 2; p.age[i] = 0;
}

/** One step: each particle moves with the flow (mm per second of animation = velocity × `scale`);
 *  one that leaves the plane, enters the part or gets old starts again at the inlet. */
export function advect(f: FlowField, p: Particles, dt: number, scale: number, maxAge = 6): void {
  for (let i = 0; i < p.pu.length; i++) {
    const vel = sampleFlow(f, p.pu[i], p.pv[i]);
    p.age[i] += dt;
    if (!vel || p.age[i] > maxAge) { respawn(f, p, i, false); continue; }
    p.pu[i] += vel[0] * scale * dt;
    p.pv[i] += vel[1] * scale * dt;
  }
}

/** Static streamlines (reduced motion): from `seeds` points along the inlet, `steps` steps each. */
export function streamlines(f: FlowField, seeds: number, steps: number, scale: number, dt = 0.05): [number, number][][] {
  const out: [number, number][][] = [];
  const { us, vs } = f;
  for (let s = 0; s < seeds; s++) {
    let u = us[0] + (us[1] - us[0]) * 0.5;
    let v = vs[0] + ((s + 0.5) / seeds) * (vs[vs.length - 1] - vs[0]);
    const line: [number, number][] = [[u, v]];
    for (let k = 0; k < steps; k++) {
      const vel = sampleFlow(f, u, v);
      if (!vel) break;
      u += vel[0] * scale * dt; v += vel[1] * scale * dt;
      line.push([u, v]);
    }
    if (line.length > 2) out.push(line);
  }
  return out;
}

/** A point (u, v) on the plane → part coordinates. */
export function planePoint(f: Pick<FlowField, "axis" | "u" | "v" | "offset">, pu: number, pv: number): V3 {
  const p: V3 = [0, 0, 0];
  p[f.axis] = f.offset; p[f.u] = pu; p[f.v] = pv;
  return p;
}
