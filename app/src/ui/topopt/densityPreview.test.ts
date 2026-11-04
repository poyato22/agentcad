import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";
import { placeView } from "./DensityPreview";

describe("the density preview's framing", () => {
  it("fits the frustum to the canvas's shape, so a wide panel does not stretch the part", () => {
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 1000);
    camera.up.set(0, 0, 1);
    const renderer = { setSize: vi.fn(), render: vi.fn() };
    const bbox = new THREE.Box3(new THREE.Vector3(-50, -50, 0), new THREE.Vector3(50, 50, 2));
    const v = { renderer, scene: new THREE.Scene(), camera, part: { bbox } , az: 35, el: 30 };
    placeView(v as never, 1064, 300);
    const aspect = (camera.right - camera.left) / (camera.top - camera.bottom);
    expect(aspect).toBeCloseTo(1064 / 300, 3);
    // the drawing buffer follows the canvas's CSS size (not the 320x240 guess made before layout)
    expect(renderer.setSize).toHaveBeenCalledWith(1064, 300, false);
    expect(renderer.render).toHaveBeenCalled();
  });
});
