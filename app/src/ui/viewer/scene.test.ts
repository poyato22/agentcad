import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";
import { loadPart } from "./loadPart";
import { AMBIENT, createViewer, EDGE_COLOR, HIGHLIGHT_COLOR, fitOrtho, SHINY_AMBIENT, type RendererLike } from "./scene";

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

  it("mesh_colors_come_from_tokens_and_follow_a_theme_change_live", async () => {
    const root = document.documentElement;
    const faceColors = (p: { object: THREE.Object3D }) => {
      const out = new Set<string>();
      p.object.traverse((o) => { if (o instanceof THREE.Mesh && !(o instanceof THREE.LineSegments)) out.add((o.material as THREE.MeshLambertMaterial).color.getHexString()); });
      return out;
    };
    try {
      root.style.setProperty("--mesh-face", "#d6d6d2");
      root.style.setProperty("--mesh-edge", "#4a4a4a");
      root.style.setProperty("--blue", "#0a5bd8");
      const v = createViewer(document.createElement("canvas"), { createRenderer: stubRenderer });
      const p = await loadPart(fixtureBytes());
      v.setPart(p);
      expect(edgeColors(p)).toEqual(new Set(["4a4a4a"]));
      expect(faceColors(p)).toEqual(new Set(["d6d6d2"]));
      v.setHighlight(true);
      expect(edgeColors(p)).toEqual(new Set(["0a5bd8"]));   // the theme's blue
      v.setHighlight(false);
      // the theme flips (profile toggle → data-theme): tokens re-read, no reload
      root.style.setProperty("--mesh-face", "#2a2a2a");
      root.style.setProperty("--mesh-edge", "#e6e6e6");
      root.setAttribute("data-theme", "dark");
      await new Promise((r) => setTimeout(r, 0));             // MutationObserver delivers
      expect(edgeColors(p)).toEqual(new Set(["e6e6e6"]));
      expect(faceColors(p)).toEqual(new Set(["2a2a2a"]));
      v.dispose();
    } finally {
      for (const k of ["--mesh-face", "--mesh-edge", "--blue"]) root.style.removeProperty(k);
      root.removeAttribute("data-theme");
    }
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

  it("vertex_colored_meshes_keep_their_colors_on_restyle", async () => {
    const v = createViewer(document.createElement("canvas"), { createRenderer: stubRenderer });
    const g = new THREE.Group();
    const mat = new THREE.MeshBasicMaterial({ vertexColors: true });
    g.add(new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), mat));
    v.setPart({ object: g, bbox: new THREE.Box3(new THREE.Vector3(-1, -1, -1), new THREE.Vector3(1, 1, 1)), dispose: () => {} });
    v.setMode("shaded");
    expect(mat.color.getHexString()).toBe("ffffff"); // the heatmap is never tinted grey
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

describe("design viewer: part colors, picks and the build view", () => {
  const plateBytes = (): ArrayBuffer =>
    new Uint8Array(readFileSync(resolve(import.meta.dirname, "../../../test/fake-core/scenarios/glb/top_plate.glb"))).buffer as ArrayBuffer;
  const faceColors = (p: { object: THREE.Object3D }): Set<string> => {
    const out = new Set<string>();
    p.object.traverse((o) => { if (o instanceof THREE.Mesh) out.add((o.material as THREE.MeshLambertMaterial).color.getHexString()); });
    return out;
  };

  it("applies the part's color to its mesh material, and the theme grey without one", async () => {
    const v = createViewer(document.createElement("canvas"), { createRenderer: stubRenderer });
    const p = await loadPart(plateBytes());
    v.setParts([{ id: "top_plate", scene: p, color: "#2f9a9c", focus: true }], true);
    expect(faceColors(p)).toEqual(new Set(["2f9a9c"]));
    v.setParts([{ id: "top_plate", scene: p, color: null, focus: true }], false);
    expect(faceColors(p)).toEqual(new Set(["2a2a2a"]));
    v.dispose();
  });

  it("a part with an appearance gets a PBR material lit by the environment; back to plain restores the matte one (U8)", async () => {
    const env = new THREE.Texture();
    const environment = vi.fn(() => env);
    const v = createViewer(document.createElement("canvas"), { createRenderer: stubRenderer, environment });
    const p = await loadPart(plateBytes());
    const mats = () => { const out: THREE.Material[] = []; p.object.traverse((o) => { if (o instanceof THREE.Mesh) out.push(o.material as THREE.Material); }); return out; };
    v.setParts([{ id: "top_plate", scene: p, color: null, focus: true }], true);
    expect(mats().every((m) => m instanceof THREE.MeshLambertMaterial)).toBe(true);
    expect(environment).not.toHaveBeenCalled();                   // built lazily, on the first shiny part
    const chrome = { color: "#e6e8eb", metalness: 1, roughness: 0.06, opacity: 1, shiny: true };
    v.setParts([{ id: "top_plate", scene: p, color: "#e6e8eb", focus: true, material: chrome }], false);
    const m = mats()[0] as THREE.MeshStandardMaterial;
    expect(m).toBeInstanceOf(THREE.MeshStandardMaterial);
    expect([m.metalness, m.roughness, m.transparent, m.color.getHexString()]).toEqual([1, 0.06, false, "e6e8eb"]);
    expect(environment).toHaveBeenCalledTimes(1);
    const clear = { color: "#dde8f0", metalness: 0, roughness: 0.05, opacity: 0.35, shiny: true };
    v.setParts([{ id: "top_plate", scene: p, color: null, focus: true, material: clear }], false);
    const c = mats()[0] as THREE.MeshStandardMaterial;
    expect([c.transparent, c.opacity, c.depthWrite]).toEqual([true, 0.35, false]);
    v.setParts([{ id: "top_plate", scene: p, color: null, focus: true }], false);
    expect(mats().every((x) => x instanceof THREE.MeshLambertMaterial)).toBe(true);
    expect(faceColors(p)).toEqual(new Set(["2a2a2a"]));
    v.dispose();
  });

  it("next to a shiny part a plain part keeps its look: the lowered ambient comes back as emissive; edges follow edgeColor, else the theme", async () => {
    let ink: string | null = null;
    const v = createViewer(document.createElement("canvas"), { createRenderer: stubRenderer, environment: () => null, edgeColor: () => ink });
    const plain = await loadPart(plateBytes());
    const metal = await loadPart(plateBytes());
    const lam = () => { let m: THREE.MeshLambertMaterial | null = null; plain.object.traverse((o) => { if (o instanceof THREE.Mesh) m = o.material as THREE.MeshLambertMaterial; }); return m as unknown as THREE.MeshLambertMaterial; };
    v.setParts([{ id: "a", scene: plain, color: null, focus: true }], true);
    expect(lam().emissiveIntensity).toBeCloseTo(0);
    expect(lam().toneMapped).toBe(false);
    const chrome = { color: "#e6e8eb", metalness: 1, roughness: 0.06, opacity: 1, shiny: true };
    v.setParts([{ id: "a", scene: plain, color: null, focus: true }, { id: "b", scene: metal, color: null, focus: false, material: chrome }], false);
    expect(lam().emissiveIntensity).toBeCloseTo(AMBIENT - SHINY_AMBIENT);
    expect(lam().emissive.getHexString()).toBe(lam().color.getHexString());
    expect(edgeColors(plain)).toEqual(new Set([hex(EDGE_COLOR)]));
    ink = "#33373d";
    v.restyle?.();
    expect(edgeColors(plain)).toEqual(new Set(["33373d"]));
    v.dispose();
  });

  it("a click on the model reports the part, a point on it (CAD mm) and the facing normal", async () => {
    const v = createViewer(document.createElement("canvas"), { createRenderer: stubRenderer });
    const p = await loadPart(plateBytes());
    v.resize(400, 300);
    v.setParts([{ id: "top_plate", scene: p, color: null, focus: true }], true);
    const hit = v.pick(200, 150);
    expect(hit?.partId).toBe("top_plate");
    const [x, y, z] = hit!.point;
    expect(Math.abs(x)).toBeLessThanOrEqual(20.01);
    expect(Math.abs(y)).toBeLessThanOrEqual(20.01);
    expect(Math.abs(z)).toBeLessThanOrEqual(1.51);
    expect(hit!.normal).toEqual([0, 0, 1]);          // the iso camera looks down on the top face
    const [sx, sy] = v.toScreen(hit!.point);
    expect(sx).toBeCloseTo(200, 0);
    expect(sy).toBeCloseTo(150, 0);
    expect(v.pick(2, 2)).toBeNull();                  // the empty sheet corner
    expect(() => { v.setMarker({ kind: "face", partId: "top_plate", normal: [0, 0, 1], point: [0, 0, 1.5], tol: 0.05 }); v.setMarker(null); }).not.toThrow();
    v.dispose();
  });

  it("measure's markers (a face, an edge, a hole's ring) and the frame listener", async () => {
    const v = createViewer(document.createElement("canvas"), { createRenderer: stubRenderer });
    const p = await loadPart(plateBytes());
    v.resize(400, 300);
    v.setParts([{ id: "top_plate", scene: p, color: null, focus: true }], true);
    const seen = vi.fn();
    const off = v.onView(seen);
    v.setMarkers([
      { kind: "face", partId: "top_plate", normal: [0, 0, 1], point: [0, 0, 1.5], tol: 0.05 },
      { kind: "edge", a: [-20, 20, 1.5], b: [20, 20, 1.5] },
      { kind: "ring", center: [0, 0, 1.5], axis: [0, 0, 1], radius: 2.1 },
    ]);
    await new Promise((r) => requestAnimationFrame(() => r(null)));
    await new Promise((r) => setTimeout(r, 30));
    expect(seen).toHaveBeenCalled();
    off();
    seen.mockClear();
    v.setMarkers([]);
    await new Promise((r) => setTimeout(r, 30));
    expect(seen).not.toHaveBeenCalled();
    v.dispose();
  });

  it("the build view frames every part, and a click names the part under it", async () => {
    const v = createViewer(document.createElement("canvas"), { createRenderer: stubRenderer });
    const a = await loadPart(plateBytes());
    const b = await loadPart(plateBytes());
    b.object.position.x += 100;
    b.object.updateMatrixWorld(true);
    b.bbox.translate(new THREE.Vector3(100, 0, 0));
    v.resize(400, 300);
    v.setParts([{ id: "a", scene: a, color: null, focus: true }, { id: "b", scene: b, color: "#c2463d", focus: false }], true, true);
    const [bx, by] = v.toScreen([100, 0, 1.5]);
    expect(bx).toBeGreaterThan(0); expect(bx).toBeLessThan(400);
    expect(v.pick(bx, by)?.partId).toBe("b");
    v.dispose();
  });
});
