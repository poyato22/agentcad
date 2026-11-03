// Three.js scene for the build view (the assembly): one group per instance (posed by its 4x4, mm,
// Z up) holding a clone of its part's model, an iso orthographic camera like the Design viewer,
// click-to-select, and the view modes' host (ui/viewer/modes: section, X-ray, explode) so the
// assembly gets the same modes as the part viewer, on the instances' poses. Rendered on demand.
// Double-click on empty space (or F, AssemblyView) fits the camera.
//
// Colors: part color when set, else the theme's grey; selected edges blue; red faces where parts
// interfere (status: fail); blue edges while an agent's change glides in.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import type { Mat4 } from "../../api/assembly";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import type { MaterialParams } from "../../lib/appearance";
import { meshColors, type PartScene } from "../viewer/loadPart";
import { fitOrtho, keepOffset, offsetOrtho, visibleWidth, ISO_AZIMUTH_DEG, ISO_ELEVATION_DEG, type ExplodeLink, type ModeHost, type RendererLike, type ShownPart } from "../viewer/scene";
import type { Vec3 } from "./kinematics";

export interface SceneInstance {
  id: string; template: PartScene | null; color: string | null;
  /** Plan 7 U8 finish (lib/appearance materialOf): a shiny one is drawn PBR with the room's
   *  reflections, as in the Part viewer (I5). Absent = the matte drafting look. */
  material?: MaterialParams;
  /** Hidden from the parts tree (Hide / Isolate, ui/left/visibility): not drawn, not hit, not
   *  cut or framed, and out of the modes' parts. */
  hidden?: boolean;
  /** Plan 10 E36: its name under it when laid out. */
  name?: string;
}

export interface OverlayLabel {
  key: string; x: number; y: number; text: string;
  /** Plan 10: what it is (absent: an interference tag): a part's name laid out, the selected
   *  instance's offset from the origin, a comment pin. */
  kind?: "layout" | "delta" | "pin" | "pick" | "hover";
  /** Behind the camera or cut away: drawn faint (pins). */
  hidden?: boolean;
}

/** A world point (mm) with a text, placed on screen every frame (setMarks). */
export interface SceneMark { key: string; point: Vec3; text: string; kind: "layout" | "delta" | "pin" | "pick" | "hover" }

/** One overlapping pair as drawn (instance ids per side; the core names instances). */
export interface InterferencePair { a: string[]; b: string[]; text: string; box?: { min: Vec3; max: Vec3 } | null; intended?: boolean }

/** A click on a part: which instance, where (world mm) and the face normal there (world). */
export interface AssemblyHit { instanceId: string; point: Vec3; normal: Vec3 }

export interface SceneCallbacks {
  /** A click on a part (null: empty space), with the click (Plan 10: measure / comment use it). */
  onSelect(id: string | null, ev?: PointerEvent): void;
  /** Interference tags, in canvas CSS pixels, after every render. */
  onLabels(labels: OverlayLabel[]): void;
}

