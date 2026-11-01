// Plan 10 E8: readable load and fixture markers, for the setup view and over a result. A load is
// a solid magenta arrow (no stress colormap uses magenta) with a dark outline, its tip on the load
// face, pointing the way the load acts. A fixed face is a dark plate on the face with a bold
// amber hatch and frame and an anchor post: they read on any colormap and either theme.
import * as THREE from "three";
import type { Bounds } from "./fields";

export const LOAD_ACCENT = "#ff3dbb";
export const FIXTURE_ACCENT = "#ffb020";
const OUTLINE = "#111318";

const PRESET_SPAN: Record<string, [number, number]> = {
  "x-min": [1, 2], "x-max": [1, 2], "y-min": [0, 2], "y-max": [0, 2], "z-min": [0, 1], "z-max": [0, 1], bottom: [0, 1], top: [0, 1],
};

/** How wide a fixed face is: a box side's smaller extent for a preset face, else from its area. */
export function faceSpan(face: string, b: Bounds, area: number | null, size: number): number {
  const p = PRESET_SPAN[face];
  if (p) return Math.min(b.max[p[0]] - b.min[p[0]], b.max[p[1]] - b.min[p[1]]);
  return area && area > 0 ? Math.sqrt(area) : size * 0.12;
}

const unlit = (color: string) => new THREE.MeshBasicMaterial({ color, toneMapped: false });

/** A solid arrow from `tail` along `dir` for `len`: a shaft about 2.4% of the part (`size`) thick,
 *  a head in proportion, both outlined by a slightly larger dark shell. */
export function solidArrow(tail: THREE.Vector3, dir: THREE.Vector3, len: number, size: number, color: string): THREE.Group {
  const g = new THREE.Group();
  g.name = "load-arrow";
  const r = Math.max(size * 0.012, 0.05);
  const head = Math.min(len * 0.32, Math.max(r * 6, size * 0.06));
  const shaft = Math.max(len - head, len * 0.4);
  const pad = r * 0.45;
  const add = (geo: THREE.BufferGeometry, mat: THREE.Material, y: number, order: number) => {
    const m = new THREE.Mesh(geo, mat);
    m.position.y = y;
    m.renderOrder = order;
    g.add(m);
  };
  const outline = unlit(OUTLINE);
  outline.side = THREE.BackSide;
  add(new THREE.CylinderGeometry(r + pad, r + pad, shaft + pad, 16), outline, shaft / 2, 4);
  add(new THREE.ConeGeometry(r * 2.8 + pad, head + pad * 1.5, 24), outline, shaft + head / 2, 4);
  add(new THREE.CylinderGeometry(r, r, shaft, 16), unlit(color), shaft / 2, 5);
  add(new THREE.ConeGeometry(r * 2.8, head, 24), unlit(color), shaft + head / 2, 5);
  // the group's +Y is the arrow, its origin the tail
  g.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.clone().normalize());
  g.position.copy(tail);
  return g;
}

/** A fixed face: a dark translucent plate `s` across on the face, a bold frame and 45° hatch. */
export function fixturePlate(center: THREE.Vector3, normal: THREE.Vector3, s: number, color: string): THREE.Group {
  const g = new THREE.Group();
  g.name = "fixture";
  const half = s / 2;
  const t = s * 0.045;
  const plate = new THREE.Mesh(new THREE.PlaneGeometry(s, s),
    new THREE.MeshBasicMaterial({ color: OUTLINE, transparent: true, opacity: 0.7, side: THREE.DoubleSide, depthWrite: false, depthTest: false, toneMapped: false }));
  plate.renderOrder = 4;
  g.add(plate);
  // drawn over the part: a fixed face on the far side still shows where the part is held
  const mat = new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide, depthTest: false, toneMapped: false });
  const stroke = (x: number, y: number, w: number, h: number, rot = 0) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), mat);
    m.position.set(x, y, s * 0.004);
    m.rotation.z = rot;
    m.renderOrder = 5;
    g.add(m);
  };
  stroke(0, half - t / 2, s, t); stroke(0, -half + t / 2, s, t); stroke(half - t / 2, 0, t, s); stroke(-half + t / 2, 0, t, s);
  for (let k = -3; k <= 3; k++) {
    // a diagonal through (c, -c) clipped to the square: its length shrinks away from the middle
    const c = (k * s) / 8;
    const L = (s - 2 * Math.abs(c)) * Math.SQRT2 * 0.9;
    if (L > t * 2) stroke(c, -c, L, t * 0.8, Math.PI / 4);
  }
  // the anchor: a post out of the face (along its normal) to a ground bar with three ground ticks,
  // solid boxes so it reads edge-on too
  const post = s * 0.55;
  const w = Math.max(t * 1.6, s * 0.06);
  const box = (x: number, y: number, z: number, sx: number, sy: number, sz: number) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(sx, sy, sz), mat);
    m.position.set(x, y, z);
    m.renderOrder = 5;
    g.add(m);
  };
  box(0, 0, post / 2, w, w, post);
  box(0, 0, post, s * 0.8, w, w);
  for (const x of [-0.3, 0, 0.3]) box(x * s - s * 0.06, 0, post + s * 0.09, w * 0.8, w * 0.8, s * 0.2);
  g.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), normal.clone().normalize());
  g.position.copy(center).addScaledVector(normal, Math.max(s * 0.01, 0.02));
  return g;
}
