// GLB bytes → a drafting-style scene object: dark low-contrast faces + light feature edges.
// The exported GLB is metres and Y-up; the scene is mm and Z-up (CAD frame). No textures are ever loaded.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

export const FACE_COLOR = "#2a2a2a";
export const EDGE_COLOR = "#e6e6e6";
export const EDGE_THRESHOLD_DEG = 20;

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
  for (const mesh of meshes) {
    const old = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const m of old) m.dispose();
    mesh.material = new THREE.MeshLambertMaterial({
      color: FACE_COLOR, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1,
    });
    const edges = new THREE.LineSegments(
      new THREE.EdgesGeometry(mesh.geometry, EDGE_THRESHOLD_DEG),
      new THREE.LineBasicMaterial({ color: EDGE_COLOR }),
    );
    edges.userData["edges"] = true;
    mesh.add(edges);
  }
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