export interface AssemblyScene {
  setInstances(list: readonly SceneInstance[]): void;
  setPose(pose: Readonly<Record<string, Mat4>>): void;
  setSelected(id: string | null): void;
  /** Plan 10 E16: the part under the pointer while measuring or commenting (its edges tinted). */
  setHovered(id: string | null): void;
  /** Interfering pairs (instance ids) with their volume label. */
  /** G2: the core's overlap list (assembly/interference.py): `box` is where the overlap is (world
   *  mm, else the two parts' box intersection); an `intended` contact is drawn quietly (blue
   *  outline, no tag, the parts untinted), never red. */
  setInterference(pairs: readonly InterferencePair[]): void;
  setArriving(on: boolean): void;
  /** Spheres the moving parts can sweep through (joint origin + reach); the camera frames them too. */
  setReach(spheres: readonly { center: Vec3; radius: number }[]): void;
  fit(): void;
  /** Fix round 1 (G-D): what the camera frames until reset — the posed parts ("posed": no joint
   *  reach), the envelope of `poses` (a motion), or null: the default (parts + every joint's reach).
   *  `margin`: box-to-view ratio (1.4 ≈ the model fills ~70%). Resizes keep it. */
  setFraming(f: Framing | null, margin?: number): void;
  /** What the view modes drive (ui/viewer/modes): the instances as shown parts, by instance id. */
  modeHost(): ModeHost;
  /** Plan 10 E4: who places whom (instance id → parent, joint axis/origin, world) for the explode. */
  setStructure(links: ReadonlyMap<string, ExplodeLink>): void;
  /** Plan 10: labelled world points re-placed after every frame (laid-out names, the offset,
   *  pins); the source is asked each frame, so it can follow poses and the explode. */
  setMarks(source: (() => readonly SceneMark[]) | null): void;
  /** Plan 10 E19: a dashed line from the world origin to this point (the selected instance off
   *  the origin); null hides it. The origin triad itself is always drawn, faint. */
  setOffset(to: Vec3 | null): void;
  /** The triad and the offset line shown (off while exploded or laid out: the parts aren't where
   *  the origin says). */
  setOriginShown(on: boolean): void;
  /** The part (instance) under client pixel (x, y), with the world point and normal. */
  pickAt(clientX: number, clientY: number): AssemblyHit | null;
  /** H1: the world point (mm) under client pixel (x, y) on the view plane through the orbit target:
   *  where a click on empty space is pinned. */
  pointOnView(clientX: number, clientY: number): Vec3;
  /** Plan 10 E16: called with the part under the pointer as it moves (null: none), throttled to
   *  one per frame. Returns the remover. */
  onHover(cb: (hit: AssemblyHit | null, ev: PointerEvent) => void): () => void;
  /** A world point → canvas CSS pixels (overlays). */
  toCanvas(p: Vec3): { x: number; y: number; behind: boolean };
  /** Plan 10 E35: a point in an instance's part frame → world, where its body is drawn now (its
   *  pose, and the explode / layout move); null: no such instance shown. */
  partToWorld(instanceId: string, p: Vec3): Vec3 | null;
  /** The inverse: a world point on the body as drawn → the part frame. */
  worldToPart(instanceId: string, w: Vec3): Vec3 | null;
  /** P11 F4: the box of an instance's mesh as drawn, in its part frame (mm); null: not shown. */
  partBox(instanceId: string): { min: Vec3; max: Vec3 } | null;
  /** A world point (mm) → client pixels. Test probe. */
  /** Dev/test: screen points, and the material an instance is drawn with right now (G4). */
  probe(): { project(p: Vec3): { x: number; y: number }; material(iid: string): { color: string; metalness: number | null; roughness: number | null; opacity: number } | null };
  resize(w: number, h: number): void;
  /** FW-layout: CSS px of the canvas the floating surfaces cover at each side; the model is framed
   *  and centred in the rest (ui/shell/insets). */
  setInsets(left: number, right: number): void;
  dispose(): void;
}

export type Framing = { kind: "posed" } | { kind: "poses"; poses: readonly Readonly<Record<string, Mat4>>[] };

export interface AssemblySceneDeps { createRenderer?: (canvas: HTMLCanvasElement) => RendererLike }

export function toMatrix4(m: Mat4): THREE.Matrix4 {
  return new THREE.Matrix4().set(...(m.flat() as Parameters<THREE.Matrix4["set"]>));
}

function tokens(): { face: string; edge: string; blue: string; red: string } {
  const base = meshColors();
  let blue = ""; let red = "";
  if (typeof document !== "undefined" && typeof getComputedStyle === "function") {
    const s = getComputedStyle(document.documentElement);
    blue = s.getPropertyValue("--blue").trim();
    red = s.getPropertyValue("--red").trim();
  }
  return { ...base, blue: blue || "#3d8bff", red: red || "#ff5a4a" };
}

function defaultRenderer(canvas: HTMLCanvasElement): RendererLike {
  // stencil: the section caps (viewer/modes/section.ts) fill the cut by stencil parity
  return new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: true, stencil: true });
}

interface Entry { id: string; group: THREE.Group; body: THREE.Object3D | null; template: PartScene | null; color: string | null; material: MaterialParams | null; hidden: boolean; name?: string }

