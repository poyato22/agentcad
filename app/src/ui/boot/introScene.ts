// The welcome intro's 3D half: a few hundred small grey cubes fly in from scattered positions and
// assemble, with eased, staggered timing and a soft light sweep, into the one plain grey Mises cube
// — the same three-tone iso cube as the app icon (icon.png): a light top, a mid-tone left face and a
// darker right face, outlined in a crisp near-white (dark) / near-black (light) edge that fades in
// as the cube closes. The component (ui/boot/Intro) owns the title and the final fade; this owns the
// WebGL scene and its RAF loop, and can jump straight to the formed cube (skip).
//
// Rendering is injected (createRenderer) so component tests can drive the sequence without WebGL.
import * as THREE from "three";

/** Lattice size: 6³ = 216 small cubes — "a few hundred", enough to read as one solid cube. */
export const GRID = 6;
/** How long the fly-in + assembly runs before the cube is fully formed. */
export const ASSEMBLE_MS = 2000;
/** Iso view, matching the icon and the viewer. */
export const ISO_AZIMUTH_DEG = 45;
export const ISO_ELEVATION_DEG = 35;

export interface RendererLike {
  render(scene: THREE.Scene, camera: THREE.Camera): void;
  setSize(w: number, h: number, updateStyle?: boolean): void;
  setPixelRatio(r: number): void;
  setClearColor(color: THREE.ColorRepresentation, alpha?: number): void;
  dispose(): void;
}

export interface IntroColors { face: string; edge: string }

export interface IntroOptions {
  colors?: IntroColors;
  /** Default: a real alpha WebGLRenderer. */
  createRenderer?: (canvas: HTMLCanvasElement) => RendererLike;
  now?: () => number;
  raf?: (cb: (t: number) => void) => number;
  caf?: (id: number) => void;
  /** Called once the cube is fully assembled (the component then types the title). */
  onAssembled?: () => void;
}

export interface IntroHandle {
  start(): void;
  /** Jump to the formed cube at once (click / any key while assembling). */
  skip(): void;
  resize(w: number, h: number): void;
  dispose(): void;
}

/** Edge-token fallbacks, used when the CSS tokens can't be read (tests / headless). */
const FALLBACK: IntroColors = { face: "#9a9a9a", edge: "#e6e6e6" };

export function introColors(): IntroColors {
  if (typeof document === "undefined" || typeof getComputedStyle !== "function") return FALLBACK;
  const s = getComputedStyle(document.documentElement);
  const face = s.getPropertyValue("--dim").trim() || FALLBACK.face;
  const edge = s.getPropertyValue("--mesh-edge").trim() || FALLBACK.edge;
  return { face, edge };
}

function defaultRenderer(canvas: HTMLCanvasElement): RendererLike {
  return new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
}

const easeOutCubic = (t: number): number => 1 - Math.pow(1 - t, 3);
const clamp01 = (t: number): number => (t < 0 ? 0 : t > 1 ? 1 : t);

/** A deterministic scatter so the fly-in is identical every launch (no RNG flicker). */
function scatter(i: number): THREE.Vector3 {
  const golden = 2.399963; // golden-angle radians
  const a = i * golden;
  const b = i * golden * 0.5;
  const r = 9 + (i % 7);
  return new THREE.Vector3(Math.cos(a) * r, Math.sin(b) * (r * 0.8), Math.sin(a) * r);
}

interface Brick { mesh: THREE.Mesh; home: THREE.Vector3; from: THREE.Vector3; delay: number; spin: THREE.Euler }

