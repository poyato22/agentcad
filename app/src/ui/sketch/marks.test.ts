import { describe, expect, it } from "vitest";
import { canSend, circleFromDrag, feedbackBody, lassoPath, markAnchor, MAX_LASSO_POINTS, normPoint, simplifyLasso, type DraftMark } from "./marks";

describe("concept sketch marks (normalised image coordinates)", () => {
  const box = { left: 100, top: 50, width: 400, height: 400 };

  it("maps a pointer to 0..1 of the image, clamped", () => {
    expect(normPoint(box, 300, 250)).toEqual({ x: 0.5, y: 0.5 });
    expect(normPoint(box, 100, 50)).toEqual({ x: 0, y: 0 });
    expect(normPoint(box, 40, 900)).toEqual({ x: 0, y: 1 });
    expect(normPoint({ ...box, width: 0 }, 300, 250)).toEqual({ x: 0, y: 0 });
  });

  it("a circle is dragged from its centre; its radius is a fraction of the image width", () => {
    expect(circleFromDrag({ x: 0.5, y: 0.5 }, { x: 0.6, y: 0.5 })).toEqual({ cx: 0.5, cy: 0.5, r: 0.1 });
    // a wide image: a vertical drag of 0.1 of the height is 0.05 of the width
    expect(circleFromDrag({ x: 0.5, y: 0.5 }, { x: 0.5, y: 0.6 }, 800, 400)).toEqual({ cx: 0.5, cy: 0.5, r: 0.05 });
    expect(circleFromDrag({ x: 0.5, y: 0.5 }, { x: 0.505, y: 0.5 })).toBeNull();   // a click, not a circle
  });

  it("a lasso keeps a simplified closed path of 3..400 points in 0..1", () => {
    expect(simplifyLasso([{ x: 0.1, y: 0.1 }, { x: 0.1001, y: 0.1 }])).toBeNull();
    const pts = simplifyLasso([{ x: 0.1, y: 0.1 }, { x: 0.1001, y: 0.1 }, { x: 0.3, y: 0.1 }, { x: 0.3, y: 0.4 }]);
    expect(pts).toEqual([[0.1, 0.1], [0.3, 0.1], [0.3, 0.4]]);
    const many = Array.from({ length: 2000 }, (_, i) => ({ x: 0.5 + 0.4 * Math.cos(i / 318), y: 0.5 + 0.4 * Math.sin(i / 318) }));
    const out = simplifyLasso(many)!;
    expect(out.length).toBeLessThanOrEqual(MAX_LASSO_POINTS);
    expect(out.every(([x, y]) => x >= 0 && x <= 1 && y >= 0 && y <= 1)).toBe(true);
    expect(lassoPath([[0, 0], [1, 0], [1, 1]], 10, 10)).toBe("M0.0 0.0 L10.0 0.0 L10.0 10.0 Z");
  });

  it("number badges sit where the core draws them", () => {
    expect(markAnchor({ kind: "pin", x: 0.2, y: 0.3, image: 0, comment: "" })).toEqual({ x: 0.2, y: 0.3 });
    const c = markAnchor({ kind: "circle", cx: 0.5, cy: 0.5, r: 0.1, image: 0, comment: "" });
    expect(c.x).toBeCloseTo(0.572); expect(c.y).toBeCloseTo(0.428);
    expect(markAnchor({ kind: "lasso", points: [[0.1, 0.5], [0.6, 0.2], [0.3, 0.9]], image: 0, comment: "" })).toEqual({ x: 0.6, y: 0.2 });
  });

  it("the POST body drops local ids and trims comments; an empty round is not sendable", () => {
    const marks: DraftMark[] = [{ id: "m1", kind: "pin", x: 0.2, y: 0.3, image: 1, comment: "  thicker legs " }];
    expect(feedbackBody(marks, " overall ")).toEqual({ marks: [{ kind: "pin", x: 0.2, y: 0.3, image: 1, comment: "thicker legs" }], comment: "overall" });
    expect(canSend([], "  ")).toBe(false);
    expect(canSend([], "x")).toBe(true);
    expect(canSend(marks, "")).toBe(true);
  });
});
