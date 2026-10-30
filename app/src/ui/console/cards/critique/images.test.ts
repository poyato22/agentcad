import { describe, expect, it } from "vitest";
import { knockOutBackground } from "./images";

/** A w×h RGBA image: white, with a filled square of `inner` at (1..3, 1..3) and a white dot inside it. */
function picture(w: number, h: number, inner: [number, number, number]): Uint8ClampedArray {
  const px = new Uint8ClampedArray(w * h * 4).fill(255);
  for (let y = 1; y <= 3; y++) for (let x = 1; x <= 3; x++) {
    const i = (y * w + x) * 4;
    [px[i], px[i + 1], px[i + 2]] = inner;
  }
  const c = (2 * w + 2) * 4;
  [px[c], px[c + 1], px[c + 2]] = [255, 255, 255];        // a white face inside the part
  return px;
}

describe("view-card thumbnail backdrop (m10)", () => {
  it("makes the white around the part transparent, not the white inside it", () => {
    const w = 6; const h = 5;
    const px = picture(w, h, [90, 100, 110]);
    const n = knockOutBackground(px, w, h);
    expect(n).toBe(w * h - 9);                                 // everything outside the 3×3 square
    expect(px[(0 * w + 0) * 4 + 3]).toBe(0);
    expect(px[(1 * w + 1) * 4 + 3]).toBe(255);                 // the part
    expect(px[(2 * w + 2) * 4 + 3]).toBe(255);                 // its white face stays
  });
});
