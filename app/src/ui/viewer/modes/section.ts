// Section plane with closed, colored caps. Every part's faces and edges are clipped by one shared
// plane; the cut is then filled per part by stencil parity: the part's back faces add one, its front
// faces take one away (depth ignored), so a pixel ends non-zero exactly where the plane lies inside
// that part's solid. A plane quad drawn only there is the cap, in the part's color with drafting
// section hatching, depth-tested like any face. The stencil is cleared after each part's cap so
// parts never fill each other's holes. Needs a stencil buffer and local clipping (scene.ts).
import * as THREE from "three";
import { meshColors } from "../loadPart";
import type { ModeHost, ShownPart } from "../scene";
import type { SectionMode, Vec3 } from "./state";

/** Range of n·p over the box's corners (n unit), i.e. where the plane can go. */
export function sectionRange(box: THREE.Box3, n: THREE.Vector3): [number, number] {
  let lo = Infinity; let hi = -Infinity;
  const v = new THREE.Vector3();
  for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) {
    const d = n.dot(v.set(x, y, z));
    lo = Math.min(lo, d); hi = Math.max(hi, d);
  }
  return [lo, hi];
}

export function unit(v: Vec3): THREE.Vector3 {
  const n = new THREE.Vector3(...v);
  return n.lengthSq() < 1e-12 ? new THREE.Vector3(1, 0, 0) : n.normalize();
}

/** The section's resolved plane distance (n unit): its own `at`, else the middle of `box`. */
export function sectionAt(s: SectionMode, box: THREE.Box3 | null): number {
  if (s.at !== null) return s.at;
  if (!box || box.isEmpty()) return 0;
  return unit(s.normal).dot(box.getCenter(new THREE.Vector3()));
}

/** A clip plane that keeps n·p <= at (three keeps n'·p + c >= 0). */
export function clipPlane(n: THREE.Vector3, at: number, into = new THREE.Plane()): THREE.Plane {
  return into.set(n.clone().negate(), at);
}

const CAP_HATCH_PX = 64;
/** Hatch tiles (one line each) across a cap: about 16 lines over the part's cross-section. */
const HATCH_TILES = 24;
const hatchCache = new Map<string, THREE.Texture | null>();

/** 45° section hatching: lines in the edge color over the cap color (null: no 2D canvas, flat). */
function hatchTexture(bg: string, line: string): THREE.Texture | null {
  const key = `${bg}|${line}`;
  if (hatchCache.has(key)) return hatchCache.get(key) ?? null;
  let tex: THREE.Texture | null = null;
  try {
    const c = typeof document !== "undefined" ? document.createElement("canvas") : null;
    const g = c?.getContext("2d") ?? null;
    if (c && g) {
      c.width = c.height = CAP_HATCH_PX;
      g.fillStyle = bg; g.fillRect(0, 0, CAP_HATCH_PX, CAP_HATCH_PX);
      g.strokeStyle = line; g.globalAlpha = 0.55; g.lineWidth = 3;
      // three diagonals so the pattern tiles seamlessly
      for (const o of [-CAP_HATCH_PX, 0, CAP_HATCH_PX]) { g.beginPath(); g.moveTo(o, CAP_HATCH_PX); g.lineTo(o + CAP_HATCH_PX, 0); g.stroke(); }
      tex = new THREE.CanvasTexture(c);
      tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
      tex.colorSpace = THREE.SRGBColorSpace;
    }
  } catch { tex = null; }
  hatchCache.set(key, tex);
  return tex;
}

/** The cap's color: the part's own color, else the theme's face grey lifted toward the edge grey
 *  (the cut must read apart from the outer faces). */
export function capColor(part: ShownPart, colors = meshColors()): string {
  if (part.color) return part.color;
  return `#${new THREE.Color(colors.face).lerp(new THREE.Color(colors.edge), 0.22).getHexString()}`;
}

interface Entry { part: ShownPart; stencils: THREE.Mesh[]; cap: THREE.Mesh }

function stencilMaterial(side: THREE.Side, op: THREE.StencilOp, plane: THREE.Plane): THREE.MeshBasicMaterial {
  const m = new THREE.MeshBasicMaterial({ side, colorWrite: false, depthWrite: false, depthTest: false });
  m.stencilWrite = true;
  m.stencilFunc = THREE.AlwaysStencilFunc;
  m.stencilFail = op; m.stencilZFail = op; m.stencilZPass = op;
  m.clippingPlanes = [plane];
  return m;
}

const RENDER_BASE = 1000;

export class SectionCaps {
  readonly plane = new THREE.Plane();
  private readonly group = new THREE.Group();
  private entries: Entry[] = [];
  private builtFor: THREE.Object3D[] = [];
  private on = false;

  private readonly host: ModeHost;

  constructor(host: ModeHost) {
    this.host = host;
    this.group.name = "section-caps";
    this.group.userData["modeHelper"] = true;
    host.scene.add(this.group);
  }

  get active(): boolean { return this.on; }
  /** The caps (tests read their colors and placement). */
  caps(): readonly THREE.Mesh[] { return this.entries.map((e) => e.cap); }

