import { describe, expect, it } from "vitest";
import type { PartSummary } from "../../api/types";
import { applyMove, dropPosAt, flatten, isWithin, planDrop, planIndent, planOutdent, planStep } from "./tree";

const row = (id: string, order: number, parent: string | null = null): PartSummary =>
  ({ part_id: id, id, label: id, vars: {}, metrics: null, mtime: 0, parent_id: parent, order, kind: "part", version: 1 });

// frame ─┬ arm_a
//        └ arm_b
// plate
// motor
const LIST = [row("frame", 0), row("arm_a", 0, "frame"), row("arm_b", 1, "frame"), row("plate", 1), row("motor", 2)];

describe("part tree model", () => {
  it("flattens depth-first in order, nested under parents", () => {
    expect(flatten(LIST).map((r) => `${"  ".repeat(r.depth)}${r.part.part_id}`)).toEqual(["frame", "  arm_a", "  arm_b", "plate", "motor"]);
    expect(flatten(LIST, new Set(["frame"])).map((r) => r.part.part_id)).toEqual(["frame", "plate", "motor"]);
  });

  it("shows a row with a missing parent, or in a parent loop, at the root", () => {
    const odd = [row("a", 0, "gone"), row("b", 1, "c"), row("c", 2, "b")];
    expect(flatten(odd).filter((r) => r.depth === 0).map((r) => r.part.part_id)).toContain("a");
    expect(flatten(odd).length).toBe(3);
  });

  it("plans a drop before / after / inside as one reorder of the target's siblings", () => {
    expect(planDrop(LIST, "motor", "plate", "before")).toEqual({ parentId: null, ids: ["frame", "motor", "plate"] });
    expect(planDrop(LIST, "frame", "motor", "after")).toEqual({ parentId: null, ids: ["plate", "motor", "frame"] });
    expect(planDrop(LIST, "plate", "frame", "inside")).toEqual({ parentId: "frame", ids: ["arm_a", "arm_b", "plate"] });
    expect(planDrop(LIST, "arm_b", "plate", "after")).toEqual({ parentId: null, ids: ["frame", "plate", "arm_b", "motor"] });
  });

  it("never nests a part in itself or its own descendant (cycle prevention)", () => {
    expect(isWithin(LIST, "frame", "arm_a")).toBe(true);
    expect(planDrop(LIST, "frame", "arm_a", "inside")).toBeNull();
    expect(planDrop(LIST, "frame", "arm_b", "before")).toBeNull();
    expect(planDrop(LIST, "frame", "frame", "inside")).toBeNull();
  });

  it("a drop that changes nothing is no move", () => {
    expect(planDrop(LIST, "plate", "frame", "after")).toBeNull();
    expect(planDrop(LIST, "arm_b", "frame", "inside")).toBeNull();
  });

  it("Alt+↑/↓ swaps with a sibling; Alt+→ nests under the one above; Alt+← moves out after the parent", () => {
    expect(planStep(LIST, "plate", -1)).toEqual({ parentId: null, ids: ["plate", "frame", "motor"] });
    expect(planStep(LIST, "motor", 1)).toBeNull();
    expect(planStep(LIST, "arm_a", 1)).toEqual({ parentId: "frame", ids: ["arm_b", "arm_a"] });
    expect(planIndent(LIST, "motor")).toEqual({ parentId: "plate", ids: ["motor"] });
    expect(planIndent(LIST, "frame")).toBeNull();
    expect(planOutdent(LIST, "arm_a")).toEqual({ parentId: null, ids: ["frame", "arm_a", "plate", "motor"] });
    expect(planOutdent(LIST, "plate")).toBeNull();
  });

  it("applies a move optimistically", () => {
    const next = applyMove(LIST, { parentId: "frame", ids: ["arm_a", "arm_b", "plate"] });
    expect(flatten(next).map((r) => `${r.depth}${r.part.part_id}`)).toEqual(["0frame", "1arm_a", "1arm_b", "1plate", "0motor"]);
  });

  it("splits a row into before / inside / after drop zones", () => {
    expect(dropPosAt(2, 20)).toBe("before");
    expect(dropPosAt(10, 20)).toBe("inside");
    expect(dropPosAt(18, 20)).toBe("after");
  });
});
