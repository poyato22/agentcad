// Plan 10 E8: the load arrow is a solid, outlined accent arrow (not a 1 px line), its tip on the
// load face; a fixed face is a hatched plate sized from the face.
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { faceSpan, FIXTURE_ACCENT, fixturePlate, LOAD_ACCENT, solidArrow } from "./markers";

describe("study markers (E8)", () => {
  it("an arrow has a thick mesh shaft and head in the load accent, outlined, ending at the face", () => {
    const tail = new THREE.Vector3(0, 0, 50);
    const a = solidArrow(tail, new THREE.Vector3(0, 0, -1), 40, 100, LOAD_ACCENT);
    const meshes: THREE.Mesh[] = [];
    a.traverse((o) => { if (o instanceof THREE.Mesh) meshes.push(o); });
    expect(meshes).toHaveLength(4);
    expect(meshes.some((m) => m.geometry instanceof THREE.CylinderGeometry && (m.material as THREE.MeshBasicMaterial).color.getHexString() === LOAD_ACCENT.slice(1))).toBe(true);
    const shaft = meshes.find((m) => m.geometry instanceof THREE.CylinderGeometry && (m.material as THREE.Material).side !== THREE.BackSide)!;
    expect((shaft.geometry as THREE.CylinderGeometry).parameters.radiusTop).toBeGreaterThanOrEqual(1);   // 1.2% of a 100 mm part
    expect(meshes.some((m) => (m.material as THREE.Material).side === THREE.BackSide)).toBe(true);        // the dark outline
    a.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(a);
    expect(box.min.z).toBeCloseTo(10, 0);         // the tip reaches the face (50 - 40)
    expect(box.max.z).toBeGreaterThan(49);
  });

  it("a fixed face is a hatched plate on the face, sized from it", () => {
    const g = fixturePlate(new THREE.Vector3(0, 0, 0), new THREE.Vector3(1, 0, 0), 20, FIXTURE_ACCENT);
    let strokes = 0;
    g.traverse((o) => { if (o instanceof THREE.Mesh && (o.material as THREE.MeshBasicMaterial).color.getHexString() === FIXTURE_ACCENT.slice(1)) strokes++; });
    expect(strokes).toBeGreaterThanOrEqual(8);    // a frame and the hatch
    expect(faceSpan("x-min", { min: [0, 0, 0], max: [100, 20, 10] }, null, 100)).toBe(10);
    expect(faceSpan("f3", { min: [0, 0, 0], max: [1, 1, 1] }, 400, 100)).toBe(20);
  });
});
