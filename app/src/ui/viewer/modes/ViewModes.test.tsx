import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { useState } from "preact/hooks";
import * as THREE from "three";
import { afterEach, describe, expect, it, vi } from "vitest";
import { loadPart } from "../loadPart";
import { createViewer, type DesignViewerHandle, type ModeHost, type RendererLike } from "../scene";
import { reference } from "./ReferenceOverlay";
import { MODES_OFF, type ModesState } from "./state";
import { ViewModes } from "./ViewModes";

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const fixtureBytes = (): ArrayBuffer =>
  new Uint8Array(readFileSync(resolve(import.meta.dirname, "../../../../../core/tests/fixtures/ui/mount_plate.glb"))).buffer as ArrayBuffer;
const stub = (): RendererLike => ({ info: { memory: { geometries: 0, textures: 0 } }, render: () => {}, setSize: vi.fn(), setPixelRatio: vi.fn(), setClearColor: vi.fn(), dispose: vi.fn() });

let v: DesignViewerHandle | null = null;
afterEach(() => { cleanup(); v?.dispose(); v = null; reference.value = null; vi.unstubAllGlobals(); });

async function host(n = 2): Promise<ModeHost> {
  v = createViewer(document.createElement("canvas"), { createRenderer: stub });
  const scenes = await Promise.all(Array.from({ length: n }, () => loadPart(fixtureBytes())));
  scenes.forEach((s, i) => { s.object.position.x = i * 50; s.object.updateMatrixWorld(true); s.bbox.setFromObject(s.object); });
  v.setParts(scenes.map((s, i) => ({ id: `p${i}`, scene: s, color: null, focus: i === 0 })), true, true);
  return v.modeHost!();
}

function Harness({ h, seen }: { h: ModeHost; seen: (m: ModesState) => void }) {
  const [m, setM] = useState<ModesState>(MODES_OFF);
  return <ViewModes host={h} modes={m} onModes={(next) => { seen(next); setM(next); }} focusId="p0" partIds={["p0", "p1"]} />;
}

const opacities = (h: ModeHost, id: string) => {
  const out: number[] = [];
  h.parts().find((p) => p.id === id)?.scene.object.traverse((o) => { if (o instanceof THREE.Mesh) out.push((o.material as THREE.Material).opacity); });
  return out;
};

describe("ViewModes", () => {
  it("x-ray: on for all, then per part", async () => {
    const h = await host();
    const seen = vi.fn();
    render(<Harness h={h} seen={seen} />);
    fireEvent.click(screen.getByRole("button", { name: "x-ray" }));
    expect(opacities(h, "p1").every((o) => o < 1)).toBe(true);
    fireEvent.click(screen.getByTitle("p1"));
    expect(opacities(h, "p0").every((o) => o === 1)).toBe(true);
    expect(opacities(h, "p1").every((o) => o < 1)).toBe(true);
    fireEvent.input(screen.getByLabelText("X-ray opacity"), { target: { value: "0.6" } });
    expect(opacities(h, "p1")[0]).toBeCloseTo(0.6);
  });

  it("section: axis, slider and the drag handle move the plane", async () => {
    const h = await host();
    const seen = vi.fn();
    render(<Harness h={h} seen={seen} />);
    fireEvent.click(screen.getByRole("button", { name: "section" }));
    fireEvent.click(screen.getByRole("button", { name: "Z" }));
    const slider = screen.getByTestId("section-slider") as HTMLInputElement;
    expect(Number(slider.min)).toBeCloseTo(-3); expect(Number(slider.max)).toBeCloseTo(3);
    fireEvent.input(slider, { target: { value: "1.5" } });
    expect(seen.mock.lastCall?.[0].section).toEqual({ on: true, normal: [0, 0, 1], at: 1.5 });
    const handle = screen.getByTestId("section-handle");
    fireEvent.keyDown(handle, { key: "ArrowDown", shiftKey: true });
    expect(seen.mock.lastCall?.[0].section.at).toBeLessThan(1.5);
    fireEvent.click(screen.getByRole("button", { name: "flip" }));
    expect(seen.mock.lastCall?.[0].section.normal).toEqual([-0, -0, -1]);
  });

  it("explode: the toggle glides, the slider follows the hand", async () => {
    const h = await host();
    const seen = vi.fn();
    render(<Harness h={h} seen={seen} />);
    const btn = screen.getByRole("button", { name: "explode" });
    expect((btn as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(btn);
    expect(seen.mock.lastCall?.[0].explode).toBe(1);
    fireEvent.input(screen.getByTestId("explode-slider"), { target: { value: "0.4" } });
    expect(seen.mock.lastCall?.[0].explode).toBeCloseTo(0.4);
  });

  it("explode is off for a single body", async () => {
    const h = await host(1);
    render(<Harness h={h} seen={vi.fn()} />);
    expect((screen.getByRole("button", { name: "explode" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("reference: an image file pins to the front view; a non-image is refused", async () => {
    const h = await host();
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:ref", revokeObjectURL: () => {} }));
    render(<Harness h={h} seen={vi.fn()} />);
    const input = screen.getByTestId("reference-file") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["x"], "notes.txt", { type: "text/plain" })] } });
    expect(screen.getByRole("alert").textContent).toContain("PNG");
    fireEvent.change(input, { target: { files: [new File(["x"], "b2.png", { type: "image/png" })] } });
    const ov = await screen.findByTestId("reference-overlay");
    expect(ov.getAttribute("data-view")).toBe("front");
    expect((ov.querySelector("img") as HTMLImageElement).src).toBe("blob:ref");
    // orbit away: the overlay hides and offers to go back
    h.camera.position.set(100, 100, 100); h.camera.lookAt(h.controls.target);
    h.controls.dispatchEvent({ type: "change" });
    fireEvent.click(await screen.findByTestId("reference-away"));
    expect(await screen.findByTestId("reference-overlay")).toBeTruthy();
  });
});