export function createIntro(canvas: HTMLCanvasElement, opts: IntroOptions = {}): IntroHandle {
  const colors = opts.colors ?? introColors();
  const now = opts.now ?? (() => performance.now());
  const raf = opts.raf ?? ((cb) => requestAnimationFrame(cb));
  const caf = opts.caf ?? ((id) => cancelAnimationFrame(id));
  const renderer = (opts.createRenderer ?? defaultRenderer)(canvas);
  renderer.setClearColor(0x000000, 0);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 100);
  const az = (ISO_AZIMUTH_DEG * Math.PI) / 180;
  const el = (ISO_ELEVATION_DEG * Math.PI) / 180;
  const dist = 16;
  camera.position.set(Math.cos(el) * Math.sin(az) * dist, Math.sin(el) * dist, Math.cos(el) * Math.cos(az) * dist);
  camera.lookAt(0, 0, 0);

  // Lighting that gives the icon's three tones: brightest top, mid left, darker right.
  const ambient = new THREE.AmbientLight(0xffffff, 0.55);
  const key = new THREE.DirectionalLight(0xffffff, 1.05);
  key.position.set(-4, 7, 5);
  const rim = new THREE.DirectionalLight(0xffffff, 0.0); // swells during the light sweep
  rim.position.set(6, 2, 4);
  scene.add(ambient, key, rim);

  const group = new THREE.Group();
  scene.add(group);

  const unit = 1;
  const gap = 0.06;
  const step = unit + gap;
  const offset = ((GRID - 1) * step) / 2;
  const faceMat = new THREE.MeshStandardMaterial({ color: new THREE.Color(colors.face), roughness: 0.85, metalness: 0.0 });
  const geo = new THREE.BoxGeometry(unit, unit, unit);

  const bricks: Brick[] = [];
  let i = 0;
  for (let x = 0; x < GRID; x++) {
    for (let y = 0; y < GRID; y++) {
      for (let z = 0; z < GRID; z++) {
        const home = new THREE.Vector3(x * step - offset, y * step - offset, z * step - offset);
        const mesh = new THREE.Mesh(geo, faceMat);
        const from = scatter(i);
        // Outer bricks arrive last so the cube fills from the centre out.
        const edgeDist = Math.max(Math.abs(x - (GRID - 1) / 2), Math.abs(y - (GRID - 1) / 2), Math.abs(z - (GRID - 1) / 2));
        const delay = (edgeDist / (GRID / 2)) * 0.45 + ((i % 5) / 5) * 0.12;
        const spin = new THREE.Euler((i % 3) * 1.1, (i % 4) * 0.9, (i % 2) * 1.4);
        mesh.position.copy(from);
        group.add(mesh);
        bricks.push({ mesh, home, from, delay: Math.min(delay, 0.85), spin });
        i++;
      }
    }
  }

  // The crisp outer outline (icon edge), faded in as the cube closes.
  const side = GRID * step - gap;
  const edges = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(side, side, side)),
    new THREE.LineBasicMaterial({ color: new THREE.Color(colors.edge), transparent: true, opacity: 0 }),
  );
  group.add(edges);

  let rafId = 0;
  let t0 = 0;
  let running = false;
  let assembled = false;

  function place(p: number): void {
    const prog = easeOutCubic(clamp01(p));
    for (const b of bricks) {
      const local = clamp01((prog - b.delay) / (1 - b.delay || 1));
      const e = easeOutCubic(local);
      b.mesh.position.lerpVectors(b.from, b.home, e);
      b.mesh.rotation.set(b.spin.x * (1 - e), b.spin.y * (1 - e), b.spin.z * (1 - e));
      (b.mesh.material as THREE.MeshStandardMaterial).opacity = 1;
    }
    (edges.material as THREE.LineBasicMaterial).opacity = easeOutCubic(clamp01((prog - 0.75) / 0.25));
    // Soft light sweep: the rim light swells through the middle of the assembly.
    rim.intensity = Math.sin(clamp01(prog) * Math.PI) * 0.6;
    group.rotation.y = (1 - prog) * 0.5; // a small settle into the iso framing
  }

  function render(): void { renderer.render(scene, camera); }

  function finish(): void {
    if (assembled) return;
    assembled = true;
    place(1);
    render();
    opts.onAssembled?.();
  }

  function frame(t: number): void {
    if (!running) return;
    if (t0 === 0) t0 = t;
    const p = (t - t0) / ASSEMBLE_MS;
    place(p);
    render();
    if (p >= 1) { finish(); return; }
    rafId = raf(frame);
  }

  return {
    start(): void {
      if (running) return;
      running = true;
      t0 = 0;
      place(0);
      render();
      rafId = raf((t) => { t0 = now(); frame(t); });
    },
    skip(): void {
      running = false;
      if (rafId) caf(rafId);
      finish();
    },
    resize(w: number, h: number): void {
      camera.aspect = h > 0 ? w / h : 1;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h, false);
      render();
    },
    dispose(): void {
      running = false;
      if (rafId) caf(rafId);
      geo.dispose();
      faceMat.dispose();
      (edges.geometry as THREE.BufferGeometry).dispose();
      (edges.material as THREE.Material).dispose();
      renderer.dispose();
    },
  };
}
