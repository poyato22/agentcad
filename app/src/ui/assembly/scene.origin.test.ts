// Plan 10: the assembly scene's origin triad and offset line (E19), its marks and the part ↔
// world mapping pins and picks use (E35, E16).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import * as THREE from "three";
import { afterEach, describe, expect, it, vi } from "vitest";
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
const at = (x: number): number[][] => [[1, 0, 0, x], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];

let sc: AssemblyScene | null = null;
afterEach(() => { sc?.dispose(); sc = null; drawn = null; });

describe("Plan 10: the origin, marks and part ↔ world (E19, E35)", () => {
  it("draws a triad at the origin and a dashed line to an instance off it; hides them while parts are moved", async () => {
    sc = createAssemblyScene(document.createElement("canvas"), { onSelect: vi.fn(), onLabels: vi.fn() }, { createRenderer: stub });
    const t = await loadPart(fixtureBytes());
    sc.setInstances([{ id: "a", template: t, color: null }]);
    sc.setPose({ a: at(30) });
    sc.setOffset([30, 0, 0]);
    sc.resize(400, 300);
    await new Promise((r) => setTimeout(r, 40));
    const triad = drawn?.getObjectByName("origin-triad");
    expect(triad?.visible).toBe(true);
    const dashed = drawn?.children.find((o) => o instanceof THREE.Line && o.material instanceof THREE.LineDashedMaterial);
    expect(dashed?.visible).toBe(true);
    sc.setOriginShown(false);
    expect(triad?.visible).toBe(false);
    expect(dashed?.visible).toBe(false);
  });

  it("a part-frame point follows the instance's pose and the body's move; marks are placed every frame", async () => {
    const onLabels = vi.fn();
    sc = createAssemblyScene(document.createElement("canvas"), { onSelect: vi.fn(), onLabels }, { createRenderer: stub });
    const t = await loadPart(fixtureBytes());
    sc.setInstances([{ id: "a", template: t, color: null }]);
    sc.setPose({ a: at(30) });
    expect(sc.partToWorld("a", [1, 2, 3])?.map((v) => Math.round(v * 1000) / 1000)).toEqual([31, 2, 3]);
    // the body moved (as the explode moves it): the point moves with it, and maps back
    const body = sc.modeHost().parts()[0].scene.object;
    body.position.x += 10;
    const w = sc.partToWorld("a", [1, 2, 3]) as number[];
    expect(Math.round(w[0])).toBe(41);
    expect(sc.worldToPart("a", w)?.map((v) => Math.round(v * 1000) / 1000)).toEqual([1, 2, 3]);
    sc.setMarks(() => [{ key: "p1", point: [0, 0, 0], text: "1", kind: "pin" }]);
    sc.resize(400, 300);
    await new Promise((r) => setTimeout(r, 40));
    expect(onLabels).toHaveBeenLastCalledWith([expect.objectContaining({ key: "pin:p1", kind: "pin", text: "1" })]);
  });
});
