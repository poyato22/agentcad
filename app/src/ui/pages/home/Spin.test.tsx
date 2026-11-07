import { act, render } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import { resetSpins, Spin, SPIN_STEP_MS } from "./Spin";

class FakeImage { onload: (() => void) | null = null; onerror: (() => void) | null = null; naturalWidth = 16 * 320; naturalHeight = 200;
  set src(_v: string) { queueMicrotask(() => this.onload?.()); } }

describe("Home's hover turntable", () => {
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); resetSpins(); });
  it("fetches the strip once on hover, then steps its 16 frames; nothing when not hovered", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("Image", FakeImage);
    vi.stubGlobal("URL", { ...URL, createObjectURL: () => "blob:spin", revokeObjectURL: () => undefined });
    const load = vi.fn().mockResolvedValue(new Blob(["x"]));
    const { container, rerender } = render(<Spin active={false} cacheKey="b1:1" load={load} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(300); });
    expect(load).not.toHaveBeenCalled();
    rerender(<Spin active cacheKey="b1:1" load={load} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(200); });
    const el = container.querySelector<HTMLElement>("[data-testid=tile-spin]")!;
    expect(el.style.backgroundSize).toBe("1600% 100%");
    expect(el.dataset.frame).toBe("0");
    await act(async () => { await vi.advanceTimersByTimeAsync(SPIN_STEP_MS * 3); });
    expect(container.querySelector<HTMLElement>("[data-testid=tile-spin]")!.dataset.frame).toBe("3");
    rerender(<Spin active={false} cacheKey="b1:1" load={load} />);
    expect(container.querySelector("[data-testid=tile-spin]")).toBeNull();
    rerender(<Spin active cacheKey="b1:1" load={load} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(200); });
    expect(load).toHaveBeenCalledTimes(1);                    // fetched once
  });
});
