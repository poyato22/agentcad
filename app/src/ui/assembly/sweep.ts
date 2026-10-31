// A presented section sweep (Plan 9 G-D, D18) on the build view: a cut plane normal to a world
// axis sweeps once from `from` to `to` (mm along the axis; either end missing = the assembly's
// box), then the slider is the user's. The view modes keep n·p <= at with n facing the camera
// (you look at the cut), so the axis value maps to `at` through n's sign. Pure: no DOM.
import type * as THREE from "three";
import type { ViewSweep } from "../viewer/modes/viewSpec";
import { sectionRange, unit } from "../viewer/modes/section";
import type { SectionMode, Vec3 } from "../viewer/modes/state";

export const AXIS_NORMAL: Record<ViewSweep["axis"], Vec3> = { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] };

/** Where a reduced-motion sweep (no auto sweep) starts: 45% along, as the core's thumbnail. */
export const SWEEP_REST_AT = 0.45;

/** [from, to] in mm along the axis: the spec's own ends, else the box's extent. */
export function sweepEnds(sweep: ViewSweep, box: THREE.Box3 | null): [number, number] | null {
  const r = box && !box.isEmpty() ? sectionRange(box, unit(AXIS_NORMAL[sweep.axis])) : null;
  const from = sweep.from ?? r?.[0];
  const to = sweep.to ?? r?.[1];
  return from === undefined || to === undefined ? null : [from, to];
}

/** The modes' section for a cut at `value` mm along the axis. `toward`: the plane normal facing
 *  the camera (±the axis). */
export function sweepSection(sweep: ViewSweep, toward: Vec3, value: number): SectionMode {
  const a = AXIS_NORMAL[sweep.axis];
  const sign = toward[0] * a[0] + toward[1] * a[1] + toward[2] * a[2] < 0 ? -1 : 1;
  return { on: true, normal: [a[0] * sign || 0, a[1] * sign || 0, a[2] * sign || 0], at: sign * value || 0 };
}

/** The axis value a section shows (the inverse of sweepSection). */
export function sweepValue(sweep: ViewSweep, s: SectionMode): number {
  const a = AXIS_NORMAL[sweep.axis];
  const n = unit(s.normal);
  const sign = n.x * a[0] + n.y * a[1] + n.z * a[2] < 0 ? -1 : 1;
  return sign * (s.at ?? 0);
}
