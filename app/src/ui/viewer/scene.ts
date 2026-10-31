// Three.js scene for the viewer: orthographic iso camera over a transparent canvas (the CSS drafting
// grid shows through), rendered on demand only.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { RectAreaLightUniformsLib } from "three/addons/lights/RectAreaLightUniformsLib.js";
import type { MaterialParams } from "../../lib/appearance";
import { EDGE_COLOR, meshColors, type MeshColors, type PartScene } from "./loadPart";

export { EDGE_COLOR };
/** Dark-theme default; the live highlight is the theme's --blue token. */
export const HIGHLIGHT_COLOR = "#3d8bff";

interface ViewerColors extends MeshColors { highlight: string }

function viewerColors(): ViewerColors {
  let blue = "";
  if (typeof document !== "undefined" && typeof getComputedStyle === "function") {
    blue = getComputedStyle(document.documentElement).getPropertyValue("--blue").trim();
  }
  return { ...meshColors(), highlight: blue || HIGHLIGHT_COLOR };
}
export const ISO_AZIMUTH_DEG = 45;
export const ISO_ELEVATION_DEG = 35;

/** One part on the sheet: its model, its color (null: the theme's face grey) and whether it is
 *  the focused part (the one the camera fits and the highlight applies to). */
export interface ShownPart {
  id: string; scene: PartScene; color: string | null; focus: boolean;
  /** Plan 7 U8: the part's look (lib/appearance materialOf); absent = the plain drafting material. */
  material?: MaterialParams;
}

/** A click on a shown part: where (world = the part's CAD frame, mm) and the surface normal. */
export interface ViewerHit { partId: string; point: [number, number, number]; normal: [number, number, number] }

/** The blue pick marker: a face (drawn on the triangles in that plane), an edge segment, or a
 *  circular edge (a ring around `axis`). */
export type PickMarker =
  | { kind: "face"; partId: string; normal: [number, number, number]; point: [number, number, number]; tol: number }
  | { kind: "edge"; a: [number, number, number]; b: [number, number, number] }
  | { kind: "ring"; center: [number, number, number]; axis: [number, number, number]; radius: number }
  | null;

export interface ViewerHandle {
  setPart(p: PartScene | null): void;
  setMode(m: "shaded" | "wire"): void;
  /** Edges turn blue while an agent is changing this part. */
  setHighlight(on: boolean): void;
  fit(): void;
  resize(w: number, h: number): void;
  dispose(): void;
}

/** The Design viewer's extras: several parts, picking and the pick marker. */
export interface DesignViewerHandle extends ViewerHandle {
  /** Several parts at once (the build view). `fit` refits the camera: to the focused part, or with
   *  `frameAll` (and whenever none is focused) to all of them; resizes keep that framing. */
  setParts(list: ShownPart[], fit: boolean, frameAll?: boolean): void;
  /** The part under canvas pixel (x, y), or null. */
  pick(x: number, y: number): ViewerHit | null;
  /** A world point → canvas pixels. */
  toScreen(p: [number, number, number]): [number, number];
  setMarker(m: PickMarker): void;
  /** Several markers at once (measure's picks); replaces setMarker's. */
  setMarkers(list: readonly PickMarker[]): void;
  /** Called after every frame (the camera moved, the canvas resized): HTML overlays re-place
   *  themselves with toScreen. Returns the unsubscribe. */
  onView(cb: () => void): () => void;
  /** Re-reads the edge color (deps.edgeColor) and repaints: the background changed. */
  restyle?(): void;
  /** The scene internals the view modes (modes/: X-ray, section, explode, views) drive. Optional:
   *  test doubles of the handle leave it out and the modes stay off. */
  modeHost?(): ModeHost;
  /** FW-layout: CSS px of the canvas the floating surfaces cover at each side; the model is
   *  framed and centred in the rest (ui/shell/insets). Optional for test doubles. */
  setInsets?(left: number, right: number): void;
}

/** What the view modes (ui/viewer/modes) need from a viewer: its scene, camera and parts, a way to
 *  repaint, and a call after every restyle (parts set, shaded / wire, highlight, theme) so the modes
 *  re-apply onto fresh materials. */
export interface ModeHost {
  scene: THREE.Scene;
  camera: THREE.OrthographicCamera;
  controls: OrbitControls;
  canvas: HTMLCanvasElement;
  parts(): readonly ShownPart[];
  requestRender(): void;
  onRestyle(cb: () => void): () => void;
  /** Section planes clip per material (needs the renderer's local clipping). */
  enableLocalClipping(): void;
}

