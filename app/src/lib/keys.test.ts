import { afterEach, describe, expect, it, vi } from "vitest";
import { isTrustedKey, msSinceLastPaste, registerShortcut, trackPastes } from "./keys";

function fakeKey(over: Partial<KeyboardEvent>): KeyboardEvent {
  return { isTrusted: true, repeat: false, isComposing: false, key: "m", ...over } as KeyboardEvent;
}

describe("isTrustedKey", () => {
  it("untrusted_or_repeat_keys_rejected", () => {
    expect(isTrustedKey(fakeKey({}))).toBe(true);
    expect(isTrustedKey(fakeKey({ isTrusted: false }))).toBe(false);
    expect(isTrustedKey(fakeKey({ repeat: true }))).toBe(false);
    expect(isTrustedKey(fakeKey({ isComposing: true }))).toBe(false);
    // a real synthetic event from script is never trusted
    expect(isTrustedKey(new KeyboardEvent("keydown", { key: "m" }))).toBe(false);
  });
});

describe("paste tracking", () => {
  it("paste_is_tracked", () => {
    const stop = trackPastes(document);
    vi.spyOn(performance, "now").mockReturnValue(10_000);
    document.body.dispatchEvent(new Event("paste", { bubbles: true }));
    vi.spyOn(performance, "now").mockReturnValue(10_250);
    expect(msSinceLastPaste()).toBe(250);
    vi.spyOn(performance, "now").mockReturnValue(20_000);
    document.body.dispatchEvent(new Event("drop", { bubbles: true }));
    vi.spyOn(performance, "now").mockReturnValue(20_100);
    expect(msSinceLastPaste()).toBe(100);
    stop();
    vi.spyOn(performance, "now").mockReturnValue(30_000);
    document.body.dispatchEvent(new Event("paste", { bubbles: true }));
    vi.spyOn(performance, "now").mockReturnValue(30_001);
    expect(msSinceLastPaste()).toBe(10_001); // listener removed: still the drop time
  });
});

describe("registerShortcut", () => {
  const offs: (() => void)[] = [];
  afterEach(() => { offs.splice(0).forEach((f) => f()); document.body.replaceChildren(); });

  it("shortcut_fires_inside_input", () => {
    const input = document.createElement("input");
    document.body.append(input);
    input.focus();
    const h = vi.fn();
    const inputHandler = vi.fn((e: KeyboardEvent) => e.stopPropagation());
    input.addEventListener("keydown", inputHandler);
    offs.push(registerShortcut("Meta+Shift+.", h));
    // ⌘⇧. on a US layout reports key ">" with code "Period"
    input.dispatchEvent(new KeyboardEvent("keydown", { key: ">", code: "Period", metaKey: true, shiftKey: true, bubbles: true, cancelable: true }));
    expect(h).toHaveBeenCalledTimes(1);
  });

  it("requires_exact_modifiers", () => {
    const h = vi.fn();
    offs.push(registerShortcut("Meta+K", h));
    const send = (o: KeyboardEventInit) => document.body.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...o }));
    send({ key: "k", code: "KeyK" });
    send({ key: "k", code: "KeyK", metaKey: true, shiftKey: true });
    expect(h).not.toHaveBeenCalled();
    send({ key: "k", code: "KeyK", metaKey: true });
    expect(h).toHaveBeenCalledTimes(1);
  });

  it("prevents_default_and_unregisters", () => {
    const h = vi.fn();
    const off = registerShortcut("Meta+N", h);
    const e = new KeyboardEvent("keydown", { key: "n", code: "KeyN", metaKey: true, bubbles: true, cancelable: true });
    document.body.dispatchEvent(e);
    expect(e.defaultPrevented).toBe(true);
    off();
    document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "n", code: "KeyN", metaKey: true, bubbles: true }));
    expect(h).toHaveBeenCalledTimes(1);
  });
});
