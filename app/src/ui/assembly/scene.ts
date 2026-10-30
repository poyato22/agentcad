// Three.js scene for assembly mode: one group per instance (posed by its 4x4, mm, Z up) holding a
// clone of its part's model, an iso orthographic camera like the Design viewer, picking, and a
// TransformControls gizmo. Rendered on demand.
//
// The gizmo drives either a free part (move / turn, snapped 1 mm / 15°) or a jointed part along
// its joint only: a turn ring about a revolute axis, an arrow along a slider. It moves a proxy;
// the controller poses the real groups from the values it reports.
//
// Colors: part color when set, else the theme's grey; selected edges blue; red faces where parts
// interfere (status: fail); blue edges while an agent's change glides in.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { TransformControls } from "three/addons/controls/TransformControls.js";
import type { Mat4 } from "../../api/assembly";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import type { MaterialParams } from "../../lib/appearance";
import { meshColors, type PartScene } from "../viewer/loadPart";
import { fitOrtho, ISO_AZIMUTH_DEG, ISO_ELEVATION_DEG, type RendererLike } from "../viewer/scene";
import type { Vec3 } from "./kinematics";
import { angleAboutZ, snapMove, snapTurn, TURN_STEP_DEG } from "./snap";

export interface SceneInstance {
  id: string; template: PartScene | null; color: string | null;
  /** Plan 7 U8 finish (lib/appearance materialOf): a shiny one is drawn PBR with the room's
   *  reflections, as in the Part viewer (I5). Absent = the matte drafting look. */
  material?: MaterialParams;
}

/** Where the gizmo sits and what it moves. */
export type GizmoSpec =
  | null
  | { kind: "free"; id: string; mode: "move" | "turn"; transform: Mat4; snap: boolean }
  | { kind: "joint"; frame: Mat4; motion: "turn" | "slide"; value: number; sign: 1 | -1; snap: boolean };

export interface PickMarker { instanceId: string; origin: Vec3; axis: Vec3; role: "a" | "b" | "hover" }

export interface OverlayLabel { key: string; x: number; y: number; text: string }

export interface SceneCallbacks {
  /** A click on a part (null: empty space). Not called while picking faces. */
  onSelect(id: string | null): void;
  /** A click on a part while picking: hit point and surface normal in that part's coordinates. */
  onPick(instanceId: string, point: Vec3, normal: Vec3): void;
  /** Pointer over a part while picking (null: off every part); return the marker to show. */
  onHoverPick(instanceId: string | null, point: Vec3, normal: Vec3): PickMarker | null;
  onDragStart(): void;
  /** Free part: its new transform. Joint: the new joint value (gizmo sign applied). */
  onDrag(v: { transform: Mat4 } | { value: number }): void;
  onDragEnd(v: { transform: Mat4 } | { value: number }): void;
  /** Interference tags, in canvas CSS pixels, after every render. */
  onLabels(labels: OverlayLabel[]): void;
}

export interface AssemblyScene {
  setInstances(list: readonly SceneInstance[]): void;
  setPose(pose: Readonly<Record<string, Mat4>>): void;
  setSelected(id: string | null): void;
  /** Interfering pairs (instance ids) with their volume label. */
  setInterference(pairs: readonly { a: string[]; b: string[]; text: string }[]): void;
  setArriving(on: boolean): void;
  /** Spheres the moving parts can sweep through (joint origin + reach); the camera frames them too. */
  setReach(spheres: readonly { center: Vec3; radius: number }[]): void;
  setPicking(on: boolean): void;
  setMarkers(markers: readonly PickMarker[]): void;
  setGizmo(spec: GizmoSpec): void;
  fit(): void;
  /** A world point (mm) → client pixels; the gizmo handle under the pointer. Test probes. */
  probe(): { project(p: Vec3): { x: number; y: number }; gizmoAxis(): string | null };
  resize(w: number, h: number): void;
  dispose(): void;
}

export interface AssemblySceneDeps { createRenderer?: (canvas: HTMLCanvasElement) => RendererLike }

export function toMatrix4(m: Mat4): THREE.Matrix4 {
  return new THREE.Matrix4().set(...(m.flat() as Parameters<THREE.Matrix4["set"]>));
}

export function fromMatrix4(m: THREE.Matrix4): Mat4 {
  const e = m.elements;
  return [0, 1, 2, 3].map((r) => [0, 1, 2, 3].map((c) => {
    const v = e[c * 4 + r];
    return Math.abs(v) < 1e-12 ? 0 : v;
  }));
}

