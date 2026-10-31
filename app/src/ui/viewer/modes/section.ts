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
/** Hatch tiles (one line each) across a cap: dense enough to read as "cut" at a glance. */
const HATCH_TILES = 40;
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
      g.strokeStyle = line; g.globalAlpha = 0.9; g.lineWidth = 6;
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

/** The cap's color (Plan 9 D17: the cut must be unmistakable): the part's color (else the face
 *  grey) pushed well toward the edge ink, so it stands far apart from the outer faces in both
 *  themes; its hatch lines go the other way (see hatchLine). */
export function capColor(part: ShownPart, colors = meshColors()): string {
  return `#${new THREE.Color(part.color ?? colors.face).lerp(new THREE.Color(colors.edge), 0.7).getHexString()}`;
}

/** The hatch lines over a cap: the face grey (dark in dark mode, light in light mode), the
 *  strongest contrast against the cap fill. */
export function hatchLine(colors = meshColors()): string {
  return `#${new THREE.Color(colors.face).getHexString()}`;
}

/** Where the plane (n·p = at, n unit) cuts a part's triangles, as world-space line segments
 *  (pairs of points). The outline of the cut. */
export function cutSegments(roots: THREE.Object3D | readonly THREE.Object3D[], n: THREE.Vector3, at: number,
  max = OUTLINE_MAX_TRIANGLES, out: number[] = []): number[] {
  const tri = [new THREE.Vector3(), new THREE.Vector3(), new THREE.Vector3()];
  const d = [0, 0, 0];
  const hits: number[] = [];
  let seen = 0;
  const list: readonly THREE.Object3D[] = roots instanceof THREE.Object3D ? [roots] : roots;
  for (const root of list) {
    root.traverse((o) => {
      if (!(o instanceof THREE.Mesh) || o.userData["modeHelper"] || seen > max) return;
      const pos = o.geometry.getAttribute("position");
      if (!pos) return;
      const idx = o.geometry.getIndex();
      const count = idx ? idx.count : pos.count;
      seen += count / 3;
      if (seen > max) return;                       // too much to outline: caps only
      o.updateMatrixWorld(true);
      const m = o.matrixWorld;
      for (let i = 0; i < count; i += 3) {
        for (let k = 0; k < 3; k++) {
          tri[k].fromBufferAttribute(pos, idx ? idx.getX(i + k) : i + k).applyMatrix4(m);
          d[k] = n.dot(tri[k]) - at;
        }
        hits.length = 0;
        for (let k = 0; k < 3; k++) {
          const j = (k + 1) % 3;
          if ((d[k] < 0) !== (d[j] < 0)) {
            const t = d[k] / (d[k] - d[j]);
            hits.push(tri[k].x + (tri[j].x - tri[k].x) * t, tri[k].y + (tri[j].y - tri[k].y) * t, tri[k].z + (tri[j].z - tri[k].z) * t);
          }
        }
        if (hits.length === 6) for (const v of hits) out.push(v);
      }
    });
  }
  return out;
}

/** The outline is worked out from at most this many triangles across all parts. */
export const OUTLINE_MAX_TRIANGLES = 400_000;
/** While the cut or the parts keep moving, the outline waits this long after the last change. */
export const OUTLINE_SETTLE_MS = 140;

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
  /** The cut's outline: where the plane crosses each part's faces, in the edge ink. */
  private readonly outline: THREE.LineSegments;
  private outlineKey = "";
  private outlineAt = 0;
  private outlineCount = 0;
  private outlineTimer: ReturnType<typeof setTimeout> | null = null;

  private readonly host: ModeHost;

  constructor(host: ModeHost) {
    this.host = host;
    this.group.name = "section-caps";
    this.group.userData["modeHelper"] = true;
    host.scene.add(this.group);
    const mat = new THREE.LineBasicMaterial({ depthFunc: THREE.LessEqualDepth });
    this.outline = new THREE.LineSegments(new THREE.BufferGeometry(), mat);
    this.outline.name = "section-outline";
    this.outline.userData["modeHelper"] = true;
    this.outline.renderOrder = RENDER_BASE + 10_000;
    this.outline.frustumCulled = false;
    this.outline.raycast = () => {};
    this.outline.visible = false;
    host.scene.add(this.outline);
  }

  /** How many outline segments the cut has (tests). */
  outlineSegments(): number { return this.outlineCount; }

  get active(): boolean { return this.on; }
  /** The caps (tests read their colors and placement). */
  caps(): readonly THREE.Mesh[] { return this.entries.map((e) => e.cap); }

  apply(s: SectionMode, parts: readonly ShownPart[], box: THREE.Box3 | null): void {
    this.on = s.on && parts.length > 0;
    for (const p of parts) clipPart(p, this.on ? this.plane : null);
    if (!this.on) { this.group.visible = false; this.outline.visible = false; this.outlineKey = ""; return; }
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
      const hatch = hatchTexture(col, hatchLine(colors));
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
    (this.outline.material as THREE.LineBasicMaterial).color.set(colors.edge);
    this.scheduleOutline(parts, n, at, box);
  }

  /** The outline only when the cut or the parts' poses changed: at once when it's the first change
   *  in a while, else (a slider drag, an animation) hidden until they stop for OUTLINE_SETTLE_MS. */
  private scheduleOutline(parts: readonly ShownPart[], n: THREE.Vector3, at: number, box: THREE.Box3 | null): void {
    const key = [n.x, n.y, n.z, at, ...parts.flatMap((p) => { p.scene.object.updateMatrixWorld(true); return [...p.scene.object.matrixWorld.elements]; })]
      .map((v) => v.toFixed(4)).join(",");
    if (key === this.outlineKey) { this.outline.visible = this.outlineCount > 0; return; }
    this.outlineKey = key;
    const now = Date.now();
    const moving = now - this.outlineAt < OUTLINE_SETTLE_MS;
    this.outlineAt = now;
    if (this.outlineTimer) clearTimeout(this.outlineTimer);
    this.outlineTimer = null;
    if (!moving) { this.buildOutline(parts, n, at, box); return; }
    this.outline.visible = false;
    this.outlineTimer = setTimeout(() => {
      this.outlineTimer = null;
      if (!this.on) return;
      this.buildOutline(parts, n, at, box);
      this.host.requestRender();
    }, OUTLINE_SETTLE_MS);
  }

  private buildOutline(parts: readonly ShownPart[], n: THREE.Vector3, at: number, box: THREE.Box3 | null): void {
    // where the plane crosses the faces, a hair toward the viewer so it sits on the cap
    const pts = cutSegments(parts.map((p) => p.scene.object), n, at);
    const lift = Math.max(1e-3, 1e-4 * (box ? box.getSize(new THREE.Vector3()).length() : 1));
    for (let i = 0; i < pts.length; i += 3) { pts[i] += n.x * lift; pts[i + 1] += n.y * lift; pts[i + 2] += n.z * lift; }
    // a fresh geometry each time; the old one gives its GPU buffer back
    const old = this.outline.geometry;
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    g.computeBoundingSphere();
    this.outline.geometry = g;
    old.dispose();
    this.outlineCount = pts.length / 6;
    this.outline.visible = pts.length > 0;
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
    if (this.outlineTimer) clearTimeout(this.outlineTimer);
    this.clear();
    this.group.removeFromParent();
    this.outline.geometry.dispose();
    (this.outline.material as THREE.Material).dispose();
    this.outline.removeFromParent();
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
