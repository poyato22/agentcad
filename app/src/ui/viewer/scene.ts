// Three.js scene for the viewer: orthographic iso camera over a transparent canvas (the CSS drafting
// grid shows through), rendered on demand only.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
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
export interface ShownPart { id: string; scene: PartScene; color: string | null; focus: boolean }

/** A click on a shown part: where (world = the part's CAD frame, mm) and the surface normal. */
export interface ViewerHit { partId: string; point: [number, number, number]; normal: [number, number, number] }

/** The blue pick marker: a face (drawn on the triangles in that plane) or an edge segment. */
export type PickMarker =
  | { kind: "face"; partId: string; normal: [number, number, number]; point: [number, number, number]; tol: number }
  | { kind: "edge"; a: [number, number, number]; b: [number, number, number] }
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
}

/** The slice of WebGLRenderer the viewer uses (lets tests stub it). */
export interface RendererLike {
  info: { memory: { geometries: number; textures: number } };
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
  edgeColor?: () => string;
}

function defaultRenderer(canvas: HTMLCanvasElement): RendererLike {
  // preserveDrawingBuffer: frames are rare (on demand), and it lets tests and screenshots read pixels back.
  return new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: true });
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

export function createViewer(canvas: HTMLCanvasElement, deps: ViewerDeps = {}): DesignViewerHandle {
  const renderer = (deps.createRenderer ?? defaultRenderer)(canvas);
  const edgeColor = deps.edgeColor;
  renderer.setClearColor(0x000000, 0);
  renderer.setPixelRatio(typeof window === "undefined" ? 1 : Math.min(window.devicePixelRatio || 1, 2));

  const scene = new THREE.Scene();
  scene.add(new THREE.AmbientLight(0xffffff, 1.1));
  const key = new THREE.DirectionalLight(0xffffff, 0.9);
  key.position.set(-1, -2, 3);
  scene.add(key);

  const camera = new THREE.OrthographicCamera(-50, 50, 50, -50, 0.1, 1000);
  camera.up.set(0, 0, 1);
  const az = THREE.MathUtils.degToRad(ISO_AZIMUTH_DEG);
  const el = THREE.MathUtils.degToRad(ISO_ELEVATION_DEG);
  const isoDir = new THREE.Vector3(Math.cos(el) * Math.sin(az), -Math.cos(el) * Math.cos(az), Math.sin(el));
  camera.position.copy(isoDir).multiplyScalar(200);
  camera.lookAt(0, 0, 0);


  let width = 1; let height = 1;
  let items: ShownPart[] = [];
  let mode: "shaded" | "wire" = "shaded";
  let highlight = false;
  let frame = 0;
  let disposed = false;
  let marker: THREE.Object3D | null = null;
  let frameAll = false;

  const draw = () => {
    frame = 0;
    if (disposed) return;
    try { renderer.render(scene, camera); } catch { /* the canvas never throws */ }
  };
  const requestRender = () => {
    if (frame || disposed) return;
    frame = requestAnimationFrame(draw);
  };

  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = false;
  controls.addEventListener("change", requestRender);

  const onLost = (e: Event) => { e.preventDefault(); };
  const onRestored = () => { requestRender(); };
  canvas.addEventListener("webglcontextlost", onLost);
  canvas.addEventListener("webglcontextrestored", onRestored);

  let colors = viewerColors();

  function applyStyle(): void {
    for (const it of items) {
      it.scene.object.traverse((o) => {
        if (o instanceof THREE.LineSegments) {
          (o.material as THREE.LineBasicMaterial).color.set(highlight && it.focus ? colors.highlight : (edgeColor?.() ?? colors.edge));
        } else if (o instanceof THREE.Mesh) {
          o.userData["partId"] = it.id;
          const m = o.material as THREE.MeshLambertMaterial;
          m.visible = mode === "shaded";
          // per-vertex colored meshes (the stress heatmap) keep their colors; plain faces take the
          // part's own color, else follow the theme
          if (!m.vertexColors) m.color?.set(it.color ?? colors.face);
        }
      });
    }
  }

  // Follow the theme live: <html data-theme> changes (profile toggle) and OS light/dark changes
  // (theme "system") re-read the tokens and repaint.
  const onTheme = () => { colors = viewerColors(); applyStyle(); setMarkerObj(lastMarker); requestRender(); };
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
    fitOrtho(camera, box, width / height);
    controls.target.copy(box.getCenter(new THREE.Vector3()));
    controls.update();
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

  let lastMarker: PickMarker = null;
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

  function setMarkerObj(m: PickMarker): void {
    clearMarker();
    lastMarker = m;
    if (!m) { requestRender(); return; }
    const blue = new THREE.Color(colors.highlight);
    if (m.kind === "edge") {
      const g = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(...m.a), new THREE.Vector3(...m.b)]);
      const line = new THREE.Line(g, new THREE.LineBasicMaterial({ color: blue, depthTest: false }));
      line.renderOrder = 10;
      marker = line;
    } else {
      const tris = planeTriangles(m.partId, new THREE.Vector3(...m.normal), new THREE.Vector3(...m.point), m.tol);
      if (!tris.length) { requestRender(); return; }
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(tris, 3));
      const mesh = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: blue, transparent: true, opacity: 0.35, side: THREE.DoubleSide, depthWrite: false }));
      mesh.renderOrder = 10;
      marker = mesh;
    }
    scene.add(marker);
    requestRender();
  }

  return {
    setPart(p) { setParts(p ? [{ id: "", scene: p, color: null, focus: true }] : [], true); },
    setParts,
    setMode(m) { mode = m; applyStyle(); requestRender(); },
    setHighlight(on) { highlight = on; applyStyle(); requestRender(); },
    pick,
    toScreen,
    setMarker: setMarkerObj,
    fit,
    resize(w, h) {
      width = Math.max(1, w); height = Math.max(1, h);
      renderer.setSize(width, height, false);
      const box = fitBox();
      if (box) fitOrtho(camera, box, width / height);
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
      clearMarker();
      controls.dispose();
      themeObserver?.disconnect();
      osScheme?.removeEventListener("change", onTheme);
      canvas.removeEventListener("webglcontextlost", onLost);
      canvas.removeEventListener("webglcontextrestored", onRestored);
      renderer.dispose();
    },
  };
}
