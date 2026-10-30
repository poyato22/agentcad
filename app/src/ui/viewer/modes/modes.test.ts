import * as THREE from "three";
import { afterEach, describe, expect, it, vi } from "vitest";
import { loadPart, type PartScene } from "../loadPart";
import { createViewer, type DesignViewerHandle, type ModeHost, type RendererLike } from "../scene";
import { currentView, dirFromAzEl, frameView, liveBox, Orbiter, viewDirection, zoomForDistance } from "./camera";
import { ModesController } from "./controller";
import { bodies, EXPLODE_MS, explodeOffset, explodeVectors } from "./explode";
import { easeInOutCubic, tween, type Clock } from "./motion";
import { capColor, clipPlane, sectionRange } from "./section";
import { GHOST_OPACITY, MODES_OFF, type ModesState } from "./state";
import { partOpacity } from "./xray";

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const fixtureBytes = (): ArrayBuffer =>
  new Uint8Array(readFileSync(resolve(import.meta.dirname, "../../../../../core/tests/fixtures/ui/mount_plate.glb"))).buffer as ArrayBuffer;

function stubRenderer(): RendererLike {
  return { info: { memory: { geometries: 0, textures: 0 } }, render: () => {}, setSize: vi.fn(), setPixelRatio: vi.fn(), setClearColor: vi.fn(), dispose: vi.fn() };
}

/** A clock the test advances by hand. */
function fakeClock(): Clock & { t: number; advance(ms: number): void } {
  const q = new Map<number, () => void>();
  let n = 0;
  const c = {
    t: 0,
    now: () => c.t,
    frame: (cb: () => void) => { q.set(++n, cb); return n; },
    cancel: (id: number) => { q.delete(id); },
    advance(ms: number) {
      const steps = Math.ceil(ms / 16);
      for (let i = 0; i < steps; i++) { c.t += ms / steps; const cbs = [...q.values()]; q.clear(); for (const cb of cbs) cb(); }
    },
  };
  return c;
}

let viewers: DesignViewerHandle[] = [];
afterEach(() => { for (const v of viewers) v.dispose(); viewers = []; vi.unstubAllGlobals(); });

async function setup(ids: string[], offsets: [number, number, number][] = []): Promise<{ v: DesignViewerHandle; host: ModeHost; scenes: PartScene[]; c: ModesController; clock: ReturnType<typeof fakeClock> }> {
  const v = createViewer(document.createElement("canvas"), { createRenderer: stubRenderer });
  viewers.push(v);
  const host = v.modeHost!();
  const scenes = await Promise.all(ids.map(async (_, i) => {
    const s = await loadPart(fixtureBytes());
    const o = offsets[i];
    if (o) { s.object.position.set(...o); s.object.updateMatrixWorld(true); s.bbox.setFromObject(s.object); }
    return s;
  }));
  const clock = fakeClock();
  const c = new ModesController(host, clock);
  v.setParts(ids.map((id, i) => ({ id, scene: scenes[i], color: i === 1 ? "#c9a227" : null, focus: i === 0 })), true, true);
  return { v, host, scenes, c, clock };
}

const faceMats = (s: PartScene) => {
  const out: THREE.Material[] = [];
  s.object.traverse((o) => { if (o instanceof THREE.Mesh) out.push(o.material as THREE.Material); });
  return out;
};
const edgeMats = (s: PartScene) => {
  const out: THREE.Material[] = [];
  s.object.traverse((o) => { if (o instanceof THREE.LineSegments) out.push(o.material as THREE.Material); });
  return out;
};
const modes = (patch: Partial<ModesState>): ModesState => ({ ...MODES_OFF, ...patch });

