// One viewer's view modes: X-ray, section caps, explode (or Laid out) and an isolated subset,
// applied together and re-applied after every restyle of the viewer (new parts, shaded / wire,
// theme), since a part that reloads comes with fresh materials.
import * as THREE from "three";
import { visibleWidth, type ModeHost, type ShownPart } from "../scene";
import { dirFromAzEl, liveBox } from "./camera";
import { Exploder } from "./explode";
import { Layouter } from "./layout";
import { realClock, type Clock } from "./motion";
import { SectionCaps } from "./section";
import { MODES_OFF, type ModesState } from "./state";
import { applyXray } from "./xray";

/** Laid out, the camera looks down at this elevation (top-down-ish: the labels stay readable). */
export const LAYOUT_ELEVATION = 62;

export class ModesController {
  readonly caps: SectionCaps;
  readonly exploder: Exploder;
  readonly layouter: Layouter;
  private state: ModesState = MODES_OFF;
  private readonly off: () => void;
  private applying = false;
  /** The camera zoom from before the parts moved apart (null: assembled). */
  private baseZoom: number | null = null;
  /** The orbit target from before the parts moved apart (null: assembled). */
  private baseTarget: THREE.Vector3 | null = null;
  /** The camera's direction from before the parts were laid out (null: not laid out). */
  private baseDir: THREE.Vector3 | null = null;

  readonly host: ModeHost;

  constructor(host: ModeHost, clock: Clock = realClock) {
    this.host = host;
    this.caps = new SectionCaps(host);
    this.exploder = new Exploder(host, clock);
    this.layouter = new Layouter(host, clock);
    this.exploder.onChange = () => { this.followZoom(); if (!this.applying) this.applySection(); };
    this.layouter.onChange = () => { this.followLayout(); if (!this.applying) this.applySection(); };
    this.off = host.onRestyle(() => this.reapply());
  }

  get modes(): ModesState { return this.state; }

  /** Apply `next`. The explode / layout glides from where it is when `animate` (and motion is
   *  allowed). They never mix: going to one lands the other back at once. */
  set(next: ModesState, opts: { animate?: boolean } = {}): void {
    const prev = this.state;
    this.state = next;
    this.applying = true;
    const layout = next.layout ?? 0;
    try {
      const parts = this.host.parts();
      applyXray(parts, next);
      if (layout > 0) {
        if (this.exploder.value !== 0) this.exploder.set(0, false);
        this.layouter.sync(parts);
        if ((prev.layout ?? 0) !== layout || this.layouter.value !== layout) this.layouter.set(layout, opts.animate ?? false);
      } else {
        if (this.layouter.value !== 0) this.layouter.set(0, (opts.animate ?? false) && next.explode === 0);
        if (this.layouter.value === 0 || next.explode > 0) {
          if (this.layouter.value !== 0) this.layouter.set(0, false);
          this.exploder.sync(parts);
          if (prev.explode !== next.explode || this.exploder.value !== next.explode) this.exploder.set(next.explode, opts.animate ?? false);
        }
      }
    } finally { this.applying = false; }
    this.applySection();
    this.host.requestRender();
  }

