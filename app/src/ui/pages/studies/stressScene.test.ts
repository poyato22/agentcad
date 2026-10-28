import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import * as THREE from "three";
import { afterEach, describe, expect, it } from "vitest";
import { loadPart } from "../../viewer/loadPart";
import { loadStress, resolvedTheme, sceneColors, tintDesign } from "./stressScene";

// Copied into the test realm's ArrayBuffer (GLTFLoader checks `instanceof ArrayBuffer`).
const bytes = (name: string): ArrayBuffer =>
  new Uint8Array(readFileSync(resolve(import.meta.dirname, "../../../../../core/tests/fixtures/ui", name))).buffer as ArrayBuffer;

afterEach(() => { document.documentElement.removeAttribute("data-theme"); });

describe("loadStress (real core result.glb)", () => {
  it("keeps the per-vertex von Mises colors, unlit, in the part's frame (mm, Z-up)", async () => {
    const { scene, legend } = await loadStress(bytes("sim_result.glb"));
    const meshes: THREE.Mesh[] = [];
    scene.object.traverse((o) => { if (o instanceof THREE.Mesh) meshes.push(o); });
    expect(meshes.length).toBeGreaterThan(0);
    for (const m of meshes) {
      const mat = m.material as THREE.MeshBasicMaterial;
      expect(mat).toBeInstanceOf(THREE.MeshBasicMaterial);
      expect(mat.vertexColors).toBe(true);
      expect(m.geometry.getAttribute("color")).toBeDefined();
    }
    // same frame as model.glb: the 40 × 20 × 6 mount plate
    const size = scene.bbox.getSize(new THREE.Vector3());
    expect(size.x).toBeCloseTo(40, 1);
    expect(size.y).toBeCloseTo(20, 1);
    expect(size.z).toBeCloseTo(6, 1);
    const part = await loadPart(bytes("mount_plate.glb"));
    expect(scene.bbox.min.distanceTo(part.bbox.min)).toBeLessThan(0.05);
    expect(legend).toEqual({ field: "von_mises", units: "MPa", min: 0, max: expect.closeTo(10.39, 2) });
    scene.dispose();
    part.dispose();
  });

  it("rejects bytes that are not a model", async () => {
    await expect(loadStress(new Uint8Array([1, 2, 3, 4]).buffer as ArrayBuffer)).rejects.toThrow();
  });
});

describe("theme colors for the 3D views", () => {
  it("follows data-theme, and the OS for system", () => {
    document.documentElement.setAttribute("data-theme", "light");
    expect(resolvedTheme()).toBe("light");
    document.documentElement.setAttribute("data-theme", "dark");
    expect(resolvedTheme()).toBe("dark");
    document.documentElement.setAttribute("data-theme", "system");
    expect(resolvedTheme(() => true)).toBe("light");
    expect(resolvedTheme(() => false)).toBe("dark");
  });

  it("light mode gets dark edges on light faces; dark mode keeps the viewer's colors", async () => {
    expect(sceneColors("dark")).toEqual({ edge: "#e6e6e6", face: "#2a2a2a" });
    expect(sceneColors("light")).toEqual({ edge: "#4a4a4a", face: "#d6d6d2" });
    const part = await loadPart(bytes("mount_plate.glb"));
    tintDesign(part, sceneColors("light"));
    part.object.traverse((o) => {
      if (o instanceof THREE.Mesh) expect((o.material as THREE.MeshLambertMaterial).color.getHexString()).toBe("d6d6d2");
    });
    part.dispose();
  });
});
