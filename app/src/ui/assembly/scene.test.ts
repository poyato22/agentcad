import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import * as THREE from "three";
import { afterEach, describe, expect, it, vi } from "vitest";
import { materialOf } from "../../lib/appearance";
import { loadPart } from "../viewer/loadPart";
import type { RendererLike } from "../viewer/scene";
import { createAssemblyScene, type AssemblyScene } from "./scene";

const fixtureBytes = (): ArrayBuffer =>
  new Uint8Array(readFileSync(resolve(import.meta.dirname, "../../../../core/tests/fixtures/ui/mount_plate.glb"))).buffer as ArrayBuffer;

let drawn: THREE.Scene | null = null;
const stub = (): RendererLike => ({
  info: { memory: { geometries: 0, textures: 0 } }, render: (s: THREE.Object3D) => { drawn = s as THREE.Scene; },
  setSize: vi.fn(), setPixelRatio: vi.fn(), setClearColor: vi.fn(), dispose: vi.fn(),
} as unknown as RendererLike);
const cb = { onSelect: vi.fn(), onPick: vi.fn(), onHoverPick: () => null, onDragStart: vi.fn(), onDrag: vi.fn(), onDragEnd: vi.fn(), onLabels: vi.fn() };

let sc: AssemblyScene | null = null;
afterEach(() => { sc?.dispose(); sc = null; drawn = null; });

function meshesOf(id: string): THREE.Mesh[] {
  const out: THREE.Mesh[] = [];
  drawn?.traverse((o) => { if (o.userData["instanceId"] === id) o.traverse((m) => { if (m instanceof THREE.Mesh) out.push(m); }); });
  return out;
}

describe("assembly scene finishes (I5)", () => {
  it("a polished part is drawn PBR (metal, low roughness); a plain one stays matte", async () => {
    sc = createAssemblyScene(document.createElement("canvas"), cb, { createRenderer: stub });
    const t = await loadPart(fixtureBytes());
    const polished = materialOf({ appearance: { preset: "polished_metal", color: "#b0b4ba", metalness: 1, roughness: 0.15, opacity: 1 } });
    sc.setInstances([
      { id: "a", template: t, color: null, material: polished },
      { id: "b", template: t, color: "#3366aa" },
    ]);
    sc.resize(400, 300);
    await new Promise((r) => setTimeout(r, 50));
    const a = meshesOf("a");
    expect(a.length).toBeGreaterThan(0);
    for (const m of a) {
      const mat = m.material as THREE.MeshStandardMaterial;
      expect(mat).toBeInstanceOf(THREE.MeshStandardMaterial);
      expect(mat.metalness).toBe(1);
      expect(mat.roughness).toBeCloseTo(0.15);
    }
    for (const m of meshesOf("b")) expect(m.material).toBeInstanceOf(THREE.MeshLambertMaterial);
  });
});
