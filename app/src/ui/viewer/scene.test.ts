import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";
import { loadPart } from "./loadPart";
import { createViewer, EDGE_COLOR, HIGHLIGHT_COLOR, fitOrtho, type RendererLike } from "./scene";

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// The core's real GLB fixture. Copied into the test realm's ArrayBuffer: jsdom's global differs from Node's
// and GLTFLoader checks `instanceof ArrayBuffer`.
const fixtureBytes = (): ArrayBuffer =>
  new Uint8Array(readFileSync(resolve(import.meta.dirname, "../../../../core/tests/fixtures/ui/mount_plate.glb"))).buffer as ArrayBuffer;

function stubRenderer(): RendererLike & { renders: number } {
  const r = {
    renders: 0,
    info: { memory: { geometries: 0, textures: 0 } },
    render: () => { r.renders++; },
    setSize: vi.fn(), setPixelRatio: vi.fn(), setClearColor: vi.fn(), dispose: vi.fn(),
  };
  return r;
}

const hex = (c: string) => c.replace("#", "");
function edgeColors(p: { object: THREE.Object3D }): Set<string> {
  const out = new Set<string>();
  p.object.traverse((o) => { if (o instanceof THREE.LineSegments) out.add((o.material as THREE.LineBasicMaterial).color.getHexString()); });
  return out;
}

describe("fitOrtho", () => {
  it("fitOrtho_contains_bbox", () => {
    for (const aspect of [0.5, 1, 2.4]) {
      const bbox = new THREE.Box3(new THREE.Vector3(-20, -10, -3), new THREE.Vector3(20, 10, 3));
      const cam = new THREE.OrthographicCamera(-1, 1, 1, -1);
      cam.up.set(0, 0, 1);
      cam.position.set(1, -1, 0.7);
      fitOrtho(cam, bbox, aspect, 1.1);
      cam.updateMatrixWorld(true);
      for (const x of [bbox.min.x, bbox.max.x]) for (const y of [bbox.min.y, bbox.max.y]) for (const z of [bbox.min.z, bbox.max.z]) {
        const v = new THREE.Vector3(x, y, z).project(cam);
        expect(Math.abs(v.x)).toBeLessThanOrEqual(1);
        expect(Math.abs(v.y)).toBeLessThanOrEqual(1);
        expect(Math.abs(v.z)).toBeLessThanOrEqual(1);
      }
    }
  });
});

describe("createViewer", () => {
  it("setHighlight_changes_edge_color", async () => {
    const v = createViewer(document.createElement("canvas"), { createRenderer: stubRenderer });
    const p = await loadPart(fixtureBytes());
    v.setPart(p);
    expect(edgeColors(p)).toEqual(new Set([hex(EDGE_COLOR)]));
    v.setHighlight(true);
    expect(edgeColors(p)).toEqual(new Set([hex(HIGHLIGHT_COLOR)]));
    v.setHighlight(false);
    expect(edgeColors(p)).toEqual(new Set([hex(EDGE_COLOR)]));
    v.setHighlight(true);
    const p2 = await loadPart(fixtureBytes());
    v.setPart(p2); // a part set while highlighted picks the highlight up
    expect(edgeColors(p2)).toEqual(new Set(["3d8bff"]));
    v.dispose();
  });

  it("edge_color_can_follow_the_theme", async () => {
    let edge = "#4a4a4a";
    const r = stubRenderer();
    const v = createViewer(document.createElement("canvas"), { createRenderer: () => r, edgeColor: () => edge });
    const p = await loadPart(fixtureBytes());
    v.setPart(p);
    expect(edgeColors(p)).toEqual(new Set(["4a4a4a"]));
    const frame = () => new Promise((ok) => requestAnimationFrame(() => ok(null)));
    await frame();
    const before = r.renders;
    edge = "#e6e6e6";
    v.setMode("shaded"); // re-applies the style (theme change) and asks for one frame
    expect(edgeColors(p)).toEqual(new Set(["e6e6e6"]));
    await frame();
    expect(r.renders).toBe(before + 1);
    v.setHighlight(true);
    expect(edgeColors(p)).toEqual(new Set([hex(HIGHLIGHT_COLOR)]));
    v.dispose();
  });

  it("renders_on_demand_only", async () => {
    const r = stubRenderer();
    vi.spyOn(globalThis, "requestAnimationFrame").mockImplementation((cb) => { queueMicrotask(() => cb(0)); return 1; });
    const v = createViewer(document.createElement("canvas"), { createRenderer: () => r });
    await new Promise((res) => setTimeout(res, 20));
    const base = r.renders;
    await new Promise((res) => setTimeout(res, 20));
    expect(r.renders).toBe(base); // idle: no frames
    v.resize(300, 200);
    v.setMode("wire");
    await new Promise((res) => setTimeout(res, 20));
    expect(r.renders).toBe(base + 1); // two requests coalesce into one frame
    v.dispose();
  });

  it("wire mode hides faces but keeps edges", async () => {
    const v = createViewer(document.createElement("canvas"), { createRenderer: stubRenderer });
    const p = await loadPart(fixtureBytes());
    v.setPart(p);
    v.setMode("wire");
    const vis = () => {
      const faces: boolean[] = []; const edges: boolean[] = [];
      p.object.traverse((o) => {
        if (o instanceof THREE.LineSegments) edges.push((o.material as THREE.Material).visible);
        else if (o instanceof THREE.Mesh) faces.push((o.material as THREE.Material).visible);
      });
      return { faces, edges };
    };
    expect(vis().faces.every((x) => !x)).toBe(true);
    expect(vis().edges.every((x) => x)).toBe(true);
    v.setMode("shaded");
    expect(vis().faces.every((x) => x)).toBe(true);
    v.dispose();
  });

  it("never throws when the renderer fails", () => {
    const r = stubRenderer();
    r.render = () => { throw new Error("boom"); };
    const v = createViewer(document.createElement("canvas"), { createRenderer: () => r });
    expect(() => { v.resize(10, 10); v.fit(); }).not.toThrow();
    v.dispose();
  });
});