/** The slice of WebGLRenderer the viewer uses (lets tests stub it). */
export interface RendererLike {
  info: { memory: { geometries: number; textures: number } };
  /** WebGLRenderer's per-material clipping switch (section planes); stubs may leave it out. */
  localClippingEnabled?: boolean;
  render(scene: THREE.Scene, camera: THREE.Camera): void;
  setSize(w: number, h: number, updateStyle?: boolean): void;
  setPixelRatio(r: number): void;
  setClearColor(color: THREE.ColorRepresentation, alpha?: number): void;
  dispose(): void;
}

export interface ViewerDeps {
  createRenderer?: (canvas: HTMLCanvasElement) => RendererLike;
  /** Edge color when not highlighted (default: the --mesh-edge token). Read on every restyle (setPart /
   *  setMode / setHighlight / theme change). */
  edgeColor?: () => string | null | undefined;
  /** The environment map shiny parts reflect (default: RoomEnvironment through PMREM, turned Z-up on a real
   *  WebGLRenderer, none on a stub). Built once, on the first shiny part. */
  environment?: () => THREE.Texture | null;
}

function defaultRenderer(canvas: HTMLCanvasElement): RendererLike {
  // preserveDrawingBuffer: frames are rare (on demand), and it lets tests and screenshots read pixels back.
  // stencil: the section caps (modes/section.ts) fill the cut by stencil parity
  return new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: true, stencil: true });
}

/**
 * Sizes the ortho frustum so every corner of `bbox` lies inside NDC [-1,1] with `margin`, keeping the
 * camera's current viewing direction. Moves the camera back along that direction and looks at the center.
 */
export function fitOrtho(camera: THREE.OrthographicCamera, bbox: THREE.Box3, aspect: number, margin = 1.15): void {
  const center = bbox.getCenter(new THREE.Vector3());
  const radius = Math.max(bbox.getSize(new THREE.Vector3()).length() / 2, 1e-3);
  const dir = camera.position.clone().sub(center);
  if (dir.lengthSq() < 1e-12) dir.set(1, -1, 1);
  dir.normalize();
  camera.zoom = 1;
  camera.position.copy(center).addScaledVector(dir, radius * 4);
  camera.lookAt(center);
  camera.updateMatrixWorld(true);
  const inv = camera.matrixWorldInverse.copy(camera.matrixWorld).invert();
  let maxX = 0; let maxY = 0;
  const v = new THREE.Vector3();
  for (const x of [bbox.min.x, bbox.max.x]) for (const y of [bbox.min.y, bbox.max.y]) for (const z of [bbox.min.z, bbox.max.z]) {
    v.set(x, y, z).applyMatrix4(inv);
    maxX = Math.max(maxX, Math.abs(v.x));
    maxY = Math.max(maxY, Math.abs(v.y));
  }
  const halfH = Math.max(maxY, maxX / Math.max(aspect, 1e-3), 1e-3) * margin;
  camera.left = -halfH * aspect; camera.right = halfH * aspect;
  camera.top = halfH; camera.bottom = -halfH;
  camera.near = radius * 0.5; camera.far = radius * 8;
  camera.updateProjectionMatrix();
}

/** FW-layout (D20): floating surfaces cover `insetL` / `insetR` CSS px at the sides of a canvas
 *  `width` × `height`. The frustum keeps its height (the scale) and shifts sideways so the camera's
 *  axis, where a fit puts the model, sits in the middle of what is left. Zoom and pan still work
 *  around that axis. */
export function offsetOrtho(camera: THREE.OrthographicCamera, width: number, height: number, insetL: number, insetR: number): void {
  const halfH = (camera.top - camera.bottom) / 2;
  const s = (2 * halfH) / Math.max(1, height);
  const cx = insetL + visibleWidth(width, insetL, insetR) / 2;
  camera.left = -cx * s; camera.right = (width - cx) * s;
  camera.updateProjectionMatrix();
}

/** The canvas width the floating surfaces leave (at least a third of it). */
export function visibleWidth(width: number, insetL: number, insetR: number): number {
  return Math.max(width / 3, width - insetL - insetR, 1);
}

/** Ambient light for plain (drafting) parts; with a shiny part on the sheet it drops to
 *  SHINY_AMBIENT (a flat ambient washes PBR finishes out) and plain Lambert faces get the
 *  difference back as emissive, so they look exactly as before. */
