// Standard views (front / top / side / iso), azimuth-elevation cameras and the orbit. The CAD
// frame is Z-up: front looks along +Y (from -Y), top looks down -Z, side (right) looks along -X.
import * as THREE from "three";
import { fitOrtho, ISO_AZIMUTH_DEG, ISO_ELEVATION_DEG, type ModeHost, type ShownPart } from "../scene";
import { prefersReducedMotion, realClock, type Clock } from "./motion";

export type ViewName = "front" | "top" | "side" | "iso";
export const VIEW_NAMES: readonly ViewName[] = ["front", "top", "side", "iso"];

/** Unit vector from the target toward the camera, azimuth from -Y toward +X, elevation up from XY. */
export function dirFromAzEl(azDeg: number, elDeg: number): THREE.Vector3 {
  const az = THREE.MathUtils.degToRad(azDeg);
  const el = THREE.MathUtils.degToRad(Math.max(-89.9, Math.min(89.9, elDeg)));
  return new THREE.Vector3(Math.cos(el) * Math.sin(az), -Math.cos(el) * Math.cos(az), Math.sin(el)).normalize();
}

export function viewDirection(v: ViewName): THREE.Vector3 {
  switch (v) {
    case "front": return dirFromAzEl(0, 0);
    case "side": return dirFromAzEl(90, 0);
    // a hair off straight down so "up" (Z) stays defined for the orbit controls
    case "top": return dirFromAzEl(0, 89.9);
    case "iso": return dirFromAzEl(ISO_AZIMUTH_DEG, ISO_ELEVATION_DEG);
  }
}

/** The standard view the camera is in (within 2°), else null. */
export function currentView(camera: THREE.Camera, target: THREE.Vector3): ViewName | null {
  const d = camera.position.clone().sub(target).normalize();
  for (const v of VIEW_NAMES) if (d.angleTo(viewDirection(v)) < THREE.MathUtils.degToRad(2)) return v;
  return null;
}

/** Everything shown, where it is now (exploded parts included). */
export function liveBox(parts: readonly ShownPart[]): THREE.Box3 | null {
  if (!parts.length) return null;
  const b = new THREE.Box3();
  for (const p of parts) { p.scene.object.updateMatrixWorld(true); b.union(new THREE.Box3().setFromObject(p.scene.object)); }
  return b.isEmpty() ? null : b;
}

/** Puts the camera on `dir` and frames `box`; `zoom` > 1 comes closer. */
export function frameView(host: ModeHost, dir: THREE.Vector3, box: THREE.Box3, zoom = 1): void {
  const { camera, controls } = host;
  const center = box.getCenter(new THREE.Vector3());
  camera.position.copy(center).addScaledVector(dir, 100);
  const aspect = Math.max((camera.right - camera.left) / Math.max(camera.top - camera.bottom, 1e-6), 1e-3);
  fitOrtho(camera, box, aspect);
  camera.zoom = Math.min(8, Math.max(0.1, zoom));
  camera.updateProjectionMatrix();
  controls.target.copy(center);
  controls.update();
  host.requestRender();
}

/** A view_spec distance (mm from the target) as an ortho zoom: the distance a 30° lens would need
 *  to frame the box is zoom 1; half that distance is zoom 2. */
export function zoomForDistance(box: THREE.Box3, distance: number): number {
  const r = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1e-3);
  const fitDist = r / Math.tan(THREE.MathUtils.degToRad(15));
  return distance > 0 ? fitDist / distance : 1;
}

/** Turns the camera about the vertical axis through the target: `turns` per `durationS`, looping.
 *  Never starts under reduced motion (the view stays put; `play` still works on request). */
export class Orbiter {
  private id = 0;
  private last = 0;
  private running = false;
  private readonly host: ModeHost;
  private readonly clock: Clock;
  constructor(host: ModeHost, clock: Clock = realClock) { this.host = host; this.clock = clock; }

  get playing(): boolean { return this.running; }

  start(turns: number, durationS: number, opts: { force?: boolean } = {}): boolean {
    this.stop();
    if (!opts.force && prefersReducedMotion()) return false;
    const rate = (2 * Math.PI * (turns || 1)) / Math.max(durationS || 12, 0.5) / 1000;
    this.running = true;
    this.last = this.clock.now();
    const axis = new THREE.Vector3(0, 0, 1);
    const tick = () => {
      if (!this.running) return;
      const now = this.clock.now();
      const dt = Math.min(now - this.last, 100);
      this.last = now;
      const { camera, controls } = this.host;
      const off = camera.position.clone().sub(controls.target).applyAxisAngle(axis, rate * dt);
      camera.position.copy(controls.target).add(off);
      camera.lookAt(controls.target);
      this.host.requestRender();
      this.id = this.clock.frame(tick);
    };
    this.id = this.clock.frame(tick);
    return true;
  }

  stop(): void {
    if (!this.running) return;
    this.running = false;
    this.clock.cancel(this.id);
  }
}
