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
const cb = { onSelect: vi.fn(), onLabels: vi.fn() };

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

describe("the view modes on the assembly (Plan 9 D17)", () => {
  const at = (x: number): number[][] => [[1, 0, 0, x], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];

  it("each posed instance is a shown part whose box follows its pose", async () => {
    sc = createAssemblyScene(document.createElement("canvas"), cb, { createRenderer: stub });
    const t = await loadPart(fixtureBytes());
    sc.setInstances([{ id: "a", template: t, color: null }, { id: "b", template: t, color: null }]);
    sc.setPose({ a: at(0), b: at(100) });
    const parts = sc.modeHost().parts();
    expect(parts.map((p) => p.id)).toEqual(["a", "b"]);
    const [a, b] = parts.map((p) => p.scene.bbox);
    expect(b.min.x - a.min.x).toBeCloseTo(100);
    // the same objects every call (the modes re-derive their state only when these change)
    expect(sc.modeHost().parts()[0]).toBe(parts[0]);
  });

  it("a section clips every instance and x-ray survives a restyle", async () => {
    const { ModesController } = await import("../viewer/modes/controller");
    const { MODES_OFF } = await import("../viewer/modes/state");
    sc = createAssemblyScene(document.createElement("canvas"), cb, { createRenderer: stub });
    const t = await loadPart(fixtureBytes());
    sc.setInstances([{ id: "a", template: t, color: null }, { id: "b", template: t, color: null }]);
    sc.setPose({ a: at(0), b: at(100) });
    const modes = new ModesController(sc.modeHost());
    modes.set({ ...MODES_OFF, section: { on: true, normal: [1, 0, 0], at: 50 }, xray: { opacity: 0.25, parts: null } });
    for (const id of ["a", "b"]) {
      for (const m of meshesOfScene(sc, id)) {
        expect((m.material as THREE.Material).clippingPlanes?.length).toBe(1);
        expect((m.material as THREE.Material).opacity).toBeCloseTo(0.25);
      }
    }
    sc.setSelected("a");                                   // a restyle resets materials…
    for (const m of meshesOfScene(sc, "a")) expect((m.material as THREE.Material).opacity).toBeCloseTo(0.25);   // …the modes re-apply
    expect(modes.caps.caps()).toHaveLength(2);
    modes.dispose();
  });
});

describe("hidden instances (FW-app I1: the tree's Hide / Isolate drive the build view)", () => {
  const at = (x: number): number[][] => [[1, 0, 0, x], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];

  it("a hidden instance is not drawn and not a shown part for the modes", async () => {
    sc = createAssemblyScene(document.createElement("canvas"), cb, { createRenderer: stub });
    const t = await loadPart(fixtureBytes());
    sc.setInstances([{ id: "a", template: t, color: null }, { id: "b", template: t, color: null, hidden: true }]);
    sc.setPose({ a: at(0), b: at(500) });
    let group: THREE.Object3D | undefined;
    sc.modeHost().scene.traverse((o) => { if (o.userData["instanceId"] === "b") group = o; });
    expect(group?.visible).toBe(false);
    expect(sc.modeHost().parts().map((p) => p.id)).toEqual(["a"]);
    // shown again: back in the modes' parts and drawn
    sc.setInstances([{ id: "a", template: t, color: null }, { id: "b", template: t, color: null }]);
    expect(group?.visible).toBe(true);
    expect(sc.modeHost().parts().map((p) => p.id)).toEqual(["a", "b"]);
  });

  it("a click where only a hidden instance is hits nothing", async () => {
    const onSelect = vi.fn();
    const canvas = document.createElement("canvas");
    canvas.getBoundingClientRect = () => ({ left: 0, top: 0, width: 400, height: 300, right: 400, bottom: 300, x: 0, y: 0, toJSON: () => ({}) });
    canvas.setPointerCapture = () => {}; canvas.releasePointerCapture = () => {};   // the orbit's
    sc = createAssemblyScene(canvas, { onSelect, onLabels: vi.fn() }, { createRenderer: stub });
    const t = await loadPart(fixtureBytes());
    sc.resize(400, 300);
    sc.setInstances([{ id: "a", template: t, color: null }]);
    sc.setPose({ a: at(0) });
    sc.fit();
    const click = () => {
      canvas.dispatchEvent(new PointerEvent("pointerdown", { clientX: 200, clientY: 150 }));
      canvas.dispatchEvent(new PointerEvent("pointerup", { clientX: 200, clientY: 150 }));
    };
    click();
    expect(onSelect).toHaveBeenLastCalledWith("a");
    sc.setInstances([{ id: "a", template: t, color: null, hidden: true }]);
    click();
    expect(onSelect).toHaveBeenLastCalledWith(null);
  });
});

function meshesOfScene(s: AssemblyScene, id: string): THREE.Mesh[] {
  const out: THREE.Mesh[] = [];
  s.modeHost().scene.traverse((o) => { if (o.userData["instanceId"] === id) o.traverse((m) => { if (m instanceof THREE.Mesh) out.push(m); }); });
  return out;
}
