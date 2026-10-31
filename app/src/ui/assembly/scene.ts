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
import { fitOrtho, ISO_AZIMUTH_DEG, ISO_ELEVATION_DEG, type ModeHost, type RendererLike, type ShownPart } from "../viewer/scene";
import type { Vec3 } from "./kinematics";

export interface SceneInstance {
  id: string; template: PartScene | null; color: string | null;
  /** Plan 7 U8 finish (lib/appearance materialOf): a shiny one is drawn PBR with the room's
   *  reflections, as in the Part viewer (I5). Absent = the matte drafting look. */
  material?: MaterialParams;
}

export interface OverlayLabel { key: string; x: number; y: number; text: string }

export interface SceneCallbacks {
  /** A click on a part (null: empty space). */
  onSelect(id: string | null): void;
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
  fit(): void;
  /** What the view modes drive (ui/viewer/modes): the instances as shown parts, by instance id. */
  modeHost(): ModeHost;
  /** A world point (mm) → client pixels. Test probe. */
  probe(): { project(p: Vec3): { x: number; y: number } };
  resize(w: number, h: number): void;
  dispose(): void;
}

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

  let width = 1; let height = 1;
  let frame = 0;
  let disposed = false;
  let colors = tokens();
  const entries = new Map<string, Entry>();
  let selected: string | null = null;
  let interference: { a: string[]; b: string[]; text: string }[] = [];
  let arriving = false;
  /** The parts (with geometry) the camera was last fitted to: a part added or removed refits. */
  let fittedKey = "";
  let reach: { center: Vec3; radius: number }[] = [];
  const FIT_MARGIN = 1.15;
  /** The view modes re-apply after every restyle (fresh materials, opacity reset). */
  const restyled = new Set<() => void>();

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

  function hit(ev: PointerEvent | MouseEvent): string | null {
    const r = canvas.getBoundingClientRect();
    ndc.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const meshes: THREE.Object3D[] = [];
    for (const e of entries.values()) e.body?.traverse((o) => { if (o instanceof THREE.Mesh) meshes.push(o); });
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
    cb.onSelect(hit(ev));
  };
  // D6: Fit is a double-click on empty space (or F)
  const onDbl = (ev: MouseEvent) => { if (hit(ev) === null) fit(); };
  canvas.addEventListener("pointerdown", onDown);
  canvas.addEventListener("pointerup", onUp);
  canvas.addEventListener("dblclick", onDbl);

  // ---- theme
  const onTheme = () => { colors = tokens(); restyle(); };
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

  // ---- the view modes' host: each posed instance is one shown part. Its bbox is its template's
  // box through the instance pose (the assembled layout: an explode never moves it).
  const shown = new Map<string, ShownPart>();
  function shownParts(): readonly ShownPart[] {
    const out: ShownPart[] = [];
    for (const e of entries.values()) {
      if (!e.body || !e.template) { shown.delete(e.id); continue; }
      let s = shown.get(e.id);
      if (!s || s.scene.object !== e.body) {
        const body = e.body as THREE.Group;
        const tpl = e.template;
        const group = e.group;
        s = {
          id: e.id, color: e.color, focus: false, ...(e.material ? { material: e.material } : {}),
          scene: { object: body, get bbox() { group.updateMatrixWorld(true); return tpl.bbox.clone().applyMatrix4(group.matrixWorld); }, dispose() { /* the scene's */ } },
        };
        shown.set(e.id, s);
      }
      out.push(s);
    }
    return out;
  }
  const host: ModeHost = {
    scene, camera, controls: orbit, canvas,
    parts: shownParts,
    requestRender,
    onRestyle(f) { restyled.add(f); return () => { restyled.delete(f); }; },
    enableLocalClipping() { renderer.localClippingEnabled = true; },
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
      refitIfNew();
      requestRender();
    },
    setSelected(id) { selected = id; restyle(); },
    setInterference(pairs) { interference = [...pairs]; restyle(); },
    setArriving(on) { if (arriving !== on) { arriving = on; restyle(); } },
    setReach(list) { reach = [...list]; },
    fit,
    modeHost: () => host,
    probe() {
      return {
        project(p) {
          const v = new THREE.Vector3(...p).project(camera);
          const r = canvas.getBoundingClientRect();
          return { x: r.left + ((v.x + 1) / 2) * r.width, y: r.top + ((1 - v.y) / 2) * r.height };
        },
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
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointerup", onUp);
      canvas.removeEventListener("dblclick", onDbl);
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