  apply(s: SectionMode, parts: readonly ShownPart[], box: THREE.Box3 | null): void {
    this.on = s.on && parts.length > 0;
    for (const p of parts) clipPart(p, this.on ? this.plane : null);
    if (!this.on) { this.group.visible = false; return; }
    this.host.enableLocalClipping();
    const n = unit(s.normal);
    clipPlane(n, sectionAt(s, box), this.plane);
    if (!sameObjects(this.builtFor, parts)) this.rebuild(parts);
    const colors = meshColors();
    const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, 1), n);
    const at = sectionAt(s, box);
    for (const e of this.entries) {
      const b = new THREE.Box3().setFromObject(e.part.scene.object);
      const c = b.getCenter(new THREE.Vector3());
      const size = Math.max(b.getSize(new THREE.Vector3()).length() * 1.5, 1e-3);
      // the cap: on the plane, under the part's middle, facing +n (where you look at the cut from)
      e.cap.position.copy(c).addScaledVector(n, at - n.dot(c));
      e.cap.quaternion.copy(q);
      e.cap.scale.set(size, size, 1);
      const mat = e.cap.material as THREE.MeshBasicMaterial;
      const col = capColor(e.part, colors);
      e.cap.userData["capColor"] = col;
      const hatch = hatchTexture(col, colors.edge);
      if (hatch) {
        // the texture carries the color (cap color + hatch lines); white leaves it as drawn
        if (!mat.map || mat.map.source !== hatch.source) { mat.map?.dispose(); mat.map = hatch.clone(); mat.needsUpdate = true; }
        mat.map.repeat.set(HATCH_TILES, HATCH_TILES);
        mat.color.set("#ffffff");
      } else {
        if (mat.map) { mat.map.dispose(); mat.map = null; mat.needsUpdate = true; }
        mat.color.set(col);
      }
      e.cap.updateMatrixWorld(true);
    }
    this.group.visible = true;
  }

  private rebuild(parts: readonly ShownPart[]): void {
    this.clear();
    this.builtFor = parts.map((p) => p.scene.object);
    parts.forEach((part, k) => {
      const stencils: THREE.Mesh[] = [];
      part.scene.object.traverse((o) => {
        if (!(o instanceof THREE.Mesh)) return;
        for (const [side, op] of [[THREE.BackSide, THREE.IncrementWrapStencilOp], [THREE.FrontSide, THREE.DecrementWrapStencilOp]] as const) {
          const m = new THREE.Mesh(o.geometry, stencilMaterial(side, op, this.plane));
          m.renderOrder = RENDER_BASE + k * 2;
          m.frustumCulled = false;
          m.matrixAutoUpdate = false;
          m.raycast = () => {};
          // follow the source mesh (exploded, moved) exactly: the world matrix is taken at draw time
          m.onBeforeRender = () => { m.matrixWorld.copy(o.matrixWorld); };
          m.userData["modeHelper"] = true;
          stencils.push(m);
          this.group.add(m);
        }
      });
      const capMat = new THREE.MeshBasicMaterial({ side: THREE.DoubleSide });
      capMat.stencilWrite = true;
      capMat.stencilRef = 0;
      capMat.stencilFunc = THREE.NotEqualStencilFunc;
      capMat.stencilFail = THREE.ReplaceStencilOp; capMat.stencilZFail = THREE.ReplaceStencilOp; capMat.stencilZPass = THREE.ReplaceStencilOp;
      const cap = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), capMat);
      cap.renderOrder = RENDER_BASE + k * 2 + 1;
      cap.raycast = () => {};
      cap.userData["modeHelper"] = true;
      cap.userData["partId"] = part.id;
      cap.name = `cap:${part.id}`;
      // the next part counts from zero
      cap.onAfterRender = (r) => { r.clearStencil(); };
      this.group.add(cap);
      this.entries.push({ part, stencils, cap });
    });
  }

  private clear(): void {
    for (const e of this.entries) {
      for (const s of e.stencils) (s.material as THREE.Material).dispose();   // geometry is the part's
      e.cap.geometry.dispose();
      const m = e.cap.material as THREE.MeshBasicMaterial;
      m.map?.dispose();
      m.dispose();
    }
    this.group.clear();
    this.entries = [];
    this.builtFor = [];
  }

  dispose(): void {
    this.clear();
    this.group.removeFromParent();
  }
}

function sameObjects(a: readonly THREE.Object3D[], parts: readonly ShownPart[]): boolean {
  return a.length === parts.length && parts.every((p, i) => p.scene.object === a[i]);
}

/** Clip (or un-clip) a part's faces and edges. */
function clipPart(p: ShownPart, plane: THREE.Plane | null): void {
  p.scene.object.traverse((o) => {
    if (!(o instanceof THREE.Mesh) && !(o instanceof THREE.LineSegments)) return;
    const m = o.material as THREE.Material;
    const want = plane ? [plane] : null;
    const has = m.clippingPlanes?.length ? m.clippingPlanes : null;
    if ((has?.[0] ?? null) === (want?.[0] ?? null)) return;
    m.clippingPlanes = want;
    m.needsUpdate = true;
  });
}
