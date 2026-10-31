import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { sweepEnds, sweepSection, sweepValue } from "./sweep";

const box = new THREE.Box3(new THREE.Vector3(-20, -10, 0), new THREE.Vector3(60, 10, 6));

describe("section sweep", () => {
  it("ends_default_to_the_assembly_box_along_the_axis", () => {
    expect(sweepEnds({ axis: "x", from: null, to: null, duration_s: 4 }, box)).toEqual([-20, 60]);
    expect(sweepEnds({ axis: "z", from: 6, to: null, duration_s: 4 }, box)).toEqual([6, 6]);
    expect(sweepEnds({ axis: "y", from: 5, to: -5, duration_s: 4 }, null)).toEqual([5, -5]);
    expect(sweepEnds({ axis: "y", from: null, to: -5, duration_s: 4 }, null)).toBeNull();
  });

  it("maps_an_axis_value_to_the_modes_plane_facing_the_camera_and_back", () => {
    const sw = { axis: "y" as const, from: null, to: null, duration_s: 4 };
    const fwd = sweepSection(sw, [0, 1, 0], 4);
    expect(fwd).toEqual({ on: true, normal: [0, 1, 0], at: 4 });
    const back = sweepSection(sw, [0, -1, 0], 4);
    expect(back.normal).toEqual([0, -1, 0]);
    expect(back.at).toBe(-4);                                  // keeps y >= 4: still the cut at y = 4
    expect(sweepValue(sw, back)).toBe(4);
    expect(sweepValue(sw, fwd)).toBe(4);
  });
});
