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

export interface ViewerHandle {
  setPart(p: PartScene | null): void;
  setMode(m: "shaded" | "wire"): void;
  /** Edges turn blue while an agent is changing this part. */
  setHighlight(on: boolean): void;
  fit(): void;
  resize(w: number, h: number): void;
  dispose(): void;
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

export interface ViewerDeps { createRenderer?: (canvas: HTMLCanvasElement) => RendererLike }

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

export function createViewer(canvas: HTMLCanvasElement, deps: ViewerDeps = {}): ViewerHandle {
  const renderer = (deps.createRenderer ?? defaultRenderer)(canvas);
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
  let part: PartScene | null = null;
  let mode: "shaded" | "wire" = "shaded";
  let highlight = false;
  let frame = 0;
  let disposed = false;

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
    if (!part) return;
    part.object.traverse((o) => {
      if (o instanceof THREE.LineSegments) {
        (o.material as THREE.LineBasicMaterial).color.set(highlight ? colors.highlight : colors.edge);
      } else if (o instanceof THREE.Mesh) {
        const m = o.material as THREE.MeshLambertMaterial;
        m.visible = mode === "shaded";
        m.color?.set(colors.face);
      }
    });
  }

  // Follow the theme live: <html data-theme> changes (profile toggle) and OS light/dark changes
  // (theme "system") re-read the tokens and repaint.
  const onTheme = () => { colors = viewerColors(); applyStyle(); requestRender(); };
  const themeObserver = typeof MutationObserver === "function" && typeof document !== "undefined"
    ? new MutationObserver(onTheme) : null;
  themeObserver?.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  const osScheme = typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: light)") : null;
  osScheme?.addEventListener("change", onTheme);

  function fit(): void {
    if (!part) return;
    fitOrtho(camera, part.bbox, width / height);
    controls.target.copy(part.bbox.getCenter(new THREE.Vector3()));
    controls.update();
    requestRender();
  }

  return {
    setPart(p) {
      if (part) scene.remove(part.object);
      part = p;
      if (p) {
        scene.add(p.object);
        applyStyle();
        fit();
      }
      requestRender();
    },
    setMode(m) { mode = m; applyStyle(); requestRender(); },
    setHighlight(on) { highlight = on; applyStyle(); requestRender(); },
    fit,
    resize(w, h) {
      width = Math.max(1, w); height = Math.max(1, h);
      renderer.setSize(width, height, false);
      if (part) fitOrtho(camera, part.bbox, width / height);
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
      controls.dispose();
      themeObserver?.disconnect();
      osScheme?.removeEventListener("change", onTheme);
      canvas.removeEventListener("webglcontextlost", onLost);
      canvas.removeEventListener("webglcontextrestored", onRestored);
      renderer.dispose();
    },
  };
}
