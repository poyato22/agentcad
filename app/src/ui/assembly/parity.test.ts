// P11 F4 parity, the app's side: the real core's GET /projects/{id}/assembly and model.glb files for
// a build with a fixed, a joint-placed, an auto-placed and a moved instance (parts built located
// and turned, like the ornicopter's gears and rods; core tests/assembly/test_pose_parity.py dumps
// them). The app's world matrix of each instance is the core's solved transform, and the box it
// draws (loadPart's mesh through that matrix, as scene.ts places it) is the core's box.
import * as THREE from "three";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { loadPart } from "../viewer/loadPart";
import { toMatrix4 } from "./scene";

const DIR = resolve(import.meta.dirname, "../../../../core/tests/fixtures/ui/p11/parity");
const payload = JSON.parse(readFileSync(resolve(DIR, "assembly.json"), "utf8")) as {
  instances: { id: string; part_id: string; transform: number[][]; fixed: boolean }[];
};
const core = JSON.parse(readFileSync(resolve(DIR, "core_boxes.json"), "utf8")) as Record<string, { part_id: string; min: number[]; max: number[] }>;
const glb = (part: string): ArrayBuffer => new Uint8Array(readFileSync(resolve(DIR, `${part}.glb`))).buffer as ArrayBuffer;

describe("the app draws every instance where the core has it (P11 F4)", () => {
  it("covers a fixed, a joint-placed, an auto-placed and a moved instance", () => {
    expect(payload.instances.map((i) => i.part_id).sort()).toEqual(["base_plate", "crank_rod", "gear_pair", "side_block"]);
    expect(payload.instances.some((i) => i.fixed)).toBe(true);
  });

  for (const inst of payload.instances) {
    it(`${inst.part_id}: the drawn box is the core's box`, async () => {
      const part = await loadPart(glb(inst.part_id));
      // scene.ts: the instance's group matrix is the core's transform, the mesh under it as loaded
      const group = new THREE.Group();
      group.matrixAutoUpdate = false;
      group.matrix.copy(toMatrix4(inst.transform));
      group.add(part.object);
      group.updateMatrixWorld(true);
      const drawn = new THREE.Box3().setFromObject(group);
      const want = core[inst.id];
      for (const [k, axis] of (["x", "y", "z"] as const).entries()) {
        expect(drawn.min[axis]).toBeCloseTo(want.min[k], 0);
        expect(drawn.max[axis]).toBeCloseTo(want.max[k], 0);
      }
      part.dispose();
    });
  }
});
