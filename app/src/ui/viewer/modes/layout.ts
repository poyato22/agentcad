// "Laid out" (Plan 10 E36): every part laid flat on the floor in a grid, knolling style — the
// largest first, each turned onto its largest flat side (its thinnest box axis up, its longest
// along X: cheap, from its own box), a name under each. It glides in and out like the explode
// (position and turn eased together) and is the explode's alternative: one or the other.
import * as THREE from "three";
import type { ModeHost, ShownPart } from "../scene";
import { realClock, tween, type Clock } from "./motion";

export const LAYOUT_MS = 520;

/** A unit's own size once laid flat: x (longest flat side), y, z (its height on the floor). */
export interface LayoutItem { id: string; size: THREE.Vector3 }
export interface LayoutCell { id: string; center: THREE.Vector3; size: THREE.Vector3 }

/** The turn that lays a box of `dims` (its own frame) flat: its thinnest axis up, its longest
 *  along X. A rotation by quarter turns only. */
export function flatTurn(dims: THREE.Vector3): THREE.Quaternion {
  const q = new THREE.Quaternion();
  const ax = [dims.x, dims.y, dims.z];
  const thin = ax.indexOf(Math.min(...ax));
  if (thin === 0) q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI / 2);          // x → z
  else if (thin === 1) q.setFromAxisAngle(new THREE.Vector3(1, 0, 0), -Math.PI / 2);    // y → z
  const after = flatDims(dims, q);
  if (after.y > after.x + 1e-9) q.premultiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), Math.PI / 2));
  return q;
}

/** `dims` turned by `q` (absolute values: a box's extents). */
export function flatDims(dims: THREE.Vector3, q: THREE.Quaternion): THREE.Vector3 {
  const m = new THREE.Matrix4().makeRotationFromQuaternion(q).elements;
  // |R| · dims, column-major elements
  return new THREE.Vector3(
    Math.abs(m[0]) * dims.x + Math.abs(m[4]) * dims.y + Math.abs(m[8]) * dims.z,
    Math.abs(m[1]) * dims.x + Math.abs(m[5]) * dims.y + Math.abs(m[9]) * dims.z,
    Math.abs(m[2]) * dims.x + Math.abs(m[6]) * dims.y + Math.abs(m[10]) * dims.z,
  );
}

/** Rows of cells, largest first, left to right and front to back, centred on `center` (XY) and
 *  standing on `floor` (Z). Pure. */
export function layoutPlan(items: readonly LayoutItem[], center: THREE.Vector3, floor: number): LayoutCell[] {
  if (!items.length) return [];
  const big = (s: THREE.Vector3) => s.x * s.y;
  const order = items.map((_, i) => i).sort((a, b) => big(items[b].size) - big(items[a].size) || items[b].size.length() - items[a].size.length() || a - b);
  const sizes = items.map((i) => i.size.length()).sort((a, b) => a - b);
  const gap = Math.max(sizes[Math.floor(sizes.length / 2)] * 0.3, 1);
  const area = items.reduce((s, i) => s + (i.size.x + gap) * (i.size.y + gap), 0);
  const width = Math.max(Math.sqrt(area) * 1.25, Math.max(...items.map((i) => i.size.x)) + gap);
  // shelf packing: a row fills to `width`, the next starts behind it (+Y is away from the iso camera)
  const rows: { idx: number[]; w: number; d: number }[] = [];
  let row = { idx: [] as number[], w: 0, d: 0 };
  for (const i of order) {
    const s = items[i].size;
    if (row.idx.length && row.w + gap + s.x > width) { rows.push(row); row = { idx: [], w: 0, d: 0 }; }
    row.w += (row.idx.length ? gap : 0) + s.x;
    row.d = Math.max(row.d, s.y);
    row.idx.push(i);
  }
  rows.push(row);
  const depth = rows.reduce((s, r) => s + r.d, 0) + gap * (rows.length - 1);
  const out: LayoutCell[] = new Array(items.length);
  let y = center.y - depth / 2;
  for (const r of rows) {
    let x = center.x - r.w / 2;
    for (const i of r.idx) {
      const s = items[i].size;
      out[i] = { id: items[i].id, center: new THREE.Vector3(x + s.x / 2, y + r.d / 2, floor + s.z / 2), size: s.clone() };
      x += s.x + gap;
    }
    y += r.d + gap;
  }
  return out;
}

interface Placed { obj: THREE.Object3D; base: THREE.Vector3; baseQ: THREE.Quaternion; to: THREE.Vector3; toQ: THREE.Quaternion }

/** The part's own box (its frame: the object's parent's), from its meshes. */
function ownBox(obj: THREE.Object3D): THREE.Box3 {
  obj.updateMatrixWorld(true);
  const inv = obj.parent ? obj.parent.matrixWorld.clone().invert() : new THREE.Matrix4();
  const b = new THREE.Box3();
  obj.traverse((o) => {
    if (!(o instanceof THREE.Mesh) || o.userData["modeHelper"]) return;
    o.geometry.computeBoundingBox();
    if (o.geometry.boundingBox) b.union(o.geometry.boundingBox.clone().applyMatrix4(inv.clone().multiply(o.matrixWorld)));
  });
  return b;
}

