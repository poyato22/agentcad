// Exploded view. The units are the shown parts (several) or, for one part made of several bodies,
// its bodies. Each moves away from the middle of the whole along the line from that middle to its
// own center; units sharing the middle (concentric parts, a stack on one axis) spread along Z in
// the order they sit. The amount glides (eased, one frame at a time) unless reduced motion is on.
import * as THREE from "three";
import type { ModeHost, ShownPart } from "../scene";
import { realClock, tween, type Clock } from "./motion";

/** World offsets at full explode for units with these centers inside `overall`. Pure. */
export function explodeVectors(centers: readonly THREE.Vector3[], sizes: readonly THREE.Vector3[], overall: THREE.Box3): THREE.Vector3[] {
  const mid = overall.getCenter(new THREE.Vector3());
  const diag = Math.max(overall.getSize(new THREE.Vector3()).length(), 1e-6);
  const out = centers.map((c) => c.clone().sub(mid));
  // radial (the rest): the further out a unit sits, the further it goes, so the gaps grow evenly
  const tight = out.map((d) => d.length() < diag * 0.02);
  const stack = out.map((_, i) => i).filter((i) => tight[i]);
  if (stack.length > 1) {
    // a stack: spread along Z by height order, one unit height apart
    stack.sort((a, b) => (centers[a].z - centers[b].z) || a - b);
    const step = Math.max(...stack.map((i) => sizes[i].z), diag * 0.25);
    stack.forEach((i, rank) => { out[i].set(0, 0, (rank - (stack.length - 1) / 2) * step * 1.2); });
  } else if (stack.length === 1) out[stack[0]].set(0, 0, 0);
  return out;
}

interface Unit { obj: THREE.Object3D; base: THREE.Vector3; local: THREE.Vector3 }

export const EXPLODE_MS = 450;

export class Exploder {
  private units: Unit[] = [];
  private builtFor: THREE.Object3D[] = [];
  private amount = 0;
  private cancel: (() => void) | null = null;
  /** Called after the amount changes (the section caps follow the parts). */
  onChange: (() => void) | null = null;

  constructor(private readonly host: ModeHost, private readonly clock: Clock = realClock) {}

  get value(): number { return this.amount; }

  /** Re-derive the units when the shown objects changed; keeps the current amount. */
  sync(parts: readonly ShownPart[]): void {
    const objs = unitObjects(parts);
    if (objs.length === this.builtFor.length && objs.every((o, i) => o === this.builtFor[i])) return;
    for (const u of this.units) u.obj.position.copy(u.base);
    this.builtFor = objs;
    this.units = [];
    if (objs.length < 2) return;
    for (const o of objs) {
      const b = o.userData["explodeBase"];
      if (!(b instanceof THREE.Vector3)) o.userData["explodeBase"] = o.position.clone();
      o.position.copy(o.userData["explodeBase"] as THREE.Vector3);
    }
    for (const p of parts) p.scene.object.updateMatrixWorld(true);
    const boxes = objs.map((o) => new THREE.Box3().setFromObject(o));
    const overall = new THREE.Box3();
    for (const b of boxes) overall.union(b);
    const vecs = explodeVectors(boxes.map((b) => b.getCenter(new THREE.Vector3())), boxes.map((b) => b.getSize(new THREE.Vector3())), overall);
    objs.forEach((o, i) => {
      const base = (o.userData["explodeBase"] as THREE.Vector3).clone();
      // a world offset → the unit's parent frame (bodies sit under the part's scale and Z-up turn)
      let local = vecs[i].clone();
      const parent = o.parent;
      if (parent && !(parent instanceof THREE.Scene)) {
        parent.updateMatrixWorld(true);
        const p0 = o.getWorldPosition(new THREE.Vector3());
        local = parent.worldToLocal(p0.clone().add(vecs[i])).sub(parent.worldToLocal(p0.clone()));
      }
      this.units.push({ obj: o, base, local });
    });
    this.place(this.amount);
  }

  /** Go to `f` (0…1): glides when `animate`, else lands at once. */
  set(f: number, animate: boolean): void {
    const to = Math.min(1, Math.max(0, f));
    this.cancel?.();
    this.cancel = null;
    if (!animate) { this.place(to); return; }
    this.cancel = tween(this.amount, to, EXPLODE_MS, (v) => this.place(v), this.clock);
  }

  private place(f: number): void {
    this.amount = f;
    for (const u of this.units) u.obj.position.copy(u.base).addScaledVector(u.local, f);
    for (const u of this.units) u.obj.updateMatrixWorld(true);
    this.onChange?.();
    this.host.requestRender();
  }

  dispose(): void {
    this.cancel?.();
    for (const u of this.units) u.obj.position.copy(u.base);
    this.units = [];
    this.builtFor = [];
  }
}

/** What moves: the parts when there are several, else the bodies of the one part. */
function unitObjects(parts: readonly ShownPart[]): THREE.Object3D[] {
  if (parts.length > 1) return parts.map((p) => p.scene.object);
  const one = parts[0];
  if (!one) return [];
  const meshes: THREE.Object3D[] = [];
  one.scene.object.traverse((o) => { if (o instanceof THREE.Mesh && !o.userData["modeHelper"]) meshes.push(o); });
  return meshes;
}
