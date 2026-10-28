// The stress half of the split view: the core's result.glb (per-vertex COLOR_0, unlit, same frame
// as the part's model.glb) as a PartScene the shared viewer (ui/viewer/scene.ts) can show. Feature
// edges match the design half. Plus the theme-aware colors both halves use.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { EDGE_COLOR, EDGE_THRESHOLD_DEG, FACE_COLOR, MM_PER_GLTF_UNIT, type PartScene } from "../../viewer/loadPart";

export interface StressLegend { field: string; units: string; min: number; max: number }

function parse(bytes: ArrayBuffer): Promise<{ scene: THREE.Group; extras: unknown }> {
  return new Promise((ok, fail) => {
    new GLTFLoader().parse(
      bytes, "",
      (gltf) => ok({ scene: gltf.scene, extras: (gltf.asset as { extras?: unknown }).extras }),
      (err) => fail(err instanceof Error ? err : new Error(String(err instanceof ErrorEvent ? err.message : err))),
    );
  });
}

function legendOf(extras: unknown): StressLegend | null {
  const m = (extras as { mises_sim?: Record<string, unknown> } | undefined)?.mises_sim;
  if (!m || typeof m.max !== "number") return null;
  return {
    field: typeof m.field === "string" ? m.field : "von_mises", units: typeof m.units === "string" ? m.units : "MPa",
    min: typeof m.min === "number" ? m.min : 0, max: m.max,
  };
}

export async function loadStress(bytes: ArrayBuffer, edgeColor: string = EDGE_COLOR): Promise<{ scene: PartScene; legend: StressLegend | null }> {
  const { scene: source, extras } = await parse(bytes);
  const object = new THREE.Group();
  object.add(source);
  object.scale.setScalar(MM_PER_GLTF_UNIT); // same transform as loadPart: metres → mm, Y-up → Z-up
  object.rotation.x = Math.PI / 2;
  const meshes: THREE.Mesh[] = [];
  source.traverse((o) => { if (o instanceof THREE.Mesh) meshes.push(o); });
  if (meshes.length === 0) throw new Error("result model has no geometry");
  for (const mesh of meshes) {
    if (!mesh.geometry.getAttribute("color")) throw new Error("result model has no stress colors");
    const old = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const m of old) m.dispose();
    mesh.material = new THREE.MeshBasicMaterial({
      vertexColors: true, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1,
    });
    const edges = new THREE.LineSegments(
      new THREE.EdgesGeometry(mesh.geometry, EDGE_THRESHOLD_DEG),
      new THREE.LineBasicMaterial({ color: edgeColor }),
    );
    edges.userData["edges"] = true;
    mesh.add(edges);
  }
  object.updateMatrixWorld(true);
  const bbox = new THREE.Box3().setFromObject(object);
  let disposed = false;
  return {
    legend: legendOf(extras),
    scene: {
      object, bbox,
      dispose() {
        if (disposed) return;
        disposed = true;
        object.traverse((o) => {
          if (o instanceof THREE.Mesh || o instanceof THREE.LineSegments) {
            o.geometry.dispose();
            for (const m of Array.isArray(o.material) ? o.material : [o.material]) m.dispose();
          }
        });
        object.removeFromParent();
      },
    },
  };
}

// ---- theme

export type SceneTheme = "light" | "dark";
export interface SceneColors { edge: string; face: string }

const prefersLight = () => typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: light)").matches;

/** The palette in effect: <html data-theme>, with "system" following the OS. */
export function resolvedTheme(osLight: () => boolean = prefersLight): SceneTheme {
  const t = typeof document === "undefined" ? null : document.documentElement.getAttribute("data-theme");
  if (t === "light" || t === "dark") return t;
  return osLight() ? "light" : "dark";
}

/** Dark keeps the design viewer's colors; light uses the light palette's dim ink and panel grey. */
export function sceneColors(t: SceneTheme): SceneColors {
  return t === "light" ? { edge: "#4a4a4a", face: "#ececea" } : { edge: EDGE_COLOR, face: FACE_COLOR };
}

/** Face color of a loadPart() scene (its edges follow the viewer's edgeColor dep). */
export function tintDesign(p: PartScene, c: SceneColors): void {
  p.object.traverse((o) => {
    if (o instanceof THREE.Mesh && o.material instanceof THREE.MeshLambertMaterial) o.material.color.set(c.face);
  });
}

/** Calls `cb` when the palette may have changed (data-theme attribute or the OS scheme). */
export function watchTheme(cb: () => void): () => void {
  const mo = typeof MutationObserver === "undefined" ? null : new MutationObserver(cb);
  mo?.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  const mq = typeof matchMedia === "function" ? matchMedia("(prefers-color-scheme: light)") : null;
  mq?.addEventListener?.("change", cb);
  return () => { mo?.disconnect(); mq?.removeEventListener?.("change", cb); };
}
