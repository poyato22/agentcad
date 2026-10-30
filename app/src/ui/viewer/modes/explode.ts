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

/**
 * A part's meshes grouped into bodies. The exported GLB has one mesh per B-rep face, so faces that
 * share a vertex (a seam edge) belong to one solid; separate solids share none.
 */
export function bodies(root: THREE.Object3D): THREE.Mesh[][] {
  const meshes: THREE.Mesh[] = [];
  root.traverse((o) => { if (o instanceof THREE.Mesh && !o.userData["modeHelper"]) meshes.push(o); });
  if (meshes.length < 2) return meshes.length ? [meshes] : [];
  root.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(root);
  const q = Math.max(box.getSize(new THREE.Vector3()).length() * 1e-5, 1e-9);
  const parent = meshes.map((_, i) => i);
  const find = (i: number): number => { while (parent[i] !== i) { parent[i] = parent[parent[i]]; i = parent[i]; } return i; };
  const seen = new Map<string, number>();
  const v = new THREE.Vector3();
  meshes.forEach((m, i) => {
    const pos = m.geometry.getAttribute("position");
    if (!pos) return;
    for (let k = 0; k < pos.count; k++) {
      v.fromBufferAttribute(pos, k).applyMatrix4(m.matrixWorld);
      const key = `${Math.round(v.x / q)},${Math.round(v.y / q)},${Math.round(v.z / q)}`;
      const j = seen.get(key);
      if (j === undefined) seen.set(key, i);
      else { const a = find(i); const b = find(j); if (a !== b) parent[a] = b; }
    }
  });
  const groups = new Map<number, THREE.Mesh[]>();
  meshes.forEach((m, i) => { const r = find(i); groups.set(r, [...(groups.get(r) ?? []), m]); });
  return [...groups.values()];
}

interface Moved { obj: THREE.Object3D; base: THREE.Vector3; local: THREE.Vector3 }

export const EXPLODE_MS = 450;

export class Exploder {
  /** Per unit, the objects that move with it. */
  private units: Moved[][] = [];
  private builtFor: THREE.Object3D[] = [];
  private amount = 0;
  private cancel: (() => void) | null = null;
  /** Called after the amount changes (the section caps follow the parts). */
  onChange: (() => void) | null = null;

  private readonly host: ModeHost;
  private readonly clock: Clock;
  constructor(host: ModeHost, clock: Clock = realClock) { this.host = host; this.clock = clock; }

  get value(): number { return this.amount; }
  /** How many things move apart (0: nothing to explode). */
  get count(): number { return this.units.length; }

  /** Re-derive the units when the shown objects changed; keeps the current amount. */
  sync(parts: readonly ShownPart[]): void {
    const roots = parts.map((p) => p.scene.object);
    if (roots.length === this.builtFor.length && roots.every((o, i) => o === this.builtFor[i])) return;
    this.reset();
    this.builtFor = roots;
    const groups = unitGroups(parts);
    if (groups.length < 2) return;
    for (const g of groups) for (const o of g) {
      if (!(o.userData["explodeBase"] instanceof THREE.Vector3)) o.userData["explodeBase"] = o.position.clone();
      o.position.copy(o.userData["explodeBase"] as THREE.Vector3);
    }
    for (const r of roots) r.updateMatrixWorld(true);
    const boxes = groups.map((g) => { const b = new THREE.Box3(); for (const o of g) b.union(new THREE.Box3().setFromObject(o)); return b; });
    const overall = new THREE.Box3();
    for (const b of boxes) overall.union(b);
    const vecs = explodeVectors(boxes.map((b) => b.getCenter(new THREE.Vector3())), boxes.map((b) => b.getSize(new THREE.Vector3())), overall);
    this.units = groups.map((g, i) => g.map((o) => {
      // a world offset → the object's parent frame (faces sit under the part's scale and Z-up turn)
      let local = vecs[i].clone();
      const parent = o.parent;
      if (parent && !(parent instanceof THREE.Scene)) {
        const p0 = o.getWorldPosition(new THREE.Vector3());
        local = parent.worldToLocal(p0.clone().add(vecs[i])).sub(parent.worldToLocal(p0.clone()));
      }
      return { obj: o, base: (o.userData["explodeBase"] as THREE.Vector3).clone(), local };
    }));
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
    for (const u of this.units) for (const m of u) { m.obj.position.copy(m.base).addScaledVector(m.local, f); m.obj.updateMatrixWorld(true); }
    this.onChange?.();
    this.host.requestRender();
  }

  private reset(): void {
    for (const u of this.units) for (const m of u) { m.obj.position.copy(m.base); m.obj.updateMatrixWorld(true); }
    this.units = [];
    this.builtFor = [];
  }

  dispose(): void {
    this.cancel?.();
    this.reset();
  }
}

/** What moves: the parts when there are several, else the bodies of the one part. */
function unitGroups(parts: readonly ShownPart[]): THREE.Object3D[][] {
  if (parts.length > 1) return parts.map((p) => [p.scene.object]);
  const one = parts[0];
  return one ? bodies(one.scene.object) : [];
}
