// One viewer's view modes: X-ray, section caps, explode and an isolated subset, applied together
// and re-applied after every restyle of the viewer (new parts, shaded / wire, theme), since a part
// that reloads comes with fresh materials.
import * as THREE from "three";
import type { ModeHost, ShownPart } from "../scene";
import { liveBox } from "./camera";
import { Exploder } from "./explode";
import { realClock, type Clock } from "./motion";
import { SectionCaps } from "./section";
import { MODES_OFF, type ModesState } from "./state";
import { applyXray } from "./xray";

export class ModesController {
  readonly caps: SectionCaps;
  readonly exploder: Exploder;
  private state: ModesState = MODES_OFF;
  private readonly off: () => void;
  private applying = false;
  /** The camera zoom from before the parts moved apart (null: assembled). */
  private baseZoom: number | null = null;

  readonly host: ModeHost;

  constructor(host: ModeHost, clock: Clock = realClock) {
    this.host = host;
    this.caps = new SectionCaps(host);
    this.exploder = new Exploder(host, clock);
    this.exploder.onChange = () => { this.followZoom(); if (!this.applying) this.applySection(); };
    this.off = host.onRestyle(() => this.reapply());
  }

  get modes(): ModesState { return this.state; }

  /** Apply `next`. The explode glides from where it is when `animate` (and motion is allowed). */
  set(next: ModesState, opts: { animate?: boolean } = {}): void {
    const prev = this.state;
    this.state = next;
    this.applying = true;
    try {
      const parts = this.host.parts();
      applyXray(parts, next);
      this.exploder.sync(parts);
      if (prev.explode !== next.explode || this.exploder.value !== next.explode) this.exploder.set(next.explode, opts.animate ?? false);
    } finally { this.applying = false; }
    this.applySection();
    this.host.requestRender();
  }

  private reapply(): void {
    this.applying = true;
    try {
      const parts = this.host.parts();
      applyXray(parts, this.state);
      this.exploder.sync(parts);
    } finally { this.applying = false; }
    this.applySection();
  }

  private applySection(): void {
    const parts = this.host.parts();
    // the plane's default (through the middle) is taken from the assembled layout, so exploding
    // does not slide the cut
    this.caps.apply(this.state.section, parts, restBox(parts));
    this.host.requestRender();
  }

  /** While exploded, zoom out just enough to keep every part in view (with the glide); back at the
   *  user's own zoom once assembled again. The camera's direction and target never change. */
  private followZoom(): void {
    const { camera } = this.host;
    if (this.exploder.value <= 0) {
      if (this.baseZoom !== null) { camera.zoom = this.baseZoom; camera.updateProjectionMatrix(); this.baseZoom = null; }
      return;
    }
    if (this.baseZoom === null) this.baseZoom = camera.zoom;
    const box = liveBox(this.host.parts());
    if (!box) return;
    camera.updateMatrixWorld(true);
    let mx = 1e-6; let my = 1e-6;
    const v = new THREE.Vector3();
    for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) {
      v.set(x, y, z).applyMatrix4(camera.matrixWorldInverse);
      mx = Math.max(mx, Math.abs(v.x)); my = Math.max(my, Math.abs(v.y));
    }
    const fits = Math.min((camera.right - camera.left) / 2 / mx, (camera.top - camera.bottom) / 2 / my) / 1.06;
    camera.zoom = Math.min(this.baseZoom, fits);
    camera.updateProjectionMatrix();
  }

  dispose(): void {
    this.off();
    this.exploder.dispose();
    this.caps.dispose();
  }
}

/** Everything shown, assembled (the section plane's range and default are taken from it). */
export function restBox(parts: readonly ShownPart[]): THREE.Box3 | null {
  if (!parts.length) return null;
  const b = parts[0].scene.bbox.clone();
  for (const p of parts) b.union(p.scene.bbox);
  return b;
}
