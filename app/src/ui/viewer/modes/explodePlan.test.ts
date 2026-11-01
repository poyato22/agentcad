// Golden tests of the exploded layout (Plan 10 E4) on a three-level assembly and an enclosure.
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { CHILD_SHARE, explodeParents, explodePlan, MIN_SHARE, MOUNT_GAP, OWN_SHARE, PARENT_SHARE, type ExplodeUnit } from "./explodePlan";

const box = (min: [number, number, number], max: [number, number, number]) => new THREE.Box3(new THREE.Vector3(...min), new THREE.Vector3(...max));
const v = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z);

/** A robot arm, three levels below the base: base → turret (revolute Z) → upper arm (revolute Y
 *  at the shoulder) → forearm (revolute Y at the elbow, reaching along +X) → two fingers (sliders
 *  along Y), plus a wheel on each side of the base (revolute Y, on their axles). */
function arm(): ExplodeUnit[] {
  return [
    { id: "base", box: box([-40, -40, 0], [40, 40, 20]) },
    { id: "turret", box: box([-15, -15, 20], [15, 15, 50]), parent: "base", axis: v(0, 0, 1), origin: v(0, 0, 20) },
    { id: "upper", box: box([-5, -5, 50], [5, 5, 110]), parent: "turret", axis: v(0, 1, 0), origin: v(0, 0, 50) },
    { id: "fore", box: box([0, -5, 105], [50, 5, 115]), parent: "upper", axis: v(0, 1, 0), origin: v(0, 0, 110) },
    { id: "fingerL", box: box([50, 2, 106], [60, 5, 114]), parent: "fore", axis: v(0, 1, 0), origin: v(50, 0, 110) },
    { id: "fingerR", box: box([50, -5, 106], [60, -2, 114]), parent: "fore", axis: v(0, 1, 0), origin: v(50, 0, 110) },
    { id: "wheelL", box: box([-10, 40, -5], [10, 46, 15]), parent: "base", axis: v(0, 1, 0), origin: v(0, 40, 5) },
    { id: "wheelR", box: box([-10, -46, -5], [10, -40, 15]), parent: "base", axis: v(0, -1, 0), origin: v(0, -40, 5) },
  ];
}

/** The Pi enclosure: a case (open top), a lid on it and a board, battery and fan inside. */
function enclosure(): ExplodeUnit[] {
  return [
    { id: "case", box: box([-45, -31, -16], [45, 31, 16]) },
    { id: "lid", box: box([-45, -31, 16], [45, 31, 18]) },
    { id: "board", box: box([-40, -20, -13], [16, 20, -11]) },
    { id: "battery", box: box([-32, -13, -10], [8, 13, 2]) },
    { id: "fan", box: box([24, -15, -13], [32, 15, 17]) },
  ];
}

const byId = (units: ExplodeUnit[], out: THREE.Vector3[]) => Object.fromEntries(units.map((u, i) => [u.id, out[i]]));
const moved = (u: ExplodeUnit, o: THREE.Vector3) => u.box.clone().translate(o);
const overlap = (a: THREE.Box3, b: THREE.Box3) => {
  const i = a.clone().intersect(b);
  if (i.isEmpty()) return 0;
  const s = i.getSize(new THREE.Vector3());
  return s.x * s.y * s.z;
};