  private reapply(): void {
    this.applying = true;
    try {
      const parts = this.host.parts();
      applyXray(parts, this.state);
      // the moved parts' rest pose changed (a joint, an agent's edit): re-plan the one shown
      if ((this.state.layout ?? 0) > 0 || this.layouter.value > 0) this.layouter.sync(parts);
      else this.exploder.sync(parts);
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

  /** While exploded, re-frame (with the glide): the target follows the middle of the exploded
   *  whole and the zoom backs out just enough to keep every part in view; back at the user's own
   *  target and zoom once assembled again. The camera's direction never changes. */
  private followZoom(): void {
    const { camera, controls } = this.host;
    if (this.exploder.value <= 0) {
      if (this.baseZoom !== null) { camera.zoom = this.baseZoom; camera.updateProjectionMatrix(); this.baseZoom = null; }
      if (this.baseTarget !== null) { this.slideTarget(this.baseTarget); this.baseTarget = null; }
      return;
    }
    if (this.baseZoom === null) this.baseZoom = camera.zoom;
    if (this.baseTarget === null) this.baseTarget = controls.target.clone();
    const box = liveBox(this.host.parts());
    if (!box) return;
    // the middle moves as far as the explode has gone (assembled: the user's own target)
    const rest = restBox(this.host.parts());
    const mid = box.getCenter(new THREE.Vector3());
    const shift = rest ? mid.sub(rest.getCenter(new THREE.Vector3())) : new THREE.Vector3();
    this.slideTarget(this.baseTarget.clone().add(shift));
    camera.zoom = Math.min(this.baseZoom, this.fitZoom(box));
    camera.updateProjectionMatrix();
  }

  /** Laid out (with the glide): the camera swings up to look down at the grid from the same side,
   *  re-centred and zoomed to fit it; back to the user's own view once assembled again. */
  private followLayout(): void {
    const { camera, controls } = this.host;
    const f = this.layouter.value;
    if (f <= 0) {
      if (this.baseDir !== null && this.baseTarget !== null) {
        controls.target.copy(this.baseTarget);
        camera.position.copy(this.baseTarget).addScaledVector(this.baseDir, camera.position.distanceTo(controls.target) || 100);
        camera.lookAt(controls.target);
      }
      if (this.baseZoom !== null) camera.zoom = this.baseZoom;
      camera.updateProjectionMatrix();
      this.baseDir = null; this.baseTarget = null; this.baseZoom = null;
      controls.update?.();
      return;
    }
    if (this.baseDir === null) {
      this.baseDir = camera.position.clone().sub(controls.target).normalize();
      this.baseTarget = controls.target.clone();
      this.baseZoom = camera.zoom;
    }
    const box = liveBox(this.host.parts());
    if (!box || !this.baseTarget || this.baseZoom === null) return;
    const az = THREE.MathUtils.radToDeg(Math.atan2(this.baseDir.x, -this.baseDir.y));
    const top = dirFromAzEl(az, LAYOUT_ELEVATION);
    const dir = this.baseDir.clone().lerp(top, f).normalize();
    const target = this.baseTarget.clone().lerp(box.getCenter(new THREE.Vector3()), f);
    const dist = Math.max(camera.position.distanceTo(controls.target), 1);
    controls.target.copy(target);
    camera.position.copy(target).addScaledVector(dir, dist);
    camera.lookAt(target);
    camera.updateMatrixWorld(true);
    camera.zoom = this.baseZoom + (this.fitZoom(box) - this.baseZoom) * f;
    camera.updateProjectionMatrix();
  }

  /** The zoom that just fits `box` around the target, keeping the camera's direction. */
  private fitZoom(box: THREE.Box3): number {
    const { camera, controls } = this.host;
    camera.updateMatrixWorld(true);
    const c = controls.target.clone().applyMatrix4(camera.matrixWorldInverse);
    let mx = 1e-6; let my = 1e-6;
    const v = new THREE.Vector3();
    for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) {
      v.set(x, y, z).applyMatrix4(camera.matrixWorldInverse);
      mx = Math.max(mx, Math.abs(v.x - c.x)); my = Math.max(my, Math.abs(v.y - c.y));
    }
    // only the canvas the floating surfaces leave counts (the frustum spans under them too)
    const W = Math.max(this.host.canvas.clientWidth || this.host.canvas.width || 1, 1);
    const ins = this.host.insets?.() ?? { left: 0, right: 0 };
    const halfW = ((camera.right - camera.left) * (visibleWidth(W, ins.left, ins.right) / W)) / 2;
    return Math.min(halfW / mx, (camera.top - camera.bottom) / 2 / my) / 1.06;
  }

  /** Moves the orbit target (and the camera with it, so the view direction stays). */
  private slideTarget(to: THREE.Vector3): void {
    const { camera, controls } = this.host;
    const d = to.clone().sub(controls.target);
    if (d.lengthSq() < 1e-12) return;
    controls.target.add(d);
    camera.position.add(d);
    camera.updateMatrixWorld(true);
  }

  dispose(): void {
    this.off();
    this.exploder.dispose();
    this.layouter.dispose();
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
