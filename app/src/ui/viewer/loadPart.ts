// GLB bytes → a drafting-style scene object: low-contrast faces + feature edges in the theme's greys.
// The exported GLB is metres and Y-up; the scene is mm and Z-up (CAD frame). No textures are ever loaded.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

/** Dark-theme defaults; the live colors come from the --mesh-face / --mesh-edge tokens (meshColors). */
export const FACE_COLOR = "#2a2a2a";
export const EDGE_COLOR = "#e6e6e6";
export const EDGE_THRESHOLD_DEG = 20;

export interface MeshColors { face: string; edge: string }

/** Mesh face/edge colors for the current theme, from the CSS tokens (fallback: the dark defaults). */
export function meshColors(): MeshColors {
  let face = "";
  let edge = "";
  if (typeof document !== "undefined" && typeof getComputedStyle === "function") {
    const s = getComputedStyle(document.documentElement);
    face = s.getPropertyValue("--mesh-face").trim();
    edge = s.getPropertyValue("--mesh-edge").trim();
  }
  return { face: face || FACE_COLOR, edge: edge || EDGE_COLOR };
}

/** Over this many face meshes a part is drawn merged (see mergeFaceMeshes). */
export const MERGE_FACES_OVER = 64;

/** A part's GLB has one mesh (and one edge set) per B-rep face. A topology-optimized body has
 *  thousands of tiny faces, so the assembly made thousands of draw calls per part and orbited at
 *  ~10 fps. Over MERGE_FACES_OVER faces, every face mesh under `root` becomes one mesh and every
 *  edge set one LineSegments, in `root`'s frame: the same triangles, edges and look. Picking is
 *  geometric (pick.ts) and needs no per-face meshes. */
export function mergeFaceMeshes(root: THREE.Object3D): void {
  const meshes: THREE.Mesh[] = [];
  root.traverse((o) => { if (o instanceof THREE.Mesh && !(o instanceof THREE.LineSegments)) meshes.push(o); });
  if (meshes.length <= MERGE_FACES_OVER) return;
  root.updateMatrixWorld(true);
  const toRoot = new THREE.Matrix4().copy(root.matrixWorld).invert();
  const faces: THREE.BufferGeometry[] = [];
  const lines: THREE.BufferGeometry[] = [];
  let lineMat: THREE.Material | null = null;
  const m = new THREE.Matrix4();
  for (const mesh of meshes) {
    m.multiplyMatrices(toRoot, mesh.matrixWorld);
    const g = (mesh.geometry.index ? mesh.geometry.toNonIndexed() : mesh.geometry.clone());
    for (const name of Object.keys(g.attributes)) if (name !== "position" && name !== "normal") g.deleteAttribute(name);
    if (!g.getAttribute("normal")) g.computeVertexNormals();
    g.applyMatrix4(m);
    faces.push(g);
    for (const c of mesh.children) {
      if (!(c instanceof THREE.LineSegments)) continue;
      const lg = new THREE.BufferGeometry().setAttribute("position", c.geometry.getAttribute("position").clone());
      lg.applyMatrix4(new THREE.Matrix4().multiplyMatrices(m, c.matrix));
      lines.push(lg);
      lineMat ??= c.material as THREE.Material;
    }
  }
  const material = meshes[0].material;
  const merged = new THREE.Mesh(mergeGeometries(faces), material);
  for (const g of faces) g.dispose();
  if (lines.length && lineMat) {
    const edges = new THREE.LineSegments(mergeGeometries(lines), lineMat);
    edges.userData["edges"] = true;
    merged.add(edges);
    for (const g of lines) g.dispose();
  }
  for (const mesh of meshes) {
    mesh.removeFromParent();
    mesh.geometry.dispose();
    for (const c of mesh.children) if (c instanceof THREE.LineSegments) { c.geometry.dispose(); if (c.material !== lineMat) (c.material as THREE.Material).dispose(); }
    if (mesh.material !== material) (Array.isArray(mesh.material) ? mesh.material : [mesh.material]).forEach((x) => x.dispose());
  }
  root.add(merged);
}

/** glTF units are metres (build123d exports them that way); the scene is in mm. */
export const MM_PER_GLTF_UNIT = 1000;

export interface PartScene { object: THREE.Group; bbox: THREE.Box3; dispose(): void }

function parseGlb(bytes: ArrayBuffer): Promise<THREE.Group> {
  return new Promise((resolve, reject) => {
    new GLTFLoader().parse(
      bytes, "",
      (gltf) => resolve(gltf.scene),
      (err) => reject(err instanceof Error ? err : new Error(String(err instanceof ErrorEvent ? err.message : err))),
    );
  });
}

export async function loadPart(bytes: ArrayBuffer): Promise<PartScene> {
  const source = await parseGlb(bytes);
  const object = new THREE.Group();
  object.add(source);
  object.scale.setScalar(MM_PER_GLTF_UNIT); // glTF is metres; the app works in mm
  object.rotation.x = Math.PI / 2; // glTF is Y-up; the scene (and CAD) is Z-up
  const meshes: THREE.Mesh[] = [];
  source.traverse((o) => { if (o instanceof THREE.Mesh) meshes.push(o); });
  if (meshes.length === 0) throw new Error("model has no geometry");
  const colors = meshColors();
  for (const mesh of meshes) {
    const old = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const m of old) m.dispose();
    mesh.material = new THREE.MeshLambertMaterial({
      color: colors.face, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1,
    });
    const edges = new THREE.LineSegments(
      new THREE.EdgesGeometry(mesh.geometry, EDGE_THRESHOLD_DEG),
      new THREE.LineBasicMaterial({ color: colors.edge }),
    );
    edges.userData["edges"] = true;
    mesh.add(edges);
  }
  mergeFaceMeshes(source);
  object.updateMatrixWorld(true);
  const bbox = new THREE.Box3().setFromObject(object);
  let disposed = false;
  return {
    object, bbox,
    dispose() {
      if (disposed) return;
      disposed = true;
      object.traverse((o) => {
        if (o instanceof THREE.Mesh || o instanceof THREE.LineSegments) {
          o.geometry.dispose();
          const mats = Array.isArray(o.material) ? o.material : [o.material];
          for (const m of mats) m.dispose();
        }
      });
      object.removeFromParent();
    },
  };
}
