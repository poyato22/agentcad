// Small colored-result thumbnails for study cards: one shared offscreen WebGL renderer draws each
// study's result GLB once (iso view, its own colormap), and the PNG data URL is cached by sim_id.
// Requests run one at a time, so a thread with many cards never holds more than one GL scene;
// each scene is disposed as soon as its picture is taken. Without WebGL it answers null.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { MM_PER_GLTF_UNIT } from "../../../viewer/loadPart";
import { fitOrtho, ISO_AZIMUTH_DEG, ISO_ELEVATION_DEG } from "../../../viewer/scene";

export type GlbFetch = (simId: string) => Promise<ArrayBuffer | null>;

const MAX = 48;
const cache = new Map<string, string | null>();
const waiting = new Map<string, Promise<string | null>>();
let chain: Promise<unknown> = Promise.resolve();
let renderer: THREE.WebGLRenderer | null = null;
let noGl = false;

export function cachedThumbnail(simId: string): string | null | undefined { return cache.get(simId); }

/** Tests: forget the cache. */
export function resetThumbnails(): void { cache.clear(); waiting.clear(); }

function remember(simId: string, url: string | null): void {
  cache.delete(simId);
  cache.set(simId, url);
  while (cache.size > MAX) cache.delete(cache.keys().next().value as string);
}

export function thumbnail(simId: string, fetchGlb: GlbFetch, size = 112): Promise<string | null> {
  if (cache.has(simId)) return Promise.resolve(cache.get(simId) ?? null);
  const w = waiting.get(simId);
  if (w) return w;
  const p = (chain = chain.then(() => draw(simId, fetchGlb, size)).catch(() => null)) as Promise<string | null>;
  const done = p.then((url) => { remember(simId, url); waiting.delete(simId); return url; });
  waiting.set(simId, done);
  return done;
}

async function draw(simId: string, fetchGlb: GlbFetch, size: number): Promise<string | null> {
  if (noGl) return null;
  const bytes = await fetchGlb(simId);
  if (!bytes) return null;
  if (!renderer) {
    try {
      const canvas = document.createElement("canvas");
      renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: true });
    } catch { noGl = true; return null; }
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(size, size, false);
  renderer.setClearColor(0x000000, 0);
  const gltf = await new GLTFLoader().parseAsync(bytes, "");
  const scene = new THREE.Scene();
  const root = new THREE.Group();
  root.add(gltf.scene);
  root.scale.setScalar(MM_PER_GLTF_UNIT);
  root.rotation.x = Math.PI / 2;
  gltf.scene.traverse((o) => {
    if (o instanceof THREE.Mesh) {
      for (const m of Array.isArray(o.material) ? o.material : [o.material]) m.dispose();
      o.material = new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide });
    }
  });
  scene.add(root);
  root.updateMatrixWorld(true);
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 1000);
  camera.up.set(0, 0, 1);
  const az = THREE.MathUtils.degToRad(ISO_AZIMUTH_DEG); const el = THREE.MathUtils.degToRad(ISO_ELEVATION_DEG);
  camera.position.set(Math.cos(el) * Math.sin(az), -Math.cos(el) * Math.cos(az), Math.sin(el)).multiplyScalar(100);
  fitOrtho(camera, new THREE.Box3().setFromObject(root), 1, 1.08);
  try {
    renderer.render(scene, camera);
    return renderer.domElement.toDataURL("image/png");
  } finally {
    root.traverse((o) => {
      const m = o as THREE.Mesh;
      m.geometry?.dispose();
      for (const mat of m.material ? (Array.isArray(m.material) ? m.material : [m.material]) : []) mat.dispose();
    });
    renderer.renderLists.dispose();
  }
}
