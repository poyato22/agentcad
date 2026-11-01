// Exploded view. The units are the shown parts (several) or, for one part made of several bodies,
// its bodies. Where each goes is explodePlan's (Plan 10 E4): out along the assembly's structure
// from where it really sits, by a share of its own size, never turned. The plan is re-derived when
// the parts move (a pose, a joint) and glides (eased, one frame at a time) unless reduced motion
// is on.
import * as THREE from "three";
import type { ModeHost, ShownPart } from "../scene";
import { explodePlan, type ExplodeUnit } from "./explodePlan";
import { realClock, tween, type Clock } from "./motion";

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

/** m11: how far the exploded body holding `point` (a rest-pose world point on the part under
 *  `root`) has moved, so a pin on it can follow. Zero when nothing under `root` is moved. */
export function explodeOffset(root: THREE.Object3D, point: THREE.Vector3): THREE.Vector3 {
  let best: { d: number; off: THREE.Vector3 } | null = null;
  root.traverse((o) => {
    const base = o.userData["explodeBase"];
    if (!(base instanceof THREE.Vector3) || o.position.equals(base)) return;
    const now = o.getWorldPosition(new THREE.Vector3());
    const rest = o.parent ? o.parent.localToWorld(base.clone()) : base.clone();
    const off = now.sub(rest);
    // the body's box back at rest: the point on (or nearest to) it picks the body
    const box = new THREE.Box3().setFromObject(o).translate(off.clone().negate());
    const d = box.distanceToPoint(point);
    if (!best || d < best.d) best = { d, off };
  });
  return (best as { d: number; off: THREE.Vector3 } | null)?.off ?? new THREE.Vector3();
}

export const EXPLODE_MS = 450;

export class Exploder {
  /** Per unit, the objects that move with it. */
  private units: Moved[][] = [];
  private builtFor: THREE.Object3D[] = [];
  private builtKey = "";
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

  /** Re-derive the units when the shown objects, their rest poses or the structure changed;
   *  keeps the current amount. */
  sync(parts: readonly ShownPart[]): void {
    const roots = parts.map((p) => p.scene.object);
    const links = this.host.structure?.() ?? null;
    const key = restKey(parts, links);
    if (roots.length === this.builtFor.length && roots.every((o, i) => o === this.builtFor[i]) && key === this.builtKey) return;
    this.reset();
    this.builtFor = roots;
    this.builtKey = key;
    const groups = unitGroups(parts);
    if (groups.length < 2) return;
    for (const g of groups) for (const o of g.objs) {
      if (!(o.userData["explodeBase"] instanceof THREE.Vector3)) o.userData["explodeBase"] = o.position.clone();
      o.position.copy(o.userData["explodeBase"] as THREE.Vector3);
    }
    for (const r of roots) r.updateMatrixWorld(true);
    const units: ExplodeUnit[] = groups.map((g) => {
      const box = new THREE.Box3();
      for (const o of g.objs) box.union(new THREE.Box3().setFromObject(o));
      const l = links?.get(g.id);
      return { id: g.id, box, parent: l?.parent ?? null, axis: l?.axis ? new THREE.Vector3(...l.axis) : null, origin: l?.origin ? new THREE.Vector3(...l.origin) : null };
    });
    const vecs = explodePlan(units);
    this.units = groups.map((g, i) => g.objs.map((o) => {
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
    this.builtKey = "";
  }

  dispose(): void {
    this.cancel?.();
    this.reset();
  }
}

/** What moves: the parts when there are several, else the bodies of the one part. */
function unitGroups(parts: readonly ShownPart[]): { id: string; objs: THREE.Object3D[] }[] {
  if (parts.length > 1) return parts.map((p) => ({ id: p.id, objs: [p.scene.object] }));
  const one = parts[0];
  return one ? bodies(one.scene.object).map((b, i) => ({ id: `${one.id}#${i}`, objs: b })) : [];
}

/** The rest layout a plan was made for: each part's assembled box (rounded) and the links. */
function restKey(parts: readonly ShownPart[], links: ReadonlyMap<string, unknown> | null): string {
  const r = (v: number) => Math.round(v * 100) / 100;
  const boxes = parts.map((p) => { const b = p.scene.bbox; return `${p.id}:${[b.min.x, b.min.y, b.min.z, b.max.x, b.max.y, b.max.z].map(r).join(",")}`; });
  return `${boxes.join(";")}|${links ? JSON.stringify([...links.entries()]) : ""}`;
}
