import { describe, expect, it } from "vitest";
import { clampScale, fitView, MAX_SCALE, MIN_SCALE, panBy, zoomAt, zoomPercent } from "./panzoom";

describe("panzoom", () => {
  it("fit_centres_the_sheet_inside_the_padding", () => {
    const v = fitView({ w: 1000, h: 600 }, { w: 297, h: 210 }, 20);
    // height-limited: (600 − 40) / 210
    expect(v.scale).toBeCloseTo(560 / 210, 6);
    expect(v.y).toBeCloseTo(20, 6);
    expect(v.x).toBeCloseTo((1000 - 297 * v.scale) / 2, 6);
  });

  it("fit_of_an_empty_viewport_is_scale_one_at_origin", () => {
    expect(fitView({ w: 0, h: 0 }, { w: 297, h: 210 })).toEqual({ scale: 1, x: 0, y: 0 });
  });

  it("zoom_at_a_point_keeps_that_point_under_the_cursor", () => {
    const v = { scale: 2, x: 10, y: 30 };
    const px = 400;
    const py = 250;
    const before = { x: (px - v.x) / v.scale, y: (py - v.y) / v.scale }; // sheet coords under the cursor
    const z = zoomAt(v, 1.5, px, py);
    expect(z.scale).toBe(3);
    expect((px - z.x) / z.scale).toBeCloseTo(before.x, 9);
    expect((py - z.y) / z.scale).toBeCloseTo(before.y, 9);
  });

  it("zoom_is_clamped_and_a_clamped_zoom_does_not_move_the_view", () => {
    expect(clampScale(1e9)).toBe(MAX_SCALE);
    expect(clampScale(1e-9)).toBe(MIN_SCALE);
    expect(clampScale(Number.NaN)).toBe(1);
    const v = { scale: MAX_SCALE, x: 5, y: 6 };
    expect(zoomAt(v, 2, 100, 100)).toEqual(v);
  });

  it("pan_moves_by_screen_pixels", () => {
    expect(panBy({ scale: 3, x: 1, y: 2 }, 10, -5)).toEqual({ scale: 3, x: 11, y: -3 });
  });

  it("zoom_percent_is_relative_to_fit", () => {
    expect(zoomPercent({ scale: 4, x: 0, y: 0 }, { scale: 2, x: 0, y: 0 })).toBe("200%");
    expect(zoomPercent({ scale: 1, x: 0, y: 0 }, { scale: 3, x: 0, y: 0 })).toBe("33%");
  });
});