describe("x-ray", () => {
  it("makes the chosen parts see-through, leaves the others and edges solid", async () => {
    const { c, scenes } = await setup(["case", "board"]);
    c.set(modes({ xray: { opacity: 0.3, parts: ["case"] } }));
    for (const m of faceMats(scenes[0])) { expect(m.transparent).toBe(true); expect(m.opacity).toBeCloseTo(0.3); expect(m.depthWrite).toBe(false); }
    for (const m of faceMats(scenes[1])) { expect(m.transparent).toBe(false); expect(m.opacity).toBe(1); }
    for (const m of edgeMats(scenes[0])) expect(m.opacity).toBe(1);
    c.set(modes({ xray: { opacity: 0.3, parts: null } }));
    for (const m of faceMats(scenes[1])) expect(m.opacity).toBeCloseTo(0.3);
    c.set(MODES_OFF);
    for (const m of faceMats(scenes[0])) { expect(m.transparent).toBe(false); expect(m.depthWrite).toBe(true); }
  });

  it("is re-applied when a part reloads with fresh materials", async () => {
    const { v, c } = await setup(["case"]);
    c.set(modes({ xray: { opacity: 0.4, parts: null } }));
    const fresh = await loadPart(fixtureBytes());
    v.setParts([{ id: "case", scene: fresh, color: null, focus: true }], false);
    for (const m of faceMats(fresh)) expect(m.opacity).toBeCloseTo(0.4);
  });

  it("ghosts the parts outside an isolated subset", () => {
    expect(partOpacity("a", { ...MODES_OFF, isolate: ["b"] })[0]).toBe(GHOST_OPACITY);
    expect(partOpacity("b", { ...MODES_OFF, isolate: ["b"] })).toEqual([1, 1]);
  });
});

describe("section", () => {
  it("clips every face and edge by one plane and keeps n·p <= at", async () => {
    const { c, scenes } = await setup(["case"]);
    c.set(modes({ section: { on: true, normal: [0, 0, 1], at: 1 } }));
    const plane = c.caps.plane;
    for (const m of [...faceMats(scenes[0]), ...edgeMats(scenes[0])]) expect(m.clippingPlanes?.[0]).toBe(plane);
    // three keeps points with plane.distanceToPoint >= 0
    expect(plane.distanceToPoint(new THREE.Vector3(0, 0, 0))).toBeGreaterThan(0);
    expect(plane.distanceToPoint(new THREE.Vector3(0, 0, 2))).toBeLessThan(0);
    c.set(MODES_OFF);
    for (const m of faceMats(scenes[0])) expect(m.clippingPlanes).toBeNull();
  });

  it("caps each part on the plane, in the part's color, facing the viewer side", async () => {
    const { c } = await setup(["case", "board"]);
    c.set(modes({ section: { on: true, normal: [1, 0, 0], at: 2.5 } }));
    const caps = c.caps.caps();
    expect(caps).toHaveLength(2);
    for (const cap of caps) {
      expect(cap.position.x).toBeCloseTo(2.5);
      const n = new THREE.Vector3(0, 0, 1).applyQuaternion(cap.quaternion);
      expect(n.x).toBeCloseTo(1);
      const m = cap.material as THREE.MeshBasicMaterial;
      expect(m.stencilFunc).toBe(THREE.NotEqualStencilFunc);
    }
    expect(caps[1].userData["capColor"]).toBe("#c9a227");
    // the default grey cap stands apart from the face grey
    expect(caps[0].userData["capColor"]).not.toBe("#2a2a2a");
    expect(capColor({ id: "x", scene: null as never, color: null, focus: false }, { face: "#000000", edge: "#ffffff" })).toBe("#818181");
  });

  it("stencil passes count back faces up and front faces down, after the parts and before each cap", async () => {
    const { c, host } = await setup(["case"]);
    c.set(modes({ section: { on: true, normal: [0, 1, 0], at: null } }));
    const helpers: THREE.Mesh[] = [];
    host.scene.traverse((o) => { if (o instanceof THREE.Mesh && o.userData["modeHelper"] && !o.name.startsWith("cap:")) helpers.push(o); });
    const back = helpers.filter((m) => (m.material as THREE.Material).side === THREE.BackSide);
    const front = helpers.filter((m) => (m.material as THREE.Material).side === THREE.FrontSide);
    expect(back.length).toBeGreaterThan(0);
    expect(back.length).toBe(front.length);
    expect((back[0].material as THREE.Material).stencilZPass).toBe(THREE.IncrementWrapStencilOp);
    expect((front[0].material as THREE.Material).stencilZPass).toBe(THREE.DecrementWrapStencilOp);
    expect((back[0].material as THREE.Material).colorWrite).toBe(false);
    const cap = c.caps.caps()[0];
    expect(cap.renderOrder).toBeGreaterThan(back[0].renderOrder);
    // at: null → through the middle of the part (y = 0 for the plate)
    expect(c.caps.plane.constant).toBeCloseTo(0);
  });

  it("range spans the box along the normal", () => {
    const b = new THREE.Box3(new THREE.Vector3(-20, -10, -3), new THREE.Vector3(20, 10, 3));
    expect(sectionRange(b, new THREE.Vector3(0, 0, 1))).toEqual([-3, 3]);
    const p = clipPlane(new THREE.Vector3(1, 0, 0), 5);
    expect(p.distanceToPoint(new THREE.Vector3(4, 0, 0))).toBeGreaterThan(0);
  });
});

