import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { partSummarySchema } from "../../api/schemas";
import type { Vec3 } from "../../api/types";
import { closestOnEdge, describeEdge, describeFace, matchFace, nearestEdge } from "./pick";

// the real core's tree rows: top_plate is a plain 40×40×3 box (centred), quad_arm has a hole and a fillet
const rows = z.array(partSummarySchema).parse(JSON.parse(readFileSync(resolve(import.meta.dirname, "../../../test/fixtures/core-ui/parts_tree.json"), "utf8")));
const plate = rows.find((p) => p.part_id === "top_plate")?.metrics;
if (!plate?.face_list || !plate.edge_list) throw new Error("fixture lacks face/edge lists");
const faces = plate.face_list;
const edges = plate.edge_list;

describe("pick → face / edge", () => {
  it("a click on the top names the top face", () => {
    const f = matchFace(faces, [5, -3, 1.5], [0, 0, 1], 0.4);
    expect(f?.normal).toEqual([0, 0, 1]);
    expect(f?.center[2]).toBeCloseTo(1.5);
    expect(describeFace(f!)).toMatch(/^f\d+ · plane facing \+Z · 1600 mm²$/);
  });

  it("the hit's own orientation decides between faces meeting at an edge", () => {
    const side = matchFace(faces, [20, 0, 1.4], [1, 0, 0], 0.4);
    expect(side?.normal).toEqual([1, 0, 0]);
  });

  it("a point off every plane names nothing planar", () => {
    expect(matchFace(faces, [0, 0, 9], [0, 0, 1], 0.4)).toBeNull();
  });

  it("the nearest edge within the pixel radius, measured along the segment", () => {
    const toScreen = (p: Vec3): [number, number] => [p[0] * 10, p[1] * 10];   // top view, 10 px/mm
    const near = nearestEdge(edges, [19.8, 5, 1.5], toScreen, 10);
    expect(near).not.toBeNull();
    expect(near?.direction).toEqual([0, 1, 0]);
    expect(near?.center?.[0]).toBe(20);
    expect(closestOnEdge(near!, [19.8, 5, 1.5])).toEqual([20, 5, near!.center![2]]);
    expect(describeEdge(near!)).toMatch(/^e\d+ · line · 40 mm$/);
    expect(nearestEdge(edges, [0, 0, 1.5], toScreen, 10)).toBeNull();     // the middle of a face: no edge
  });
});