describe("explodePlan (E4)", () => {
  it("children move away from their parent along the joint axis or out from the joint, carrying the parent's move", () => {
    const u = arm();
    const o = byId(u, explodePlan(u));
    // the base is the root at the middle-ish: it is what everything hangs off
    expect(o.turret.x).toBeCloseTo(0); expect(o.turret.y).toBeCloseTo(0);
    expect(o.turret.z).toBeGreaterThan(0);                       // up its Z axis, off the base
    // the upper arm sits along its link (not along its Y hinge): further up, on top of the turret's move
    expect(o.upper.x).toBeCloseTo(0); expect(o.upper.y).toBeCloseTo(0);
    expect(o.upper.z).toBeGreaterThan(o.turret.z);
    // the forearm reaches along +X from the elbow: it goes out along +X, carried up with the arm
    expect(o.fore.x).toBeGreaterThan(0);
    expect(o.fore.z).toBeCloseTo(o.upper.z);
    // the fingers slide apart along their axis, each to its own side, with the forearm
    expect(o.fingerL.y).toBeGreaterThan(0); expect(o.fingerR.y).toBeLessThan(0);
    expect(o.fingerL.x).toBeCloseTo(o.fore.x); expect(o.fingerR.x).toBeCloseTo(o.fore.x);
    expect(o.fingerL.z).toBeCloseTo(o.fore.z);
    // the wheels come off their axles, outwards
    expect(o.wheelL.y).toBeGreaterThan(0); expect(o.wheelR.y).toBeLessThan(0);
    expect(o.wheelL.x).toBeCloseTo(0); expect(o.wheelL.z).toBeCloseTo(0);
  });

  it("steps come from the level (parent's extent, own size), comparable across the tree, damped down a chain", () => {
    const u = arm();
    const out = explodePlan(u);
    const o = byId(u, out);
    const parentOf: Record<string, string> = { turret: "base", upper: "turret", fore: "upper", fingerL: "fore", fingerR: "fore", wheelL: "base", wheelR: "base" };
    const steps = Object.entries(parentOf).map(([c, p]) => o[c].clone().sub(o[p]).length());
    for (const s of steps) expect(s).toBeGreaterThan(0);
    // no level runs away from the others (the review's 3x bound)
    expect(Math.max(...steps) / Math.min(...steps)).toBeLessThan(3);
    // the turret's step: the larger of its share of the base's height and of its own size
    const whole = new THREE.Box3(); for (const x of u) whole.union(x.box);
    const D = whole.getSize(new THREE.Vector3()).length();
    // (+ the mount gap: it sits on the base, so it ends a quarter of its own height clear of it)
    expect(o.turret.length()).toBeCloseTo(Math.max(PARENT_SHARE * 20, CHILD_SHARE * Math.hypot(30, 30, 30), MIN_SHARE * D) + MOUNT_GAP * 30, 5);
    // OWN_SHARE still sets the roots' radial step
    expect(OWN_SHARE).toBeGreaterThan(0);
  });

  it("parts mounted on a chassis end visibly clear of it, and of each other when stacked", () => {
    const u: ExplodeUnit[] = [
      { id: "chassis", box: box([-60, -40, 0], [60, 40, 30]) },
      { id: "battery", box: box([-30, -20, 11], [30, 20, 26]) },                 // in the pocket
      { id: "turret", box: box([5, -25, 30], [55, 25, 50]), parent: "chassis", axis: v(0, 0, 1), origin: v(30, 0, 30) },
      { id: "lidar", box: box([-52, -10, 30], [-32, 10, 42]), parent: "chassis", axis: v(0, 0, 1), origin: v(-42, 0, 30) },
    ];
    const o = byId(u, explodePlan(u));
    const at = (id: string) => u.find((x) => x.id === id)!.box.clone().translate(o[id]);
    for (const id of ["battery", "turret", "lidar"]) {
      const h = u.find((x) => x.id === id)!.box.getSize(v(0, 0, 0)).z;
      expect(at(id).min.z - 30, id).toBeGreaterThanOrEqual(MOUNT_GAP * h - 1e-6);
    }
    // the turret, stacked over the battery, leaves a gap of a quarter of its height too
    expect(at("turret").min.z - at("battery").max.z).toBeGreaterThanOrEqual(MOUNT_GAP * 20 - 1e-6);
  });

  it("a child that overlaps its parent clears the parent's box along its way first", () => {
    const u: ExplodeUnit[] = [
      { id: "chassis", box: box([-60, -40, 0], [60, 40, 30]) },
      // a mast sunk 10 mm into the chassis, on a Z slide
      { id: "mast", box: box([-5, -5, 20], [5, 5, 60]), parent: "chassis", axis: v(0, 0, 1), origin: v(0, 0, 30) },
    ];
    const o = byId(u, explodePlan(u));
    expect(u[1].box.min.z + o.mast.z).toBeGreaterThan(30);
  });

  it("nothing lands on anything else", () => {
    for (const u of [arm(), enclosure()]) {
      const out = explodePlan(u);
      for (let i = 0; i < u.length; i++) for (let j = i + 1; j < u.length; j++) {
        expect(overlap(moved(u[i], out[i]), moved(u[j], out[j])), `${u[i].id} × ${u[j].id}`).toBeLessThan(1e-6);
      }
    }
  });

  it("an enclosure opens upward: the contents leave the case and stack in the order they sat, the lid on top", () => {
    const u = enclosure();
    expect(explodeParents(u).map((p) => (p === null ? null : u[p].id))).toEqual([null, null, "case", "case", "case"]);
    const o = byId(u, explodePlan(u));
    expect(o.case.length()).toBe(0);                                      // the frame stays
    for (const id of ["lid", "board", "battery", "fan"]) {
      expect(o[id].x).toBeCloseTo(0); expect(o[id].y).toBeCloseTo(0);    // straight up: layout kept
    }
    const top = (id: string) => moved(u.find((x) => x.id === id)!, o[id]);
    // every content clears the case's top
    for (const id of ["board", "battery", "fan"]) expect(top(id).min.z).toBeGreaterThan(16);
    // the board and battery share a footprint: stacked in the order they sat (board below);
    // the fan, off to the side, needs no stacking; the lid covers them all and goes last
    expect(top("battery").min.z).toBeGreaterThan(top("board").max.z);
    for (const id of ["board", "battery", "fan"]) expect(top("lid").min.z).toBeGreaterThan(top(id).max.z);
  });

  it("parts at the middle with no structure: the biggest stays and the others step off it", () => {
    const u: ExplodeUnit[] = [
      { id: "shaft", box: box([-2, -2, -30], [2, 2, 30]) },
      { id: "bearing", box: box([-6, -6, -3], [6, 6, 3]) },
    ];
    // the bearing sits inside the shaft's box? no: it is wider. Neither holds the other
    const o = byId(u, explodePlan(u));
    expect(o.shaft.length()).toBe(0);
    expect(o.bearing.length()).toBeGreaterThan(0);
    expect(overlap(moved(u[0], o.shaft), moved(u[1], o.bearing))).toBe(0);
  });

  it("is a translation per part only and the same every time (no randomness)", () => {
    const a = explodePlan(arm()).map((x) => x.toArray());
    const b = explodePlan(arm()).map((x) => x.toArray());
    expect(a).toEqual(b);
  });
});
