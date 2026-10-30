import { describe, expect, it } from "vitest";
import { assemblyPartSchema } from "./assembly";

describe("assembly part rows", () => {
  it("the layout skeleton's row (no mtime, no metrics) parses, so Assembly view still lists the build (real core, G3)", () => {
    const rows = [
      { part_id: "skeleton_b1", label: "Skeleton", kind: "skeleton", metrics: null, mtime: null },
      { part_id: "arm", metrics: null, mtime: 5, appearance: { preset: "polished_metal", color: "#b0b4ba", metalness: 1, roughness: 0.15, opacity: 1 } },
    ].map((r) => assemblyPartSchema.parse(r));
    expect(rows[0].mtime).toBe(0);
    expect(rows[1].appearance?.metalness).toBe(1);        // I5: the finish reaches Assembly view
  });
});
