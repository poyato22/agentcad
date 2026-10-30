import { describe, expect, it } from "vitest";
import type { Step } from "../../api/types";
import { parseSkeleton, skeletonFromSteps } from "./skeleton";

const step = (id: number, name: string, data: unknown): Step => ({
  id, run_id: "r", ts: id, kind: "tool_result", payload: { name, output: { ok: true, summary: "", data } },
  evidence_hash: "", prev_hash: null, collapsed: false,
});
const view = (version: number, span: number) => ({
  project_id: "b1", skeleton_id: "skeleton_b1", version,
  skeleton: { params: { span }, datums: { mid: { kind: "axis", origin: [0, 0, 0], direction: [1, 0, 0] } }, envelope: { min: [0, 0, 0], max: [10, 20, 5] }, curves: { le: {} } },
});

describe("layout skeleton (I4b)", () => {
  it("parses the core's skeleton_set / skeleton_get data", () => {
    const v = parseSkeleton(view(3, 900), "skeleton_b1")!;
    expect(v.version).toBe(3);
    expect(v.params).toEqual([["span", 900]]);
    expect(v.datums[0]).toEqual(["mid", { kind: "axis", origin: [0, 0, 0], normal: null, direction: [1, 0, 0] }]);
    expect(v.envelope).toEqual({ min: [0, 0, 0], max: [10, 20, 5] });
    expect(v.curves).toEqual(["le"]);
    expect(parseSkeleton(view(3, 900), "skeleton_other")).toBeNull();
    expect(parseSkeleton({ skeleton_id: "skeleton_b1", version: 0, skeleton: null }, "skeleton_b1")?.params).toEqual([]);
  });

  it("takes the newest version from the loaded steps, ignoring other tools", () => {
    const all = [[step(1, "skeleton_set", view(1, 100)), step(5, "mirror_part_create", view(9, 1))], [step(3, "skeleton_get", view(2, 200))]];
    expect(skeletonFromSteps(all, "skeleton_b1")?.params).toEqual([["span", 200]]);
    expect(skeletonFromSteps([], "skeleton_b1")).toBeNull();
  });
});
