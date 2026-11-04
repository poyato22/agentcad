import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { loadPart, mergeFaceMeshes } from "./loadPart";

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// The core's real GLB fixture. Copied into the test realm's ArrayBuffer: jsdom's global differs from Node's
// and GLTFLoader checks `instanceof ArrayBuffer`.
const fixtureBytes = (): ArrayBuffer =>
  new Uint8Array(readFileSync(resolve(import.meta.dirname, "../../../../core/tests/fixtures/ui/mount_plate.glb"))).buffer as ArrayBuffer;


describe("loadPart", () => {
  it("loadPart_parses_fixture_glb", async () => {
    const p = await loadPart(fixtureBytes());
    const size = p.bbox.getSize(new THREE.Vector3());
    expect(size.x).toBeCloseTo(40, 2);
    expect(size.y).toBeCloseTo(20, 2);
    expect(size.z).toBeCloseTo(6, 2); // thickness 6 per fixtures/ui/parts.json metrics
    let lines = 0;
    let faceColor = "";
    p.object.traverse((o) => {
      if (o instanceof THREE.LineSegments) lines++;
      else if (o instanceof THREE.Mesh) {
        const m = o.material as THREE.MeshLambertMaterial;
        faceColor = m.color.getHexString();
        expect(m.map).toBeNull();
      }
    });
    expect(lines).toBeGreaterThan(0);
    expect(faceColor).toBe("2a2a2a");
    p.dispose();
  });

  it("rejects garbage bytes", async () => {
    await expect(loadPart(new Uint8Array([1, 2, 3, 4]).buffer)).rejects.toThrow();
  });

  it("dispose_releases_geometries", async () => {
    const p = await loadPart(fixtureBytes());
    const geoms: THREE.BufferGeometry[] = [];
    p.object.traverse((o) => {
      if (o instanceof THREE.Mesh || o instanceof THREE.LineSegments) geoms.push(o.geometry as THREE.BufferGeometry);
    });
    expect(geoms.length).toBeGreaterThanOrEqual(2);
    let disposed = 0;
    for (const g of geoms) g.addEventListener("dispose", () => { disposed++; });
    p.dispose();
    expect(disposed).toBe(geoms.length);
  });
});

describe("a part with thousands of faces (a topology-optimized body)", () => {
  it("draws as one mesh and one edge set, with the same triangles, edges and extent", () => {
    // the GLB holds one mesh per B-rep face; a topopt bracket has thousands of tiny faces, so the
    // assembly made thousands of draw calls per part and orbited at ~10 fps
    const root = new THREE.Group();
    root.position.set(5, 0, 0);
    let tris = 0;
    let segs = 0;
    for (let i = 0; i < 300; i++) {
      const g = new THREE.PlaneGeometry(1, 1);
      const m = new THREE.Mesh(g, new THREE.MeshLambertMaterial());
      m.position.set(i % 20, Math.floor(i / 20), 0);
      m.rotation.z = i * 0.1;
      const e = new THREE.LineSegments(new THREE.EdgesGeometry(g, 20), new THREE.LineBasicMaterial());
      e.userData["edges"] = true;
      m.add(e);
      root.add(m);
      tris += 2;
      segs += 4;
    }
    root.updateMatrixWorld(true);
    const before = new THREE.Box3().setFromObject(root);
    mergeFaceMeshes(root);
    root.updateMatrixWorld(true);
    const meshes: THREE.Mesh[] = []; const lines: THREE.LineSegments[] = [];
    root.traverse((o) => { if (o instanceof THREE.LineSegments) lines.push(o); else if (o instanceof THREE.Mesh) meshes.push(o); });
    expect(meshes).toHaveLength(1);
    expect(lines).toHaveLength(1);
    expect(lines[0].userData["edges"]).toBe(true);
    const g = meshes[0].geometry;
    expect((g.getIndex()?.count ?? g.getAttribute("position").count) / 3).toBe(tris);
    expect(lines[0].geometry.getAttribute("position").count / 2).toBe(segs);
    const after = new THREE.Box3().setFromObject(root);
    expect(after.min.distanceTo(before.min)).toBeLessThan(1e-6);
    expect(after.max.distanceTo(before.max)).toBeLessThan(1e-6);
  });

  it("leaves a part with a few faces as it is", () => {
    const root = new THREE.Group();
    for (let i = 0; i < 6; i++) root.add(new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshLambertMaterial()));
    mergeFaceMeshes(root);
    expect(root.children).toHaveLength(6);
  });
});