/** Two plates (40 × 20 × 6) at x = ±10, fully exploded: each goes out by 1.2× its distance plus 0.45× the diagonal. */
const APART = 10 + 10 * 1.2 + Math.hypot(60, 20, 6) * 0.45;

describe("explode", () => {
  it("groups a part's per-face meshes into solids: one plate is one body, two apart are two", async () => {
    const one = await loadPart(fixtureBytes());
    expect(bodies(one.object)).toHaveLength(1);
    const two = new THREE.Group();
    const a = await loadPart(fixtureBytes()); const b = await loadPart(fixtureBytes());
    b.object.position.x = 100;
    two.add(a.object, b.object);
    expect(bodies(two)).toHaveLength(2);
  });

  it("pushes units out from the middle along their own direction; concentric ones stack on Z", () => {
    const overall = new THREE.Box3(new THREE.Vector3(-10, -10, -10), new THREE.Vector3(10, 10, 10));
    const s = new THREE.Vector3(2, 2, 2);
    const v = explodeVectors([new THREE.Vector3(5, 0, 0), new THREE.Vector3(-5, 0, 0)], [s, s], overall);
    const far = 5 * 1.2 + Math.hypot(20, 20, 20) * 0.45;   // its own distance, and a share of the whole
    expect(v[0].x).toBeCloseTo(far); expect(v[1].x).toBeCloseTo(-far);
    const st = explodeVectors([new THREE.Vector3(0, 0, 0.1), new THREE.Vector3(0, 0, -0.1)], [s, s], overall);
    expect(st[0].z).toBeGreaterThan(0); expect(st[1].z).toBeLessThan(0);
    expect(st[0].x).toBe(0);
  });

  it("glides smoothly to the target (eased, monotone, lands exactly)", async () => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} }));
    const { c, scenes, clock } = await setup(["case", "board"], [[-10, 0, 0], [10, 0, 0]]);
    c.set(modes({ explode: 1 }), { animate: true });
    const xs: number[] = [];
    for (let t = 0; t < EXPLODE_MS + 50; t += 16) { clock.advance(16); xs.push(scenes[1].object.position.x); }
    for (let i = 1; i < xs.length; i++) expect(xs[i]).toBeGreaterThanOrEqual(xs[i - 1] - 1e-9);
    // no frame jumps more than a fifth of the way
    const total = xs[xs.length - 1] - 10;
    for (let i = 1; i < xs.length; i++) expect(xs[i] - xs[i - 1]).toBeLessThan(total / 5);
    expect(scenes[1].object.position.x).toBeCloseTo(APART);
    expect(scenes[0].object.position.x).toBeCloseTo(-APART);
    expect(c.exploder.value).toBe(1);
    c.set(modes({ explode: 0 }));
    expect(scenes[1].object.position.x).toBeCloseTo(10);
  });

  it("lands at once under reduced motion", async () => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: q.includes("reduce"), media: q, addEventListener() {}, removeEventListener() {} }));
    const { c, scenes } = await setup(["case", "board"], [[-10, 0, 0], [10, 0, 0]]);
    c.set(modes({ explode: 1 }), { animate: true });
    expect(scenes[1].object.position.x).toBeCloseTo(APART);
  });

  it("zooms out to keep the exploded parts in view, and back when assembled", async () => {
    const { c, host, v } = await setup(["case", "board"], [[-10, 0, 0], [10, 0, 0]]);
    v.resize(400, 300);
    const zoom0 = host.camera.zoom;
    c.set(modes({ explode: 1 }));
    expect(host.camera.zoom).toBeLessThan(zoom0);
    const box = liveBox(host.parts())!;
    host.camera.updateMatrixWorld(true);
    for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) {
      const p = new THREE.Vector3(x, y, z).project(host.camera);
      expect(Math.abs(p.x)).toBeLessThanOrEqual(1);
      expect(Math.abs(p.y)).toBeLessThanOrEqual(1);
    }
    c.set(modes({ explode: 0 }));
    expect(host.camera.zoom).toBeCloseTo(zoom0);
  });

  it("the section caps follow exploded parts", async () => {
    const { c, scenes, host } = await setup(["case", "board"], [[-10, 0, 0], [10, 0, 0]]);
    c.set(modes({ explode: 1, section: { on: true, normal: [0, 1, 0], at: 0 } }));
    const stencil = host.scene.getObjectByName("section-caps")!.children.find((o) => !o.name.startsWith("cap:")) as THREE.Mesh;
    stencil.onBeforeRender(null as never, null as never, null as never, null as never, null as never, null as never);
    const src: THREE.Mesh[] = [];
    scenes[0].object.traverse((o) => { if (o instanceof THREE.Mesh) src.push(o); });
    expect(stencil.matrixWorld.equals(src[0].matrixWorld)).toBe(true);
    expect(c.caps.caps()[1].position.x).toBeCloseTo(APART);
  });
});

