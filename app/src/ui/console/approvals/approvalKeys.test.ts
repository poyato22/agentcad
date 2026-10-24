import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { trackPastes } from "../../../lib/keys";
import { ARM_MS, keyToAction } from "./approvalKeys";

// keyToAction gets real-looking KeyboardEvent objects: jsdom can't make a trusted event, so the
// trusted flag is set on a plain object. (The real browser path is covered by e2e/approvals.spec.ts.)
type KeyInit = Partial<Pick<KeyboardEvent, "key" | "isTrusted" | "repeat" | "isComposing" | "metaKey" | "ctrlKey" | "altKey" | "shiftKey">> & { target?: EventTarget };

function key(init: KeyInit): KeyboardEvent {
  return {
    key: "m", isTrusted: true, repeat: false, isComposing: false,
    metaKey: false, ctrlKey: false, altKey: false, shiftKey: false, target: document.activeElement,
    ...init,
  } as unknown as KeyboardEvent;
}

let card: HTMLDivElement;
let outside: HTMLInputElement;
const ARMED = 0;
const LATER = ARM_MS + 1;

beforeEach(() => {
  card = document.createElement("div");
  card.tabIndex = 0;
  const btn = document.createElement("button");
  btn.textContent = "Merge";
  card.append(btn);
  outside = document.createElement("input");
  document.body.append(card, outside);
});

afterEach(() => { document.body.replaceChildren(); });

describe("keyToAction", () => {
  it("maps_keys_when_card_focused", () => {
    card.focus();
    expect(keyToAction(key({ key: "m" }), card, ARMED, LATER)).toBe("approve");
    expect(keyToAction(key({ key: "a" }), card, ARMED, LATER)).toBe("approve");
    expect(keyToAction(key({ key: "k" }), card, ARMED, LATER)).toBe("keep_branch");
    expect(keyToAction(key({ key: "r" }), card, ARMED, LATER)).toBe("reject");
    expect(keyToAction(key({ key: "d" }), card, ARMED, LATER)).toBe("diff");
    expect(keyToAction(key({ key: "x" }), card, ARMED, LATER)).toBeNull();
    expect(keyToAction(key({ key: "Enter" }), card, ARMED, LATER)).toBeNull();
  });

  it("works_with_focus_on_a_button_inside_the_card", () => {
    (card.querySelector("button") as HTMLButtonElement).focus();
    expect(keyToAction(key({ key: "m" }), card, ARMED, LATER)).toBe("approve");
  });

  it("key_ignored_when_focus_elsewhere", () => {
    outside.focus();
    expect(keyToAction(key({ key: "m", target: outside }), card, ARMED, LATER)).toBeNull();
    (document.activeElement as HTMLElement).blur();
    expect(keyToAction(key({ key: "m", target: document.body }), card, ARMED, LATER)).toBeNull();
  });

  it("key_ignored_when_untrusted", () => {
    card.focus();
    expect(keyToAction(key({ isTrusted: false }), card, ARMED, LATER)).toBeNull();
    // a real dispatched event is untrusted too
    const real = new KeyboardEvent("keydown", { key: "m", bubbles: true });
    expect(keyToAction(real, card, ARMED, LATER)).toBeNull();
  });

  it("key_ignored_on_repeat_or_composition", () => {
    card.focus();
    expect(keyToAction(key({ repeat: true }), card, ARMED, LATER)).toBeNull();
    expect(keyToAction(key({ isComposing: true }), card, ARMED, LATER)).toBeNull();
  });

  it("key_ignored_with_modifiers", () => {
    card.focus();
    for (const mod of ["metaKey", "ctrlKey", "altKey", "shiftKey"] as const) {
      expect(keyToAction(key({ [mod]: true }), card, ARMED, LATER)).toBeNull();
    }
  });

  it("key_ignored_within_400ms_of_mount", () => {
    card.focus();
    expect(keyToAction(key({}), card, 1000, 1000 + ARM_MS - 1)).toBeNull();
    expect(keyToAction(key({}), card, 1000, 1000 + ARM_MS)).toBe("approve");
  });

  it("key_ignored_in_text_input_inside_card", () => {
    const inner = document.createElement("input");
    card.append(inner);
    inner.focus();
    expect(keyToAction(key({ target: inner }), card, ARMED, LATER)).toBeNull();
    const ta = document.createElement("textarea");
    card.append(ta);
    ta.focus();
    expect(keyToAction(key({ target: ta }), card, ARMED, LATER)).toBeNull();
  });

  // last: it leaves a recent paste in the module-level tracker
  it("key_ignored_within_1s_after_paste", () => {
    const stop = trackPastes(document);
    const t0 = 50_000; // an integer: t0 + 1000 - t0 must be exactly 1000
    vi.spyOn(performance, "now").mockImplementation(() => t0);
    card.focus();
    document.dispatchEvent(new Event("paste", { bubbles: true }));
    expect(keyToAction(key({}), card, ARMED, LATER)).toBeNull();
    vi.spyOn(performance, "now").mockImplementation(() => t0 + 999);
    expect(keyToAction(key({}), card, ARMED, LATER)).toBeNull();
    vi.spyOn(performance, "now").mockImplementation(() => t0 + 1000);
    expect(keyToAction(key({}), card, ARMED, LATER)).toBe("approve");
    stop();
  });
});
