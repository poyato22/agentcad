// Plan 10 E36: the laid-out grid.
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { flatDims, flatTurn, layoutPlan } from "./layout";

const v = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z);

describe("Laid out (E36)", () => {
  it("lays each part on its largest flat side, its longest side along X (quarter turns only)", () => {
    for (const dims of [v(10, 80, 4), v(3, 20, 60), v(50, 5, 30), v(40, 20, 2)]) {
      const d = flatDims(dims, flatTurn(dims));
      const s = [dims.x, dims.y, dims.z].sort((a, b) => a - b);
      expect(d.z).toBeCloseTo(s[0]);             // thinnest up
      expect(d.x).toBeCloseTo(s[2]);             // longest along X
      expect(d.y).toBeCloseTo(s[1]);
    }
  });

  it("a grid, largest first, centred on the build, on its floor, nothing overlapping", () => {
    const items = [
      { id: "small", size: v(10, 10, 5) }, { id: "frame", size: v(120, 80, 20) }, { id: "arm", size: v(80, 14, 14) },
      { id: "bolt", size: v(12, 4, 4) }, { id: "wheel", size: v(44, 44, 12) },
    ];
    const cells = layoutPlan(items, v(0, 0, 0), -5);
    const byId = Object.fromEntries(cells.map((c) => [c.id, c]));
    // the largest is the first one laid (front-left)
    const order = [...cells].sort((a, b) => (a.center.y - b.center.y) || (a.center.x - b.center.x)).map((c) => c.id);
    expect(order[0]).toBe("frame");
    for (const c of cells) expect(c.center.z - c.size.z / 2).toBeCloseTo(-5);
    const boxes = cells.map((c) => new THREE.Box3().setFromCenterAndSize(c.center, c.size));
    for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
      const both = boxes[i].clone().intersect(boxes[j]);
      expect(both.isEmpty() || both.getSize(v(0, 0, 0)).x * both.getSize(v(0, 0, 0)).y < 1e-9, `${cells[i].id} × ${cells[j].id}`).toBe(true);
    }
    const all = new THREE.Box3(); for (const b of boxes) all.union(b);
    const mid = all.getCenter(v(0, 0, 0));
    expect(Math.abs(mid.y)).toBeLessThan(1e-6);
    expect(byId["bolt"]).toBeDefined();
  });
});
