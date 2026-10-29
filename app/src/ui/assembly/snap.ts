// Gizmo snapping: 1 mm moves and 15° turns (Plan 5 A4). Pure math so it is unit-tested; the scene
// applies it on every TransformControls change.
//   · A move lands each axis that moved on the whole-mm grid (axes the drag didn't touch keep their
//     value, so a part sitting at y = 0.3 isn't nudged when it is dragged along x).
//   · A turn snaps the turn since the drag began to whole 15° steps about the same axis.
//   · Joint values snap absolutely: whole 15° for angles, whole mm for distances.
import { Quaternion, Vector3 } from "three";

export const MOVE_STEP_MM = 1;
export const TURN_STEP_DEG = 15;
/** With snapping off, values still settle on 0.1 mm / 0.1° so the store never holds float noise. */
export const FINE_STEP = 0.1;

const EPS = 1e-6;

export function snapTo(v: number, step: number): number {
  const s = Math.round(v / step) * step;
  // kill -0 and binary noise (0.30000000000000004)
  return Number((s === 0 ? 0 : s).toFixed(6));
}

/** Snap the axes of `cur` that moved away from `start` to the `step` grid. */
export function snapMove(start: readonly number[], cur: readonly number[], step = MOVE_STEP_MM): [number, number, number] {
  return [0, 1, 2].map((i) => (Math.abs(cur[i] - start[i]) > EPS ? snapTo(cur[i], step) : start[i])) as [number, number, number];
}

/** The rotation `start → cur` as (unit axis, signed angle in degrees, in (-180, 180]). */
export function turnBetween(start: Quaternion, cur: Quaternion): { axis: Vector3; deg: number } {
  const d = cur.clone().multiply(start.clone().invert()).normalize();
  if (d.w < 0) d.set(-d.x, -d.y, -d.z, -d.w); // shortest way round
  const s = Math.sqrt(Math.max(0, 1 - d.w * d.w));
  const deg = (2 * Math.acos(Math.min(1, d.w)) * 180) / Math.PI;
  const axis = s < EPS ? new Vector3(0, 0, 1) : new Vector3(d.x / s, d.y / s, d.z / s);
  return { axis, deg };
}

/** `cur` with its turn since `start` rounded to whole `stepDeg` steps (same axis). */
export function snapTurn(start: Quaternion, cur: Quaternion, stepDeg = TURN_STEP_DEG): Quaternion {
  const { axis, deg } = turnBetween(start, cur);
  const snapped = snapTo(deg, stepDeg);
  return new Quaternion().setFromAxisAngle(axis, (snapped * Math.PI) / 180).multiply(start.clone());
}

/** Angle (deg) of a rotation about local Z, unwrapped to the turn nearest `near` (so a drag past
 *  180° keeps counting instead of jumping to -180°). */
export function angleAboutZ(q: Quaternion, near = 0): number {
  const deg = (2 * Math.atan2(q.z, q.w) * 180) / Math.PI;
  return deg + 360 * Math.round((near - deg) / 360);
}

/** A joint value from a drag: snapped (or fine) and clamped to the limits. */
export function snapJointValue(v: number, kind: "angle" | "distance", snap: boolean, limits?: readonly number[] | null): number {
  const step = snap ? (kind === "angle" ? TURN_STEP_DEG : MOVE_STEP_MM) : FINE_STEP;
  let out = snapTo(v, step);
  if (limits) out = Math.min(Math.max(out, limits[0]), limits[1]);
  return out;
}
