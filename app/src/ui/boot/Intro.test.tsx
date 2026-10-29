import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { IntroHandle } from "./introScene";
import { Intro, SKIP_FADE_MS } from "./Intro";

function fakeIntro(): { create: () => IntroHandle; handle: IntroHandle; triggerAssembled: () => void } {
  let onAssembled: (() => void) | undefined;
  const handle: IntroHandle = { start: vi.fn(), skip: vi.fn(), resize: vi.fn(), dispose: vi.fn() };
  const create = vi.fn((_c: HTMLCanvasElement, opts?: { onAssembled?: () => void }) => {
    onAssembled = opts?.onAssembled;
    return handle;
  }) as unknown as () => IntroHandle;
  return { create, handle, triggerAssembled: () => onAssembled?.() };
}

afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("Intro", () => {
  it("plays the assembly then types the title (full motion)", () => {
    const { create, handle } = fakeIntro();
    render(<Intro onDone={vi.fn()} createIntro={create} reducedMotion={false} />);
    expect(handle.start).toHaveBeenCalledOnce();
    expect(handle.skip).not.toHaveBeenCalled();
    expect(screen.getByText("Welcome to Mises")).toBeTruthy();
  });

  it("is skippable by click", () => {
    vi.useFakeTimers();
    const onDone = vi.fn();
    const { create, handle } = fakeIntro();
    render(<Intro onDone={onDone} createIntro={create} reducedMotion={false} />);
    fireEvent.click(screen.getByRole("button", { name: /skip/i }));
    expect(handle.skip).toHaveBeenCalledOnce();
    vi.runAllTimers();
    expect(onDone).toHaveBeenCalledOnce();
  });

  it("a skip fades for exactly as long as it waits before onDone", () => {
    vi.useFakeTimers();
    const onDone = vi.fn();
    const { create } = fakeIntro();
    render(<Intro onDone={onDone} createIntro={create} reducedMotion={false} />);
    const el = screen.getByRole("button", { name: /skip/i });
    fireEvent.click(el);
    expect(el.style.getPropertyValue("--fade-ms")).toBe(`${SKIP_FADE_MS}ms`);
    vi.advanceTimersByTime(SKIP_FADE_MS - 1);
    expect(onDone).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(onDone).toHaveBeenCalledOnce();
  });

  it("boot overlays carry a window drag region clear of the traffic lights", () => {
    const { create } = fakeIntro();
    const { container } = render(<Intro onDone={vi.fn()} createIntro={create} reducedMotion={false} />);
    expect(container.querySelector(".boot-drag[data-tauri-drag-region]")).not.toBeNull();
  });

  it("is skippable by any key", () => {
    vi.useFakeTimers();
    const onDone = vi.fn();
    const { create } = fakeIntro();
    render(<Intro onDone={onDone} createIntro={create} reducedMotion={false} />);
    fireEvent.keyDown(screen.getByRole("button", { name: /skip/i }), { key: "x" });
    vi.runAllTimers();
    expect(onDone).toHaveBeenCalledOnce();
  });

  it("respects reduced motion: no fly-in, a short fade to done", () => {
    vi.useFakeTimers();
    const onDone = vi.fn();
    const { create, handle } = fakeIntro();
    render(<Intro onDone={onDone} createIntro={create} reducedMotion={true} />);
    // the fly-in is skipped and the title shows instantly
    expect(handle.start).not.toHaveBeenCalled();
    expect(handle.skip).toHaveBeenCalled();
    expect(screen.getByText("Welcome to Mises").className).toContain("is-instant");
    expect(onDone).not.toHaveBeenCalled();
    vi.runAllTimers();
    expect(onDone).toHaveBeenCalledOnce();
  });

  it("calls onDone only once", () => {
    vi.useFakeTimers();
    const onDone = vi.fn();
    const { create } = fakeIntro();
    render(<Intro onDone={onDone} createIntro={create} reducedMotion={false} />);
    const btn = screen.getByRole("button", { name: /skip/i });
    fireEvent.click(btn);
    fireEvent.click(btn);
    fireEvent.keyDown(btn, { key: "Enter" });
    vi.runAllTimers();
    expect(onDone).toHaveBeenCalledOnce();
  });
});
