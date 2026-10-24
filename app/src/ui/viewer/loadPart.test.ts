import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { loadPart } from "./loadPart";

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