function tokens(): { face: string; edge: string; blue: string; red: string; dim: string } {
  const base = meshColors();
  let blue = ""; let red = ""; let dim = "";
  if (typeof document !== "undefined" && typeof getComputedStyle === "function") {
    const s = getComputedStyle(document.documentElement);
    blue = s.getPropertyValue("--blue").trim();
    red = s.getPropertyValue("--red").trim();
    dim = s.getPropertyValue("--dim").trim();
  }
  return { ...base, blue: blue || "#3d8bff", red: red || "#ff5a4a", dim: dim || "#9a9a9a" };
}

function defaultRenderer(canvas: HTMLCanvasElement): RendererLike {
  return new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: true });
}

interface Entry { id: string; group: THREE.Group; body: THREE.Object3D | null; template: PartScene | null; color: string | null; material: MaterialParams | null }

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
  const markerRoot = new THREE.Group();
  scene.add(markerRoot);

  let width = 1; let height = 1;
  let frame = 0;
  let disposed = false;
  let colors = tokens();
  const entries = new Map<string, Entry>();
  let selected: string | null = null;
  let interference: { a: string[]; b: string[]; text: string }[] = [];
  let arriving = false;
  let picking = false;
  let markers: PickMarker[] = [];
  let hoverMarker: PickMarker | null = null;
  /** The parts (with geometry) the camera was last fitted to: a part added or removed refits. */
  let fittedKey = "";
  let reach: { center: Vec3; radius: number }[] = [];
  const FIT_MARGIN = 1.15;

  const draw = () => {
    frame = 0;
    if (disposed) return;
    try { renderer.render(scene, camera); } catch { /* the canvas never throws */ }
    cb.onLabels(labels());
  };
  const requestRender = () => { if (!frame && !disposed) frame = requestAnimationFrame(draw); };

  const orbit = new OrbitControls(camera, canvas);
  orbit.enableDamping = false;
  orbit.addEventListener("change", requestRender);

  // ---- gizmo
  const gizmo = new TransformControls(camera, canvas);
  gizmo.setSize(0.75);
  const helper = gizmo.getHelper();
  scene.add(helper);
  const jointFrame = new THREE.Group();
  jointFrame.matrixAutoUpdate = false;
  scene.add(jointFrame);
  const proxy = new THREE.Object3D();
  let spec: GizmoSpec = null;
  const startQ = new THREE.Quaternion();
  const startP = new THREE.Vector3();
  let lastValue = 0;
  let justDragged = false;

  function gizmoColors(): void {
    gizmo.setColors(colors.dim, colors.dim, colors.dim, colors.blue);
  }
  gizmoColors();

  function proxyValue(): number {
    if (!spec || spec.kind !== "joint") return 0;
    const raw = spec.motion === "turn" ? angleAboutZ(proxy.quaternion, lastValue * spec.sign) : proxy.position.z;
    const step = spec.motion === "turn" ? TURN_STEP_DEG : 1;
    const v = spec.snap ? Math.round(raw / step) * step : Math.round(raw * 10) / 10;
    return v * spec.sign;
  }

  function placeProxyForJoint(value: number): void {
    if (!spec || spec.kind !== "joint") return;
    proxy.position.set(0, 0, 0);
    proxy.quaternion.identity();
    if (spec.motion === "turn") proxy.quaternion.setFromAxisAngle(new THREE.Vector3(0, 0, 1), THREE.MathUtils.degToRad(value * spec.sign));
    else proxy.position.z = value * spec.sign;
    proxy.updateMatrixWorld(true);
  }

  gizmo.addEventListener("dragging-changed", (e) => {
    const dragging = (e as unknown as { value: boolean }).value;
    orbit.enabled = !dragging;
    if (dragging) {
      startQ.copy(proxy.quaternion);
      startP.copy(proxy.position);
      if (spec?.kind === "joint") lastValue = spec.value;
      cb.onDragStart();
    } else {
      justDragged = true;
      cb.onDragEnd(current());
    }
  });
  gizmo.addEventListener("objectChange", () => {
    if (!spec) return;
    if (spec.kind === "free" && spec.snap) {
      if (spec.mode === "move") proxy.position.fromArray(snapMove(startP.toArray(), proxy.position.toArray()));
      else proxy.quaternion.copy(snapTurn(startQ, proxy.quaternion));
    }
    if (spec.kind === "joint") {
      const v = proxyValue();
      lastValue = v;
      placeProxyForJoint(v);
    }
    cb.onDrag(current());
    requestRender();
  });
  gizmo.addEventListener("change", requestRender);

  function current(): { transform: Mat4 } | { value: number } {
    if (spec?.kind === "joint") return { value: lastValue };
    proxy.updateMatrix();
    return { transform: fromMatrix4(proxy.matrix) };
  }

  function applyGizmo(next: GizmoSpec): void {
    if (gizmo.dragging) return; // the drag owns the proxy until it ends
    spec = next;
    gizmo.detach();
    proxy.removeFromParent();
    if (!next || picking) { requestRender(); return; }
    gizmo.showX = gizmo.showY = gizmo.showZ = true;
    if (next.kind === "free") {
      scene.add(proxy);
      const m = toMatrix4(next.transform);
      m.decompose(proxy.position, proxy.quaternion, proxy.scale);
      gizmo.setSpace("world");
      gizmo.setMode(next.mode === "move" ? "translate" : "rotate");
    } else {
      jointFrame.matrix.copy(toMatrix4(next.frame));
      jointFrame.updateMatrixWorld(true);
      jointFrame.add(proxy);
      lastValue = next.value;
      placeProxyForJoint(next.value);
      gizmo.setSpace("local");
      gizmo.setMode(next.motion === "turn" ? "rotate" : "translate");
      gizmo.showX = false;
      gizmo.showY = false;
    }
    gizmo.attach(proxy);
    requestRender();
  }

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
    for (const p of interference) for (const id of [...p.a, ...p.b]) hot.add(id);
    for (const e of entries.values()) {
      if (!e.body) continue;
      // the part keeps its own color; a clash shows as red edges and the red overlap block
      const face = e.material?.shiny ? (e.material.color ?? e.color ?? colors.face) : (e.color ?? colors.face);
      const edge = e.id === selected || arriving ? colors.blue : hot.has(e.id) ? colors.red : colors.edge;
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
    requestRender();
  }

  // ---- interference: a red block where each clashing pair's world boxes overlap
  const overlapRoot = new THREE.Group();
  scene.add(overlapRoot);
  const unitBox = new THREE.BoxGeometry(1, 1, 1);
  const unitEdges = new THREE.EdgesGeometry(unitBox);
  const overlapFill = new THREE.MeshBasicMaterial({ color: colors.red, transparent: true, opacity: 0.45, depthWrite: false });
  const overlapLine = new THREE.LineBasicMaterial({ color: colors.red });

  function rebuildOverlaps(): void {
    overlapFill.color.set(colors.red);
    overlapLine.color.set(colors.red);
    for (const c of [...overlapRoot.children]) c.removeFromParent();
    for (const p of interference) {
      const a = new THREE.Box3(); const b = new THREE.Box3();
      for (const id of p.a) { const e = entries.get(id); if (e?.body) a.expandByObject(e.group); }
      for (const id of p.b) { const e = entries.get(id); if (e?.body) b.expandByObject(e.group); }
      const both = a.intersect(b);
      if (both.isEmpty()) continue;
      const size = both.getSize(new THREE.Vector3()).max(new THREE.Vector3(0.2, 0.2, 0.2));
      const g = new THREE.Group();
      g.position.copy(both.getCenter(new THREE.Vector3()));
      g.scale.copy(size);
      g.add(new THREE.Mesh(unitBox, overlapFill), new THREE.LineSegments(unitEdges, overlapLine));
      g.renderOrder = 5;
      overlapRoot.add(g);
    }
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

  // ---- markers (picked faces, hover)

  function markerObject(m: PickMarker): THREE.Object3D {
    const g = new THREE.Group();
    const e = entries.get(m.instanceId);
    const size = e?.template ? Math.max(2, e.template.bbox.getSize(new THREE.Vector3()).length() * 0.08) : 4;
    const mat = new THREE.LineBasicMaterial({ color: colors.blue, depthTest: false, transparent: true, opacity: m.role === "hover" ? 0.6 : 1 });
    const pts: THREE.Vector3[] = [];
    for (let i = 0; i <= 48; i++) { const t = (i / 48) * Math.PI * 2; pts.push(new THREE.Vector3(Math.cos(t) * size, Math.sin(t) * size, 0)); }
    const ring = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), mat);
    const stem = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(0, 0, size * 2)]), mat);
    g.add(ring, stem);
    if (m.role !== "hover") {
      const dot = new THREE.Mesh(new THREE.CircleGeometry(size * 0.35, 24), new THREE.MeshBasicMaterial({ color: colors.blue, depthTest: false, side: THREE.DoubleSide }));
      g.add(dot);
    }
    g.renderOrder = 10;
    g.traverse((o) => { o.renderOrder = 10; });
    const z = new THREE.Vector3(...m.axis).normalize();
    g.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), z);
    g.position.set(...m.origin);
    const holder = new THREE.Group();
    holder.matrixAutoUpdate = false;
    if (e) holder.matrix.copy(e.group.matrix);
    holder.userData["instanceId"] = m.instanceId;
    holder.add(g);
    return holder;
  }

  function rebuildMarkers(): void {
    for (const c of [...markerRoot.children]) {
      c.traverse((o) => {
        if (o instanceof THREE.Line || o instanceof THREE.Mesh) { o.geometry.dispose(); (o.material as THREE.Material).dispose(); }
      });
      c.removeFromParent();
    }
    for (const m of [...markers, ...(hoverMarker ? [hoverMarker] : [])]) markerRoot.add(markerObject(m));
    requestRender();
  }

  function syncMarkerPoses(): void {
    for (const c of markerRoot.children) {
      const e = entries.get(c.userData["instanceId"] as string);
      if (e) c.matrix.copy(e.group.matrix);
    }
  }

  // ---- labels

  const box = new THREE.Box3();
  const box2 = new THREE.Box3();
  function labels(): OverlayLabel[] {
    const out: OverlayLabel[] = [];
    interference.forEach((p, i) => {
      const a = p.a.map((id) => entries.get(id)?.group).filter(Boolean) as THREE.Group[];
      const b = p.b.map((id) => entries.get(id)?.group).filter(Boolean) as THREE.Group[];
      if (!a.length || !b.length) return;
      box.makeEmpty(); for (const g of a) box.expandByObject(g);
      box2.makeEmpty(); for (const g of b) box2.expandByObject(g);
      const both = box.clone().intersect(box2);
      const c = (both.isEmpty() ? box.union(box2) : both).getCenter(new THREE.Vector3()).project(camera);
      out.push({ key: `${i}`, x: ((c.x + 1) / 2) * width, y: ((1 - c.y) / 2) * height, text: p.text });
    });
    return out;
  }

  // ---- picking

  const ray = new THREE.Raycaster();
  const ndc = new THREE.Vector2();

  function hit(ev: PointerEvent): { id: string; point: Vec3; normal: Vec3 } | null {
    const r = canvas.getBoundingClientRect();
    ndc.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const meshes: THREE.Object3D[] = [];
    for (const e of entries.values()) e.body?.traverse((o) => { if (o instanceof THREE.Mesh) meshes.push(o); });
    const h = ray.intersectObjects(meshes, false)[0];
    if (!h || !h.face) return null;
    let o: THREE.Object3D | null = h.object;
    while (o && o.userData["instanceId"] === undefined) o = o.parent;
    const id = o?.userData["instanceId"] as string | undefined;
    const e = id ? entries.get(id) : undefined;
    if (!e) return null;
    const inv = e.group.matrixWorld.clone().invert();
    const p = h.point.clone().applyMatrix4(inv);
    const n = h.face.normal.clone().transformDirection(h.object.matrixWorld).transformDirection(inv);
    const r3 = (v: THREE.Vector3): Vec3 => [Number(v.x.toFixed(4)), Number(v.y.toFixed(4)), Number(v.z.toFixed(4))];
    return { id: e.id, point: r3(p), normal: r3(n.normalize()) };
  }

  let down: { x: number; y: number; onGizmo: boolean } | null = null;
  const onDown = (ev: PointerEvent) => { down = { x: ev.clientX, y: ev.clientY, onGizmo: gizmo.axis !== null }; };
  const onUp = (ev: PointerEvent) => {
    const d = down;
    down = null;
    if (!d || d.onGizmo || justDragged) { justDragged = false; return; }
    if (Math.hypot(ev.clientX - d.x, ev.clientY - d.y) > 4) return; // an orbit, not a click
    const h = hit(ev);
    if (picking) { if (h) cb.onPick(h.id, h.point, h.normal); return; }
    cb.onSelect(h?.id ?? null);
  };
  let moveFrame = 0;
  let lastMove: PointerEvent | null = null;
  const onMove = (ev: PointerEvent) => {
    if (!picking) return;
    lastMove = ev;
    if (moveFrame) return;
    moveFrame = requestAnimationFrame(() => {
      moveFrame = 0;
      if (!lastMove || !picking) return;
      const h = hit(lastMove);
      const next = h ? cb.onHoverPick(h.id, h.point, h.normal) : cb.onHoverPick(null, [0, 0, 0], [0, 0, 1]);
      const same = next && hoverMarker && next.instanceId === hoverMarker.instanceId && next.origin.join() === hoverMarker.origin.join() && next.axis.join() === hoverMarker.axis.join();
      if (same || (!next && !hoverMarker)) return;
      hoverMarker = next;
      canvas.style.cursor = next ? "crosshair" : "";
      rebuildMarkers();
    });
  };
  canvas.addEventListener("pointerdown", onDown);
  canvas.addEventListener("pointerup", onUp);
  canvas.addEventListener("pointermove", onMove);

  // ---- theme
  const onTheme = () => { colors = tokens(); gizmoColors(); restyle(); rebuildMarkers(); };
  const themeObserver = typeof MutationObserver === "function" && typeof document !== "undefined" ? new MutationObserver(onTheme) : null;
  themeObserver?.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  const osScheme = typeof window !== "undefined" && typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: light)") : null;
  osScheme?.addEventListener("change", onTheme);

  function worldBox(): THREE.Box3 {
    const b = new THREE.Box3();
    for (const e of entries.values()) if (e.body) b.expandByObject(e.group);
    if (b.isEmpty()) return b;
    for (const r of reach) {
      const c = new THREE.Vector3(...r.center);
      b.expandByPoint(c.clone().addScalar(r.radius)).expandByPoint(c.clone().addScalar(-r.radius));
    }
    return b;
  }

  function refitIfNew(): void {
    const key = [...entries.values()].filter((e) => e.body).map((e) => e.id).sort().join(",");
    if (key === fittedKey || key === "") return;
    fittedKey = key;
    root.updateMatrixWorld(true);
    fit();
  }

  function fit(): void {
    const b = worldBox();
    if (b.isEmpty()) return;
    fitOrtho(camera, b, width / height, FIT_MARGIN);
    orbit.target.copy(b.getCenter(new THREE.Vector3()));
    orbit.update();
    requestRender();
  }

  return {
    setInstances(list) {
      const keep = new Set(list.map((i) => i.id));
      for (const [id, e] of entries) {
        if (!keep.has(id)) { disposeBody(e.body); e.group.removeFromParent(); entries.delete(id); }
      }
      for (const inst of list) {
        let e = entries.get(inst.id);
        if (!e) {
          const group = new THREE.Group();
          group.matrixAutoUpdate = false;
          group.userData["instanceId"] = inst.id;
          root.add(group);
          e = { id: inst.id, group, body: null, template: null, color: null, material: null };
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
      }
      restyle();
      refitIfNew();
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
      syncMarkerPoses();
      markerRoot.updateMatrixWorld(true);
      refitIfNew();
      requestRender();
    },
    setSelected(id) { selected = id; restyle(); },
    setInterference(pairs) { interference = [...pairs]; restyle(); },
    setArriving(on) { if (arriving !== on) { arriving = on; restyle(); } },
    setReach(list) { reach = [...list]; },
    setPicking(on) {
      picking = on;
      if (!on) { hoverMarker = null; canvas.style.cursor = ""; rebuildMarkers(); }
      applyGizmo(on ? null : spec);
    },
    setMarkers(list) { markers = [...list]; rebuildMarkers(); },
    setGizmo(next) { applyGizmo(next); },
    fit,
    probe() {
      return {
        project(p) {
          const v = new THREE.Vector3(...p).project(camera);
          const r = canvas.getBoundingClientRect();
          return { x: r.left + ((v.x + 1) / 2) * r.width, y: r.top + ((1 - v.y) / 2) * r.height };
        },
        gizmoAxis: () => gizmo.axis,
      };
    },
    resize(w, h) {
      width = Math.max(1, w); height = Math.max(1, h);
      renderer.setSize(width, height, false);
      const b = worldBox();
      if (!b.isEmpty()) fitOrtho(camera, b, width / height, FIT_MARGIN);
      else {
        const halfH = (camera.top - camera.bottom) / 2;
        camera.left = -halfH * (width / height); camera.right = halfH * (width / height);
        camera.updateProjectionMatrix();
      }
      requestRender();
    },
    dispose() {
      disposed = true;
      if (frame) cancelAnimationFrame(frame);
      if (moveFrame) cancelAnimationFrame(moveFrame);
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointerup", onUp);
      canvas.removeEventListener("pointermove", onMove);
      gizmo.detach();
      gizmo.dispose();
      orbit.dispose();
      themeObserver?.disconnect();
      osScheme?.removeEventListener("change", onTheme);
      for (const e of entries.values()) disposeBody(e.body);
      unitBox.dispose(); unitEdges.dispose(); overlapFill.dispose(); overlapLine.dispose();
      entries.clear();
      markers = []; hoverMarker = null;
      rebuildMarkers();
      envTex?.dispose();
      renderer.dispose();
    },
  };
}
