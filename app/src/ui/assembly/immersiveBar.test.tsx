import { act, cleanup, render, screen } from "@testing-library/preact";
import * as THREE from "three";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ModeHost } from "../viewer/scene";
import { useRestBox } from "./ImmersiveBar";

afterEach(() => { cleanup(); vi.useRealTimers(); });

function Probe({ host }: { host: ModeHost | null }) {
  const box = useRestBox(host);
  return <span data-testid="box">{box ? "box" : "none"}</span>;
}

describe("the full window's Section (study playback)", () => {
  it("turns on once the assembly's parts have loaded, without another re-render", () => {
    vi.useFakeTimers();
    let parts: unknown[] = [];
    const host = { parts: () => parts } as unknown as ModeHost;
    render(<Probe host={host} />);
    expect(screen.getByTestId("box").textContent).toBe("none");
    parts = [{ scene: { bbox: new THREE.Box3(new THREE.Vector3(0, 0, 0), new THREE.Vector3(1, 1, 1)) } }];
    act(() => { vi.advanceTimersByTime(1000); });
    expect(screen.getByTestId("box").textContent).toBe("box");
  });
});