export class Layouter {
  private units: Placed[] = [];
  private cells: (LayoutCell & { label: string })[] = [];
  private builtKey = "";
  private amount = 0;
  private cancel: (() => void) | null = null;
  onChange: (() => void) | null = null;
  private readonly host: ModeHost;
  private readonly clock: Clock;
  constructor(host: ModeHost, clock: Clock = realClock) { this.host = host; this.clock = clock; }

  get value(): number { return this.amount; }
  get count(): number { return this.units.length; }
  /** Where each part lies (world, laid out) with its name, for the labels under them. */
  labels(): { id: string; text: string; point: [number, number, number] }[] {
    if (this.amount < 1) return [];
    return this.cells.map((c) => ({ id: c.id, text: c.label, point: [c.center.x, c.center.y - c.size.y / 2, c.center.z - c.size.z / 2] }));
  }

  /** Re-plans when the shown parts (or their rest pose) changed; keeps the amount. Only whole
   *  parts are laid out (a single part's bodies stay as they are). */
  sync(parts: readonly ShownPart[]): void {
    const key = parts.map((p) => { const b = p.scene.bbox; return `${p.id}:${[b.min.x, b.min.y, b.min.z, b.max.x, b.max.y, b.max.z].map((v) => Math.round(v * 100)).join(",")}`; }).join(";");
    if (key === this.builtKey) return;
    this.reset();
    this.builtKey = key;
    if (parts.length < 2) return;
    const objs = parts.map((p) => p.scene.object);
    for (const o of objs) {
      if (!(o.userData["layoutBase"] instanceof THREE.Vector3)) o.userData["layoutBase"] = o.position.clone();
      if (!(o.userData["layoutBaseQ"] instanceof THREE.Quaternion)) o.userData["layoutBaseQ"] = o.quaternion.clone();
      o.position.copy(o.userData["layoutBase"] as THREE.Vector3);
      o.quaternion.copy(o.userData["layoutBaseQ"] as THREE.Quaternion);
      o.updateMatrixWorld(true);
    }
    const overall = new THREE.Box3();
    for (const p of parts) overall.union(p.scene.bbox);
    const center = overall.getCenter(new THREE.Vector3());
    // each part in its own frame, turned flat; then where it lies
    const own = objs.map(ownBox);
    const turns = own.map((b) => flatTurn(b.getSize(new THREE.Vector3())));
    const items = parts.map((p, i) => ({ id: p.id, size: flatDims(own[i].getSize(new THREE.Vector3()), turns[i]) }));
    const cells = layoutPlan(items, center, overall.min.z);
    this.cells = cells.map((c, i) => ({ ...c, label: parts[i].label ?? parts[i].id }));
    this.units = objs.map((o, i) => {
      const parent = o.parent;
      const parentQ = parent ? parent.getWorldQuaternion(new THREE.Quaternion()) : new THREE.Quaternion();
      const baseQ = (o.userData["layoutBaseQ"] as THREE.Quaternion).clone();
      // the world turn wanted: the part's own frame, turned flat → the object's local turn
      const toQ = parentQ.clone().invert().multiply(turns[i]).multiply(baseQ);
      o.quaternion.copy(toQ);
      o.updateMatrixWorld(true);
      const now = new THREE.Box3().setFromObject(o).getCenter(new THREE.Vector3());
      const d = cells[i].center.clone().sub(now);
      const to = o.position.clone();
      if (parent && !(parent instanceof THREE.Scene)) {
        const w0 = o.getWorldPosition(new THREE.Vector3());
        to.add(parent.worldToLocal(w0.clone().add(d)).sub(parent.worldToLocal(w0.clone())));
      } else to.add(d);
      o.quaternion.copy(baseQ);
      o.updateMatrixWorld(true);
      return { obj: o, base: (o.userData["layoutBase"] as THREE.Vector3).clone(), baseQ, to, toQ };
    });
    this.place(this.amount);
  }

  set(f: number, animate: boolean): void {
    const to = Math.min(1, Math.max(0, f));
    this.cancel?.();
    this.cancel = null;
    if (!animate) { this.place(to); return; }
    this.cancel = tween(this.amount, to, LAYOUT_MS, (v) => this.place(v), this.clock);
  }

  private place(f: number): void {
    this.amount = f;
    for (const u of this.units) {
      u.obj.position.lerpVectors(u.base, u.to, f);
      u.obj.quaternion.slerpQuaternions(u.baseQ, u.toQ, f);
      u.obj.updateMatrixWorld(true);
    }
    this.onChange?.();
    this.host.requestRender();
  }

  private reset(): void {
    for (const u of this.units) { u.obj.position.copy(u.base); u.obj.quaternion.copy(u.baseQ); u.obj.updateMatrixWorld(true); }
    this.units = [];
    this.cells = [];
    this.builtKey = "";
  }

  dispose(): void {
    this.cancel?.();
    this.reset();
  }
}