export const AMBIENT = 1.1;
export const SHINY_AMBIENT = 0.5;
/** The highlight light (shiny parts only): a small softbox above and across from the iso camera,
 *  sized and placed from the framed box, so flat faces under the orthographic camera show a
 *  highlight that moves across them instead of one flat reflected color. */
export const SOFTBOX = { intensity: 10, width: 0.5, height: 0.2, distance: 0.8, dir: [-0.3, 0.7, 0.6] as const };

let rectLib = false;
/** RectAreaLight needs its lookup tables once per page. */
function initRectLights(): void {
  if (rectLib) return;
  rectLib = true;
  try { RectAreaLightUniformsLib.init(); } catch { /* no WebGL tables: the softbox is just dark */ }
}

export function createViewer(canvas: HTMLCanvasElement, deps: ViewerDeps = {}): DesignViewerHandle {
  const renderer = (deps.createRenderer ?? defaultRenderer)(canvas);
  const edgeColor = deps.edgeColor;
  renderer.setClearColor(0x000000, 0);
  renderer.setPixelRatio(typeof window === "undefined" ? 1 : Math.min(window.devicePixelRatio || 1, 2));

  const scene = new THREE.Scene();
  const ambient = new THREE.AmbientLight(0xffffff, AMBIENT);
  scene.add(ambient);
  const key = new THREE.DirectionalLight(0xffffff, 0.9);
  key.position.set(-1, -2, 3);
  scene.add(key);
  // Filmic tone mapping for the PBR finishes; every plain material opts out (toneMapped: false),
  // so the drafting look is unchanged.
  if (renderer instanceof THREE.WebGLRenderer) {
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1;
  }
  let softbox: THREE.RectAreaLight | null = null;

  const camera = new THREE.OrthographicCamera(-50, 50, 50, -50, 0.1, 1000);
  camera.up.set(0, 0, 1);
  const az = THREE.MathUtils.degToRad(ISO_AZIMUTH_DEG);
  const el = THREE.MathUtils.degToRad(ISO_ELEVATION_DEG);
  const isoDir = new THREE.Vector3(Math.cos(el) * Math.sin(az), -Math.cos(el) * Math.cos(az), Math.sin(el));
  camera.position.copy(isoDir).multiplyScalar(200);
  camera.lookAt(0, 0, 0);


  let width = 1; let height = 1;
  let insetL = 0; let insetR = 0;
  const frameView = (box: THREE.Box3) => {
    fitOrtho(camera, box, visibleWidth(width, insetL, insetR) / height);
    offsetOrtho(camera, width, height, insetL, insetR);
  };
  let items: ShownPart[] = [];
  let mode: "shaded" | "wire" = "shaded";
  let highlight = false;
  let frame = 0;
  let disposed = false;
  let marker: THREE.Group | null = null;
  const viewListeners = new Set<() => void>();
  let frameAll = false;

  const draw = () => {
    frame = 0;
    if (disposed) return;
    try { renderer.render(scene, camera); } catch { /* the canvas never throws */ }
    for (const cb of [...viewListeners]) { try { cb(); } catch { /* an overlay never breaks a frame */ } }
  };
  const requestRender = () => {
    if (frame || disposed) return;
    frame = requestAnimationFrame(draw);
  };

  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = false;
  controls.addEventListener("change", requestRender);
  /** The user turned, panned or zoomed since the last fit: a fold then only slides the view. */
  let moved = false;
  controls.addEventListener("start", () => { moved = true; });

  const onLost = (e: Event) => { e.preventDefault(); };
  const onRestored = () => { requestRender(); };
  canvas.addEventListener("webglcontextlost", onLost);
  canvas.addEventListener("webglcontextrestored", onRestored);

  let colors = viewerColors();
  const restyled = new Set<() => void>();

  // ---- materials (Plan 7 U8, R22): a part with an appearance gets a physically based material lit
  // by Three's RoomEnvironment (turned Z-up, through PMREM) and the softbox, so metals reflect and
  // flat faces carry a highlight; a plain part keeps loadPart's matte drafting material.

  let envTex: THREE.Texture | null | undefined;
  function environment(): THREE.Texture | null {
    if (envTex !== undefined) return envTex;
    envTex = null;
    try {
      if (deps.environment) envTex = deps.environment();
      else if (renderer instanceof THREE.WebGLRenderer) {
        const pmrem = new THREE.PMREMGenerator(renderer);
        const room = new RoomEnvironment();
        envTex = pmrem.fromScene(room, 0.04).texture;
        room.dispose();
        pmrem.dispose();
      }
    } catch { envTex = null; /* no env map: shiny parts still get the lights */ }
    scene.environment = envTex;
    scene.environmentRotation.x = Math.PI / 2;                    // RoomEnvironment is Y-up; the CAD frame is Z-up
    return envTex;
  }

  /** The mesh's material for this look: swapped between matte Lambert and PBR Standard only when
   *  the kind has to change, then tuned in place. */
  function materialFor(o: THREE.Mesh, look: MaterialParams | undefined): THREE.MeshLambertMaterial | THREE.MeshStandardMaterial {
    const cur = o.material as THREE.MeshLambertMaterial | THREE.MeshStandardMaterial;
    const shiny = !!look?.shiny && !cur.vertexColors;
    if (shiny && !(cur instanceof THREE.MeshStandardMaterial)) {
      environment();
      const next = new THREE.MeshStandardMaterial({ polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });
      cur.dispose();
      o.material = next;
    } else if (!shiny && cur instanceof THREE.MeshStandardMaterial) {
      const next = new THREE.MeshLambertMaterial({ polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });
      cur.dispose();
      o.material = next;
    }
    const m = o.material as THREE.MeshLambertMaterial | THREE.MeshStandardMaterial;
    if (m instanceof THREE.MeshStandardMaterial && look) {
      m.metalness = look.metalness;
      m.roughness = look.roughness;
      const see = look.opacity < 1;
      if (m.transparent !== see) m.needsUpdate = true;
      m.transparent = see;
      m.opacity = look.opacity;
      m.depthWrite = !see;
      // metals live on their reflections: a little more of the environment
      m.envMapIntensity = look.metalness > 0.5 ? 1.3 : 1;
    }
    return m;
  }

  /** Lights for what is on the sheet: with a shiny part, the softbox on and the ambient down
   *  (plain faces get the difference back as emissive). */
  function applyLights(): void {
    const shiny = items.some((i) => i.material?.shiny);
    ambient.intensity = shiny ? SHINY_AMBIENT : AMBIENT;
    if (shiny && !softbox) {
      initRectLights();
      softbox = new THREE.RectAreaLight(0xffffff, SOFTBOX.intensity, 1, 1);
      scene.add(softbox);
      placeSoftbox();
    } else if (!shiny && softbox) {
      scene.remove(softbox);
      softbox.dispose();
      softbox = null;
    }
  }

  function placeSoftbox(): void {
    if (!softbox) return;
    const box = fitBox();
    if (!box) return;
    const c = box.getCenter(new THREE.Vector3());
    const r = Math.max(box.getSize(new THREE.Vector3()).length(), 1e-3);
    softbox.width = r * SOFTBOX.width;
    softbox.height = r * SOFTBOX.height;
    softbox.position.copy(c).addScaledVector(new THREE.Vector3(...SOFTBOX.dir).normalize(), r * SOFTBOX.distance);
    softbox.lookAt(c);
  }

  /** Plain materials (Lambert faces, edges, markers) skip tone mapping. */
  function untoned(m: THREE.Material): void {
    if (!m.toneMapped) return;
    m.toneMapped = false;
    m.needsUpdate = true;
  }

  function applyStyle(): void {
    applyLights();
    const lift = AMBIENT - ambient.intensity;
    for (const it of items) {
      it.scene.object.traverse((o) => {
        if (o instanceof THREE.LineSegments) {
          const lm = o.material as THREE.LineBasicMaterial;
          untoned(lm);
          lm.color.set(highlight && it.focus ? colors.highlight : (edgeColor?.() ?? colors.edge));
        } else if (o instanceof THREE.Mesh) {
          o.userData["partId"] = it.id;
          const m = materialFor(o, it.material);
          m.visible = mode === "shaded";
          // per-vertex colored meshes (the stress heatmap) keep their colors; plain faces take the
          // part's own color, else follow the theme
          if (!m.vertexColors) m.color?.set(it.material?.color ?? it.color ?? colors.face);
          if (m instanceof THREE.MeshLambertMaterial) {
            untoned(m);
            if (!m.vertexColors) { m.emissive.copy(m.color); m.emissiveIntensity = lift; }
          }
        }
      });
    }
    for (const cb of [...restyled]) cb();
  }

  // Follow the theme live: <html data-theme> changes (profile toggle) and OS light/dark changes
  // (theme "system") re-read the tokens and repaint.
  const onTheme = () => { colors = viewerColors(); applyStyle(); setMarkerObjs(lastMarkers); requestRender(); };
  const themeObserver = typeof MutationObserver === "function" && typeof document !== "undefined"
    ? new MutationObserver(onTheme) : null;
  themeObserver?.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  const osScheme = typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: light)") : null;
  osScheme?.addEventListener("change", onTheme);

  /** The box the camera frames: the focused part, else every shown part. */
  function fitBox(): THREE.Box3 | null {
    const f = frameAll ? undefined : items.find((i) => i.focus);
    if (f) return f.scene.bbox;
    if (!items.length) return null;
    const b = new THREE.Box3();
    for (const i of items) b.union(i.scene.bbox);
    return b;
  }

  function fit(): void {
    const box = fitBox();
    if (!box) return;
    moved = false;
    frameView(box);
    controls.target.copy(box.getCenter(new THREE.Vector3()));
    controls.update();
    placeSoftbox();
    requestRender();
  }

  function setParts(list: ShownPart[], refit: boolean, all = false): void {
    frameAll = all;
    const keep = new Set(list.map((i) => i.scene.object));
    for (const it of items) if (!keep.has(it.scene.object)) scene.remove(it.scene.object);
    items = list;
    for (const it of items) if (it.scene.object.parent !== scene) scene.add(it.scene.object);
    applyStyle();
    if (refit) fit();
    requestRender();
  }

  // ---- picking

  const raycaster = new THREE.Raycaster();
  function pick(x: number, y: number): ViewerHit | null {
    const ndc = new THREE.Vector2((x / width) * 2 - 1, -(y / height) * 2 + 1);
    camera.updateMatrixWorld(true);
    raycaster.setFromCamera(ndc, camera);
    const meshes: THREE.Object3D[] = [];
    for (const it of items) it.scene.object.traverse((o) => { if (o instanceof THREE.Mesh) meshes.push(o); });
    const hit = raycaster.intersectObjects(meshes, false)[0];
    if (!hit || !hit.face) return null;
    const n = hit.face.normal.clone().transformDirection(hit.object.matrixWorld).normalize();
    // the side facing the camera (a hit from inside a thin wall reports the far side)
    if (n.dot(raycaster.ray.direction) > 0) n.negate();
    const r = (v: number) => Number(v.toFixed(4)) + 0;
    return { partId: String(hit.object.userData["partId"] ?? ""), point: [r(hit.point.x), r(hit.point.y), r(hit.point.z)], normal: [r(n.x), r(n.y), r(n.z)] };
  }

  function toScreen(p: [number, number, number]): [number, number] {
    camera.updateMatrixWorld(true);
    const v = new THREE.Vector3(...p).project(camera);
    return [((v.x + 1) / 2) * width, ((1 - v.y) / 2) * height];
  }

  // ---- pick marker (blue: the selected face / edge)

  let lastMarkers: readonly PickMarker[] = [];
  function clearMarker(): void {
    if (!marker) return;
    scene.remove(marker);
    marker.traverse((o) => {
      if (o instanceof THREE.Mesh || o instanceof THREE.Line) { o.geometry.dispose(); (o.material as THREE.Material).dispose(); }
    });
    marker = null;
  }

  /** The triangles of a part that lie in the picked face's plane, in world coordinates. */
  function planeTriangles(partId: string, normal: THREE.Vector3, point: THREE.Vector3, tol: number): number[] {
    const out: number[] = [];
    const it = items.find((i) => i.id === partId);
    if (!it) return out;
    const a = new THREE.Vector3(); const b = new THREE.Vector3(); const c = new THREE.Vector3();
    const n = new THREE.Vector3(); const e1 = new THREE.Vector3(); const e2 = new THREE.Vector3();
    it.scene.object.updateMatrixWorld(true);
    it.scene.object.traverse((o) => {
      if (!(o instanceof THREE.Mesh)) return;
      const pos = o.geometry.getAttribute("position");
      if (!pos) return;
      const idx = o.geometry.getIndex();
      const count = idx ? idx.count : pos.count;
      for (let i = 0; i + 2 < count; i += 3) {
        const ia = idx ? idx.getX(i) : i; const ib = idx ? idx.getX(i + 1) : i + 1; const ic = idx ? idx.getX(i + 2) : i + 2;
        a.fromBufferAttribute(pos, ia).applyMatrix4(o.matrixWorld);
        b.fromBufferAttribute(pos, ib).applyMatrix4(o.matrixWorld);
        c.fromBufferAttribute(pos, ic).applyMatrix4(o.matrixWorld);
        n.crossVectors(e1.subVectors(b, a), e2.subVectors(c, a));
        if (n.lengthSq() < 1e-12) continue;
        n.normalize();
        if (Math.abs(n.dot(normal)) < 0.999) continue;
        if (Math.abs(e1.subVectors(a, point).dot(normal)) > tol) continue;
        // lift it a hair off the surface toward the viewer side of the face
        for (const v of [a, b, c]) { v.addScaledVector(normal, tol * 0.5); out.push(v.x, v.y, v.z); }
      }
    });
    return out;
  }

  function markerObj(m: NonNullable<PickMarker>, blue: THREE.Color): THREE.Object3D | null {
    if (m.kind === "edge" || m.kind === "ring") {
      let pts: THREE.Vector3[];
      if (m.kind === "edge") pts = [new THREE.Vector3(...m.a), new THREE.Vector3(...m.b)];
      else {
        const ax = new THREE.Vector3(...m.axis).normalize();
        const u = new THREE.Vector3().crossVectors(ax, Math.abs(ax.z) < 0.9 ? new THREE.Vector3(0, 0, 1) : new THREE.Vector3(1, 0, 0)).normalize();
        const v = new THREE.Vector3().crossVectors(ax, u);
        const c = new THREE.Vector3(...m.center);
        pts = Array.from({ length: 65 }, (_, i) => {
          const t = (i / 64) * Math.PI * 2;
          return c.clone().addScaledVector(u, Math.cos(t) * m.radius).addScaledVector(v, Math.sin(t) * m.radius);
        });
      }
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: blue, depthTest: false, toneMapped: false }));
      line.renderOrder = 10;
      return line;
    }
    const tris = planeTriangles(m.partId, new THREE.Vector3(...m.normal), new THREE.Vector3(...m.point), m.tol);
    if (!tris.length) return null;
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(tris, 3));
    const mesh = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: blue, transparent: true, opacity: 0.6, side: THREE.DoubleSide, depthWrite: false, toneMapped: false }));
    mesh.renderOrder = 10;
    return mesh;
  }

  function setMarkerObjs(list: readonly PickMarker[]): void {
    clearMarker();
    lastMarkers = list;
    const blue = new THREE.Color(colors.highlight);
    const group = new THREE.Group();
    for (const m of list) {
      const o = m ? markerObj(m, blue) : null;
      if (o) group.add(o);
    }
    if (group.children.length) { marker = group; scene.add(group); }
    requestRender();
  }

  return {
    setPart(p) { setParts(p ? [{ id: "", scene: p, color: null, focus: true }] : [], true); },
    setParts,
    setMode(m) { mode = m; applyStyle(); requestRender(); },
    setHighlight(on) { highlight = on; applyStyle(); requestRender(); },
    pick,
    toScreen,
    setMarker(m) { setMarkerObjs(m ? [m] : []); },
    setMarkers(list) { setMarkerObjs(list); },
    onView(cb) { viewListeners.add(cb); return () => { viewListeners.delete(cb); }; },
    restyle() { applyStyle(); requestRender(); },
    fit,
    modeHost: () => ({
      scene, camera, controls, canvas, parts: () => items, requestRender,
      onRestyle(cb) { restyled.add(cb); return () => { restyled.delete(cb); }; },
      enableLocalClipping() { renderer.localClippingEnabled = true; },
    }),
    resize(w, h) {
      width = Math.max(1, w); height = Math.max(1, h);
      renderer.setSize(width, height, false);
      const box = fitBox();
      if (box) frameView(box);
      else offsetOrtho(camera, width, height, insetL, insetR);
      requestRender();
    },
    setInsets(l, r) {
      if (l === insetL && r === insetR) return;
      insetL = l; insetR = r;
      // untouched since the last fit: refit to the new room; else keep the user's view, re-centred
      if (!moved && fitBox()) fit();
      else offsetOrtho(camera, width, height, insetL, insetR);
      requestRender();
    },
    dispose() {
      disposed = true;
      restyled.clear();
      if (frame) cancelAnimationFrame(frame);
      clearMarker();
      viewListeners.clear();
      envTex?.dispose();
      softbox?.dispose();
      controls.dispose();
      themeObserver?.disconnect();
      osScheme?.removeEventListener("change", onTheme);
      canvas.removeEventListener("webglcontextlost", onLost);
      canvas.removeEventListener("webglcontextrestored", onRestored);
      renderer.dispose();
    },
  };
}
