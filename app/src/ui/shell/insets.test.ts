import * as THREE from "three";
import { afterEach, describe, expect, it } from "vitest";
import { fitOrtho, offsetOrtho, visibleWidth } from "../viewer/scene";
import { insetsOf, NO_INSETS, viewInsets, watchInsets } from "./insets";

const rect = (left: number, top: number, width: number, height: number) =>
  ({ left, top, width, height, right: left + width, bottom: top + height, x: left, y: top, toJSON: () => ({}) }) as DOMRect;

describe("floating surface insets (FW-layout, D20)", () => {
  const viewer = rect(0, 44, 1440, 856);

  it("a surface counts to its far edge plus the gap it keeps from the window edge", () => {
    // the Parts surface 240 wide, 12 in; the dock 440 wide, 12 in from the right
    expect(insetsOf(viewer, rect(12, 56, 240, 300), rect(988, 56, 440, 832))).toEqual({ left: 264, right: 464 });
    // a folded tab (34 wide) still keeps the model clear of it
    expect(insetsOf(viewer, rect(12, 56, 34, 80), null)).toEqual({ left: 58, right: 0 });
    expect(insetsOf(viewer, null, null)).toEqual({ left: 0, right: 0 });
  });

  it("never leaves the model less than 120px", () => {
    const narrow = rect(0, 44, 600, 600);
    const i = insetsOf(narrow, rect(12, 56, 240, 300), rect(148, 56, 440, 500));
    expect(i.left + i.right).toBeLessThanOrEqual(480);
    expect(i.left).toBeLessThan(i.right);
  });
});

describe("watchInsets (Plan 10 E37)", () => {
  const el = (attrs: Record<string, string>, r: DOMRect): HTMLElement => {
    const e = document.createElement("div");
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    e.getBoundingClientRect = () => r;
    return e;
  };
  const RO = globalThis.ResizeObserver;
  afterEach(() => { globalThis.ResizeObserver = RO; viewInsets.value = NO_INSETS; });

  it("re-measures when the dock mounts after the viewer, or is replaced by a wider one", async () => {
    const observed = new Set<Element>();
    globalThis.ResizeObserver = class {
      cb: () => void;
      constructor(cb: () => void) { this.cb = cb; }
      observe(e: Element) { observed.add(e); this.cb(); }
      unobserve(e: Element) { observed.delete(e); }
      disconnect() { observed.clear(); }
    } as unknown as typeof ResizeObserver;
    const app = document.createElement("div");
    app.append(el({ "data-region": "viewer" }, rect(0, 44, 1440, 856)), el({ "data-region": "left", "data-surface": "" }, rect(12, 56, 240, 300)));
    document.body.append(app);
    const stop = watchInsets(app);
    expect(viewInsets.value).toEqual({ left: 264, right: 0 });
    const dock = el({ "data-region": "console", "data-surface": "" }, rect(988, 56, 440, 832));
    app.append(dock);
    await new Promise((r) => setTimeout(r, 40));
    expect(viewInsets.value).toEqual({ left: 264, right: 464 });
    expect(observed.has(dock)).toBe(true);
    const wide = el({ "data-region": "console", "data-surface": "" }, rect(660, 56, 768, 832));
    dock.replaceWith(wide);
    await new Promise((r) => setTimeout(r, 40));
    expect(viewInsets.value.right).toBe(792);
    expect(observed.has(dock)).toBe(false);
    stop();
    app.remove();
  });
});

describe("offsetOrtho", () => {
  it("centres the camera's axis in the room the surfaces leave, keeping the scale", () => {
    const cam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 1000);
    cam.position.set(100, -100, 100);
    cam.up.set(0, 0, 1);
    const box = new THREE.Box3(new THREE.Vector3(-10, -10, -10), new THREE.Vector3(10, 10, 10));
    const [w, h, l, r] = [1440, 856, 264, 464];
    fitOrtho(cam, box, visibleWidth(w, l, r) / h);
    const halfH = cam.top;
    offsetOrtho(cam, w, h, l, r);
    expect(cam.top).toBe(halfH);                                         // same scale
    expect((cam.right - cam.left) / (cam.top - cam.bottom)).toBeCloseTo(w / h, 6);
    // the model's centre (the camera axis, x = 0) projects to the middle of 264..976
    const axisPx = (-cam.left / (cam.right - cam.left)) * w;
    expect(axisPx).toBeCloseTo(l + (w - l - r) / 2, 6);
    // and the whole box fits between the surfaces
    cam.updateMatrixWorld(true);
    for (const x of [-10, 10]) for (const y of [-10, 10]) for (const z of [-10, 10]) {
      const p = new THREE.Vector3(x, y, z).project(cam);
      const px = ((p.x + 1) / 2) * w;
      expect(px).toBeGreaterThan(l);
      expect(px).toBeLessThan(w - r);
    }
  });

  it("with no surfaces it is the plain symmetric frustum", () => {
    const cam = new THREE.OrthographicCamera(-2, 2, 1, -1, 0.1, 1000);
    offsetOrtho(cam, 800, 400, 0, 0);
    expect([cam.left, cam.right]).toEqual([-2, 2]);
  });
});