describe("motion", () => {
  it("easing starts and ends still", () => {
    expect(easeInOutCubic(0)).toBe(0); expect(easeInOutCubic(1)).toBe(1); expect(easeInOutCubic(0.5)).toBeCloseTo(0.5);
  });
  it("tween under reduced motion is one step", () => {
    const seen: number[] = [];
    tween(0, 1, 400, (v) => seen.push(v), undefined, true);
    expect(seen).toEqual([1]);
  });
});

describe("camera", () => {
  it("standard views and az/el agree with the CAD frame", () => {
    expect(viewDirection("front").y).toBeCloseTo(-1);
    expect(viewDirection("side").x).toBeCloseTo(1);
    expect(viewDirection("top").z).toBeCloseTo(1, 3);
    expect(dirFromAzEl(90, 0).x).toBeCloseTo(1);
  });

  it("frames a view and knows it is in it; distance maps to a zoom", async () => {
    const { host, c } = await setup(["case"]);
    void c;
    const box = liveBox(host.parts())!;
    frameView(host, viewDirection("top"), box);
    expect(currentView(host.camera, host.controls.target)).toBe("top");
    const z = zoomForDistance(box, 1e9);
    expect(z).toBeLessThan(0.01);
  });

  it("orbit turns the camera about Z, and does not start under reduced motion", async () => {
    const { host } = await setup(["case"]);
    const clock = fakeClock();
    const o = new Orbiter(host, clock);
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: q.includes("reduce"), media: q, addEventListener() {}, removeEventListener() {} }));
    expect(o.start(1, 8)).toBe(false);
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} }));
    const before = host.camera.position.clone();
    expect(o.start(1, 8)).toBe(true);
    clock.advance(2000);
    const after = host.camera.position.clone();
    expect(after.z).toBeCloseTo(before.z);
    expect(after.distanceTo(before)).toBeGreaterThan(1);
    o.stop();
  });
});

describe("pins follow the explode (m11)", () => {
  it("a point on a moved body gets that body's offset; one on a body at rest gets none", () => {
    const root = new THREE.Group();
    const box = (x: number) => { const m = new THREE.Mesh(new THREE.BoxGeometry(2, 2, 2)); m.position.set(x, 0, 0); m.userData["explodeBase"] = m.position.clone(); root.add(m); return m; };
    const a = box(0); box(10);
    a.position.set(-5, 0, 3);                      // exploded
    root.updateMatrixWorld(true);
    expect(explodeOffset(root, new THREE.Vector3(1, 0, 0)).toArray()).toEqual([-5, 0, 3]);
    expect(explodeOffset(new THREE.Group(), new THREE.Vector3(1, 0, 0)).toArray()).toEqual([0, 0, 0]);
  });
});