export function createAssemblyScene(canvas: HTMLCanvasElement, cb: SceneCallbacks, deps: AssemblySceneDeps = {}): AssemblyScene {
  const renderer = (deps.createRenderer ?? defaultRenderer)(canvas);
  renderer.setClearColor(0x000000, 0);
  renderer.setPixelRatio(typeof window === "undefined" ? 1 : Math.min(window.devicePixelRatio || 1, 2));

  const scene = new THREE.Scene();
  scene.add(new THREE.AmbientLight(0xffffff, 1.1));
  const key = new THREE.DirectionalLight(0xffffff, 0.9);
  key.position.set(-1, -2, 3);
  scene.add(key);

  const camera = new THREE.OrthographicCamera(-80, 80, 80, -80, 0.1, 4000);
  camera.up.set(0, 0, 1);
  const az = THREE.MathUtils.degToRad(ISO_AZIMUTH_DEG);
  const el = THREE.MathUtils.degToRad(ISO_ELEVATION_DEG);
  camera.position.set(Math.cos(el) * Math.sin(az), -Math.cos(el) * Math.cos(az), Math.sin(el)).multiplyScalar(300);
  camera.lookAt(0, 0, 0);

  const root = new THREE.Group();
  scene.add(root);

  let width = 1; let height = 1;
  /** FW-layout: canvas px the floating surfaces cover at each side (setInsets). */
  let insetL = 0; let insetR = 0;
  const frameView = (b: THREE.Box3, margin: number) => {
    fitOrtho(camera, b, visibleWidth(width, insetL, insetR) / height, margin);
    offsetOrtho(camera, width, height, insetL, insetR);
  };
  let frame = 0;
  let disposed = false;
  let colors = tokens();
  const entries = new Map<string, Entry>();
  let selected: string | null = null;
  let hovered: string | null = null;
  let interference: InterferencePair[] = [];
  let arriving = false;
  /** The parts (with geometry) the camera was last fitted to: a part added or removed refits. */
  let fittedKey = "";
  let reach: { center: Vec3; radius: number }[] = [];
  const FIT_MARGIN = 1.15;
  /** The view modes re-apply after every restyle (fresh materials, opacity reset). */
  const restyled = new Set<() => void>();
  /** Who places whom, for the explode (setStructure). */
  let structure: ReadonlyMap<string, ExplodeLink> = new Map();
  /** Labelled points placed each frame (setMarks). */
  let markSource: (() => readonly SceneMark[]) | null = null;

  const draw = () => {
    frame = 0;
    if (disposed) return;
    try { renderer.render(scene, camera); } catch { /* the canvas never throws */ }
    cb.onLabels(labels());
  };
  const requestRender = () => { if (!frame && !disposed) frame = requestAnimationFrame(draw); };

  const orbit = new OrbitControls(camera, canvas);
  orbit.enableDamping = false;
  // a scroll zoom pivots on the free area's middle, not the canvas centre (P10 FWA)
  orbit.addEventListener("change", () => { keepOffset(camera, canvas, { left: insetL, right: insetR }); requestRender(); });
  /** The user turned, panned or zoomed since the last fit: a fold then only slides the view. */
  let moved = false;
  orbit.addEventListener("start", () => { moved = true; });

  // ---- materials

  /** The room the shiny parts reflect: RoomEnvironment through PMREM, turned Z-up — the Part
   *  viewer's (viewer/scene.ts), made once on first need. Null without WebGL (tests). */
  let envTex: THREE.Texture | null | undefined;
  function environment(): void {
    if (envTex !== undefined) return;
    envTex = null;
    try {
      if (renderer instanceof THREE.WebGLRenderer) {
        const pmrem = new THREE.PMREMGenerator(renderer);
        const room = new RoomEnvironment();
        envTex = pmrem.fromScene(room, 0.04).texture;
        room.dispose();
        pmrem.dispose();
      }
    } catch { envTex = null; }
    scene.environment = envTex;
    scene.environmentRotation.x = Math.PI / 2;
  }

  /** Matte Lambert, or PBR Standard for a shiny finish; swapped only when the kind changes. */
  function meshMaterial(o: THREE.Mesh, look: MaterialParams | null): THREE.MeshLambertMaterial | THREE.MeshStandardMaterial {
    const cur = o.material as THREE.MeshLambertMaterial | THREE.MeshStandardMaterial;
    const shiny = !!look?.shiny && !cur.vertexColors;
    if (shiny && !(cur instanceof THREE.MeshStandardMaterial)) {
      environment();
      o.material = new THREE.MeshStandardMaterial({ polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });
      cur.dispose();
    } else if (!shiny && cur instanceof THREE.MeshStandardMaterial) {
      o.material = new THREE.MeshLambertMaterial({ polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });
      cur.dispose();
    }
    const m = o.material as THREE.MeshLambertMaterial | THREE.MeshStandardMaterial;
    if (m instanceof THREE.MeshStandardMaterial && look) {
      m.metalness = look.metalness;
      m.roughness = look.roughness;
      m.envMapIntensity = look.metalness > 0.5 ? 1.3 : 1;
    }
    return m;
  }

  function restyle(): void {
    const hot = new Set<string>();
    for (const p of interference) if (!p.intended) for (const id of [...p.a, ...p.b]) hot.add(id);
    for (const e of entries.values()) {
      if (!e.body) continue;
      // the part keeps its own color; a clash shows as red edges and the red overlap block
      const face = e.material?.shiny ? (e.material.color ?? e.color ?? colors.face) : (e.color ?? colors.face);
      const edge = e.id === selected || e.id === hovered || arriving ? colors.blue : hot.has(e.id) ? colors.red : colors.edge;
      e.body.traverse((o) => {
        if (o instanceof THREE.LineSegments) (o.material as THREE.LineBasicMaterial).color.set(edge);
        else if (o instanceof THREE.Mesh) {
          const m = meshMaterial(o, e.material);
          m.color.set(face);
          // a clash shows through; else the finish's own opacity (glass-like looks)
          const see = hot.has(e.id) ? 0.55 : (e.material?.shiny ? e.material.opacity : 1);
          if (m.transparent !== see < 1) m.needsUpdate = true;
          m.transparent = see < 1;
          m.opacity = see;
          m.depthWrite = see >= 1;
        }
      });
    }
    rebuildOverlaps();
    for (const f of [...restyled]) f();
    requestRender();
  }

  // ---- interference: a red block where each clashing pair's world boxes overlap
  const overlapRoot = new THREE.Group();
  scene.add(overlapRoot);
  const unitBox = new THREE.BoxGeometry(1, 1, 1);
  const unitEdges = new THREE.EdgesGeometry(unitBox);
  const overlapFill = new THREE.MeshBasicMaterial({ color: colors.red, transparent: true, opacity: 0.45, depthWrite: false });
  const overlapLine = new THREE.LineBasicMaterial({ color: colors.red });
  // G2: a declared contact (press fit, bearing seat) is outlined quietly, never red
  const intendedLine = new THREE.LineBasicMaterial({ color: colors.blue, transparent: true, opacity: 0.7 });

  function rebuildOverlaps(): void {
    overlapFill.color.set(colors.red);
    overlapLine.color.set(colors.red);
    intendedLine.color.set(colors.blue);
    for (const c of [...overlapRoot.children]) c.removeFromParent();
    for (const p of interference) {
      const a = new THREE.Box3(); const b = new THREE.Box3();
      for (const id of p.a) { const e = entries.get(id); if (e?.body && !e.hidden) a.expandByObject(e.group); }
      for (const id of p.b) { const e = entries.get(id); if (e?.body && !e.hidden) b.expandByObject(e.group); }
      // where the core found the overlap; else (an older core) the parts' boxes' intersection
      const both = a.isEmpty() || b.isEmpty() ? new THREE.Box3()
        : p.box ? new THREE.Box3(new THREE.Vector3(...p.box.min), new THREE.Vector3(...p.box.max)) : a.intersect(b);
      if (both.isEmpty()) continue;
      const size = both.getSize(new THREE.Vector3()).max(new THREE.Vector3(0.6, 0.6, 0.6));
      const g = new THREE.Group();
      g.position.copy(both.getCenter(new THREE.Vector3()));
      g.scale.copy(size);
      if (p.intended) g.add(new THREE.LineSegments(unitEdges, intendedLine));
      else g.add(new THREE.Mesh(unitBox, overlapFill), new THREE.LineSegments(unitEdges, overlapLine));
      g.userData["intended"] = !!p.intended;
      g.renderOrder = 5;
      overlapRoot.add(g);
    }
  }

  function pickAt(clientX: number, clientY: number): AssemblyHit | null {
    const r = canvas.getBoundingClientRect();
    ndc.set(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const meshes: THREE.Object3D[] = [];
    for (const e of entries.values()) if (!e.hidden) e.body?.traverse((o) => { if (o instanceof THREE.Mesh) meshes.push(o); });
    const plane = sectionPlane();
    const h = ray.intersectObjects(meshes, false).find((x) => !plane || plane.distanceToPoint(x.point) >= -1e-6);
    if (!h) return null;
    let o: THREE.Object3D | null = h.object;
    while (o && o.userData["instanceId"] === undefined) o = o.parent;
    const id = o?.userData["instanceId"] as string | undefined;
    if (!id || !entries.has(id)) return null;
    const n = h.face ? h.face.normal.clone().transformDirection(h.object.matrixWorld) : new THREE.Vector3(0, 0, 1);
    return { instanceId: id, point: [h.point.x, h.point.y, h.point.z], normal: [n.x, n.y, n.z] };
  }

  /** Part frame → world as the body is drawn now: the group's pose × the body's own move away
   *  from where its template sits (the explode / layout), i.e. G · M · R0⁻¹. */
  function bodyMatrix(iid: string): THREE.Matrix4 | null {
    const e = entries.get(iid);
    if (!e?.body || !e.template) return null;
    e.group.updateMatrixWorld(true);
    e.body.updateMatrix();
    e.template.object.updateMatrix();
    return e.group.matrixWorld.clone().multiply(e.body.matrix).multiply(e.template.object.matrix.clone().invert());
  }

  function cloneBody(t: PartScene): THREE.Object3D {
    const body = t.object.clone(true);
    body.traverse((o) => {
      if (o instanceof THREE.Mesh || o instanceof THREE.LineSegments) {
        o.material = (o.material as THREE.Material).clone();
      }
    });
    return body;
  }

  function disposeBody(body: THREE.Object3D | null): void {
    body?.traverse((o) => {
      if (o instanceof THREE.Mesh || o instanceof THREE.LineSegments) (o.material as THREE.Material).dispose();
    });
    body?.removeFromParent();
  }

  // ---- labels

  const box = new THREE.Box3();
  const box2 = new THREE.Box3();
  function labels(): OverlayLabel[] {
    const out: OverlayLabel[] = [];
    interference.forEach((p, i) => {
      const shownGroup = (id: string) => { const e = entries.get(id); return e && !e.hidden ? e.group : undefined; };
      const a = p.a.map(shownGroup).filter(Boolean) as THREE.Group[];
      const b = p.b.map(shownGroup).filter(Boolean) as THREE.Group[];
      if (!a.length || !b.length || p.intended) return;      // an intended contact has no tag
      box.makeEmpty(); for (const g of a) box.expandByObject(g);
      box2.makeEmpty(); for (const g of b) box2.expandByObject(g);
      const both = p.box ? new THREE.Box3(new THREE.Vector3(...p.box.min), new THREE.Vector3(...p.box.max)) : box.clone().intersect(box2);
      const c = (both.isEmpty() ? box.union(box2) : both).getCenter(new THREE.Vector3()).project(camera);
      out.push({ key: `${i}`, x: ((c.x + 1) / 2) * width, y: ((1 - c.y) / 2) * height, text: p.text });
    });
    for (const m of markSource?.() ?? []) {
      const s = toCanvas(m.point);
      out.push({ key: `${m.kind}:${m.key}`, x: Math.round(s.x * 10) / 10, y: Math.round(s.y * 10) / 10, text: m.text, kind: m.kind, ...(s.behind ? { hidden: true } : {}) });
    }
    return out;
  }

  function toCanvas(p: Vec3): { x: number; y: number; behind: boolean } {
    const v = new THREE.Vector3(...p).project(camera);
    return { x: ((v.x + 1) / 2) * width, y: ((1 - v.y) / 2) * height, behind: v.z > 1 || v.z < -1 };
  }

  // ---- the origin (E19): a faint X / Y / Z triad at the world origin, always; a dashed line to
  // the selected instance's origin when it sits off it
  const triad = new THREE.Group();
  triad.name = "origin-triad";
  triad.renderOrder = 7;
  scene.add(triad);
  const AXIS_COLORS = [0xe5484d, 0x46a758, 0x3d8bff];
  const axisMats = AXIS_COLORS.map((c) => new THREE.LineBasicMaterial({ color: c, transparent: true, opacity: 0.7, depthTest: false }));
  const axisGeos = [[1, 0, 0], [0, 1, 0], [0, 0, 1]].map((d) => new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(...d)]));
  axisGeos.forEach((g, i) => { const l = new THREE.Line(g, axisMats[i]); l.renderOrder = 7; triad.add(l); });
  const originDot = new THREE.Mesh(new THREE.SphereGeometry(0.035, 12, 8), new THREE.MeshBasicMaterial({ color: 0x9aa3ad, transparent: true, opacity: 0.8, depthTest: false }));
  originDot.renderOrder = 7;
  triad.add(originDot);
  const offsetMat = new THREE.LineDashedMaterial({ color: colors.blue, dashSize: 3, gapSize: 2, transparent: true, opacity: 0.85, depthTest: false });
  const offsetGeo = new THREE.BufferGeometry();
  const offsetLine = new THREE.Line(offsetGeo, offsetMat);
  offsetLine.renderOrder = 7;
  offsetLine.visible = false;
  scene.add(offsetLine);
  let offsetTo: Vec3 | null = null;
  let originShown = true;
  /** The triad's arm length: a tenth of what is shown (it never dwarfs a small part). */
  function placeTriad(): void {
    const b = posedBox();
    const s = b.isEmpty() ? 10 : Math.max(b.getSize(new THREE.Vector3()).length() * 0.1, 4);
    triad.scale.setScalar(s);
    offsetMat.color.set(colors.blue);
    offsetMat.dashSize = s * 0.12; offsetMat.gapSize = s * 0.08;
    if (offsetTo) {
      offsetGeo.setFromPoints([new THREE.Vector3(), new THREE.Vector3(...offsetTo)]);
      offsetLine.computeLineDistances();
    }
    offsetLine.visible = originShown && offsetTo !== null;
    triad.visible = originShown;
  }

  // ---- picking

  const ray = new THREE.Raycaster();
  const ndc = new THREE.Vector2();

  function hit(ev: PointerEvent | MouseEvent): string | null {
    const r = canvas.getBoundingClientRect();
    ndc.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const meshes: THREE.Object3D[] = [];
    for (const e of entries.values()) if (!e.hidden) e.body?.traverse((o) => { if (o instanceof THREE.Mesh) meshes.push(o); });
    // a section keeps only the near side: a hit on a clipped-away face is not a hit
    const plane = sectionPlane();
    const h = ray.intersectObjects(meshes, false).find((x) => !plane || plane.distanceToPoint(x.point) >= -1e-6);
    if (!h) return null;
    let o: THREE.Object3D | null = h.object;
    while (o && o.userData["instanceId"] === undefined) o = o.parent;
    const id = o?.userData["instanceId"] as string | undefined;
    return id && entries.has(id) ? id : null;
  }

  /** The section's clip plane while one is on (read off the parts' materials). */
  function sectionPlane(): THREE.Plane | null {
    for (const e of entries.values()) {
      let found: THREE.Plane | null = null;
      e.body?.traverse((o) => {
        if (!found && o instanceof THREE.Mesh) found = (o.material as THREE.Material).clippingPlanes?.[0] ?? null;
      });
      if (found) return found;
    }
    return null;
  }

  let down: { x: number; y: number } | null = null;
  const onDown = (ev: PointerEvent) => { down = { x: ev.clientX, y: ev.clientY }; };
  const onUp = (ev: PointerEvent) => {
    const d = down;
    down = null;
    if (!d || Math.hypot(ev.clientX - d.x, ev.clientY - d.y) > 4) return; // an orbit, not a click
    cb.onSelect(hit(ev), ev);
  };
  // D6: Fit is a double-click on empty space (or F)
  const onDbl = (ev: MouseEvent) => { if (hit(ev) === null) fit(); };
  canvas.addEventListener("pointerdown", onDown);
  canvas.addEventListener("pointerup", onUp);
  canvas.addEventListener("dblclick", onDbl);
  // E16: what is under the pointer, once a frame at most (only while someone listens)
  const hoverers = new Set<(hit: AssemblyHit | null, ev: PointerEvent) => void>();
  let hoverFrame = 0;
  let hoverEv: PointerEvent | null = null;
  const onMove = (ev: PointerEvent) => {
    if (!hoverers.size || ev.buttons) return;
    hoverEv = ev;
    if (hoverFrame) return;
    hoverFrame = requestAnimationFrame(() => {
      hoverFrame = 0;
      const e = hoverEv;
      if (!e || disposed) return;
      const h = pickAt(e.clientX, e.clientY);
      for (const f of [...hoverers]) f(h, e);
    });
  };
  const onLeave = (ev: PointerEvent) => { for (const f of [...hoverers]) f(null, ev); };
  canvas.addEventListener("pointermove", onMove);
  canvas.addEventListener("pointerleave", onLeave);

  // ---- theme
  const onTheme = () => { colors = tokens(); restyle(); };
  const themeObserver = typeof MutationObserver === "function" && typeof document !== "undefined" ? new MutationObserver(onTheme) : null;
  themeObserver?.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  const osScheme = typeof window !== "undefined" && typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: light)") : null;
  osScheme?.addEventListener("change", onTheme);

  function worldBox(): THREE.Box3 {
    const b = new THREE.Box3();
    for (const e of entries.values()) if (e.body && !e.hidden) b.expandByObject(e.group);
    if (b.isEmpty()) return b;
    for (const r of reach) {
      const c = new THREE.Vector3(...r.center);
      b.expandByPoint(c.clone().addScalar(r.radius)).expandByPoint(c.clone().addScalar(-r.radius));
    }
    return b;
  }

  function refitIfNew(): void {
    // what is shown: a Hide or Isolate in the parts tree frames what is left
    const key = [...entries.values()].filter((e) => e.body && !e.hidden).map((e) => e.id).sort().join(",");
    if (key === fittedKey || key === "") return;
    fittedKey = key;
    root.updateMatrixWorld(true);
    fit();
  }

  /** The framing a full-window view asked for (null: the default). */
  let framing: Framing | null = null;
  let framingMargin = FIT_MARGIN;
  function posedBox(): THREE.Box3 {
    const b = new THREE.Box3();
    for (const e of entries.values()) if (e.body && !e.hidden) b.expandByObject(e.group);
    return b;
  }
  function posesBox(poses: readonly Readonly<Record<string, Mat4>>[]): THREE.Box3 {
    const b = new THREE.Box3();
    for (const pose of poses) {
      for (const e of entries.values()) {
        const m = pose[e.id];
        if (!e.template || !m || e.hidden) continue;
        b.union(e.template.bbox.clone().applyMatrix4(toMatrix4(m)));
      }
    }
    return b.isEmpty() ? posedBox() : b;
  }
  function frameBox(): THREE.Box3 {
    if (!framing) return worldBox();
    return framing.kind === "posed" ? posedBox() : posesBox(framing.poses);
  }
  const frameMargin = () => (framing ? framingMargin : FIT_MARGIN);

  function fit(): void {
    const b = frameBox();
    if (b.isEmpty()) return;
    moved = false;
    frameView(b, frameMargin());
    orbit.target.copy(b.getCenter(new THREE.Vector3()));
    orbit.update();
    requestRender();
  }

  // ---- the view modes' host: each posed instance is one shown part. Its bbox is its template's
  // box through the instance pose (the assembled layout: an explode never moves it).
  const shown = new Map<string, ShownPart>();
  function shownParts(): readonly ShownPart[] {
    const out: ShownPart[] = [];
    for (const e of entries.values()) {
      if (!e.body || !e.template || e.hidden) { shown.delete(e.id); continue; }
      let s = shown.get(e.id);
      if (!s || s.scene.object !== e.body) {
        const body = e.body as THREE.Group;
        const tpl = e.template;
        const group = e.group;
        s = {
          id: e.id, color: e.color, focus: false, ...(e.material ? { material: e.material } : {}), ...(e.name ? { label: e.name } : {}),
          scene: { object: body, get bbox() { group.updateMatrixWorld(true); return tpl.bbox.clone().applyMatrix4(group.matrixWorld); }, dispose() { /* the scene's */ } },
        };
        shown.set(e.id, s);
      }
      out.push(s);
    }
    return out;
  }
  /** Parts moved (a glide, an animation): the view modes re-place their caps and outline, once a
   *  frame at most (review M1: a moving part's cut never shows hollow). */
  let modesFrame = 0;
  function followModes(): void {
    if (modesFrame || disposed || restyled.size === 0) return;
    modesFrame = requestAnimationFrame(() => { modesFrame = 0; if (!disposed) for (const f of [...restyled]) f(); });
  }

  const host: ModeHost = {
    scene, camera, controls: orbit, canvas,
    parts: shownParts,
    requestRender,
    onRestyle(f) { restyled.add(f); return () => { restyled.delete(f); }; },
    enableLocalClipping() { renderer.localClippingEnabled = true; },
    structure: () => structure,
    insets: () => ({ left: insetL, right: insetR }),
  };

  return {
    setInstances(list) {
      const keep = new Set(list.map((i) => i.id));
      for (const [id, e] of entries) {
        if (!keep.has(id)) { disposeBody(e.body); e.group.removeFromParent(); entries.delete(id); shown.delete(id); }
      }
      for (const inst of list) {
        let e = entries.get(inst.id);
        if (!e) {
          const group = new THREE.Group();
          group.matrixAutoUpdate = false;
          group.userData["instanceId"] = inst.id;
          root.add(group);
          e = { id: inst.id, group, body: null, template: null, color: null, material: null, hidden: false };
          entries.set(inst.id, e);
        }
        if (e.template !== inst.template) {
          disposeBody(e.body);
          e.body = inst.template ? cloneBody(inst.template) : null;
          if (e.body) e.group.add(e.body);
          e.template = inst.template;
        }
        e.color = inst.color;
        e.material = inst.material ?? null;
        e.hidden = !!inst.hidden;
        e.name = inst.name;
        const sp = shown.get(e.id);
        if (sp) sp.label = inst.name;
        e.group.visible = !e.hidden;
      }
      restyle();
      refitIfNew();
      placeTriad();
    },
    setPose(pose) {
      for (const [id, m] of Object.entries(pose)) {
        const e = entries.get(id);
        if (!e) continue;
        e.group.matrix.copy(toMatrix4(m));
        e.group.matrixWorldNeedsUpdate = true;
      }
      root.updateMatrixWorld(true);
      rebuildOverlaps();
      refitIfNew();
      placeTriad();
      followModes();
      requestRender();
    },
    setSelected(id) { selected = id; restyle(); },
    setHovered(id) { if (hovered !== id) { hovered = id; restyle(); } },
    setInterference(pairs) { interference = [...pairs]; restyle(); },
    setArriving(on) { if (arriving !== on) { arriving = on; restyle(); } },
    setReach(list) { reach = [...list]; },
    fit,
    setFraming(f, margin = FIT_MARGIN) { framing = f; framingMargin = margin; fit(); },
    modeHost: () => host,
    setStructure(links) { structure = links; },
    setMarks(source) { markSource = source; requestRender(); },
    setOffset(to) {
      const same = to === offsetTo || (to !== null && offsetTo !== null && to.every((v, i) => Math.abs(v - (offsetTo as Vec3)[i]) < 1e-6));
      if (same) return;
      offsetTo = to ? [to[0], to[1], to[2]] : null;
      placeTriad();
      requestRender();
    },
    setOriginShown(on) { if (on !== originShown) { originShown = on; placeTriad(); requestRender(); } },
    pickAt,
    pointOnView(clientX, clientY) {
      const r = canvas.getBoundingClientRect();
      ndc.set(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(ndc, camera);
      const dir = camera.getWorldDirection(new THREE.Vector3());
      const at = ray.ray.intersectPlane(new THREE.Plane().setFromNormalAndCoplanarPoint(dir, orbit.target), new THREE.Vector3());
      const v = at ?? orbit.target;
      return [v.x, v.y, v.z];
    },
    partToWorld(iid, p) {
      const m = bodyMatrix(iid);
      if (!m) return null;
      const v = new THREE.Vector3(...p).applyMatrix4(m);
      return [v.x, v.y, v.z];
    },
    worldToPart(iid, w) {
      const m = bodyMatrix(iid);
      if (!m) return null;
      const v = new THREE.Vector3(...w).applyMatrix4(m.invert());
      return [v.x, v.y, v.z];
    },
    partBox(iid) {
      const b = entries.get(iid)?.template?.bbox;
      if (!b || b.isEmpty()) return null;
      return { min: [b.min.x, b.min.y, b.min.z], max: [b.max.x, b.max.y, b.max.z] };
    },
    onHover(f) { hoverers.add(f); return () => { hoverers.delete(f); }; },
    toCanvas,
    probe() {
      return {
        project(p) {
          const v = new THREE.Vector3(...p).project(camera);
          const r = canvas.getBoundingClientRect();
          return { x: r.left + ((v.x + 1) / 2) * r.width, y: r.top + ((1 - v.y) / 2) * r.height };
        },
        material(iid) {
          let mesh: THREE.Mesh | null = null;
          entries.get(iid)?.body?.traverse((o) => { if (!mesh && o instanceof THREE.Mesh) mesh = o; });
          const m = (mesh as THREE.Mesh | null)?.material as THREE.MeshStandardMaterial | undefined;
          if (!m) return null;
          return { color: `#${m.color.getHexString()}`, metalness: "metalness" in m ? m.metalness : null,
            roughness: "roughness" in m ? m.roughness : null, opacity: m.opacity };
        },
      };
    },
    resize(w, h) {
      width = Math.max(1, w); height = Math.max(1, h);
      renderer.setSize(width, height, false);
      const b = frameBox();
      if (!b.isEmpty()) {
        frameView(b, frameMargin());
        if (framing) { orbit.target.copy(b.getCenter(new THREE.Vector3())); orbit.update(); }
      }
      else offsetOrtho(camera, width, height, insetL, insetR);
      requestRender();
    },
    setInsets(l, r) {
      if (l === insetL && r === insetR) return;
      insetL = l; insetR = r;
      // untouched since the last fit: refit to the new room; else keep the user's view, re-centred
      if (!moved && !frameBox().isEmpty()) fit();
      else offsetOrtho(camera, width, height, insetL, insetR);
      requestRender();
    },
    dispose() {
      disposed = true;
      if (frame) cancelAnimationFrame(frame);
      if (modesFrame) cancelAnimationFrame(modesFrame);
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointerup", onUp);
      canvas.removeEventListener("dblclick", onDbl);
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerleave", onLeave);
      if (hoverFrame) cancelAnimationFrame(hoverFrame);
      hoverers.clear();
      for (const g of axisGeos) g.dispose();
      for (const m of axisMats) m.dispose();
      originDot.geometry.dispose(); (originDot.material as THREE.Material).dispose();
      offsetGeo.dispose(); offsetMat.dispose();
      orbit.dispose();
      themeObserver?.disconnect();
      osScheme?.removeEventListener("change", onTheme);
      for (const e of entries.values()) disposeBody(e.body);
      unitBox.dispose(); unitEdges.dispose(); overlapFill.dispose(); overlapLine.dispose();
      entries.clear();
      shown.clear();
      restyled.clear();
      envTex?.dispose();
      renderer.dispose();
    },
  };
}
