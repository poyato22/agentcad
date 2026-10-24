import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { trackPastes } from "../../../lib/keys";
import { ARM_MS, focusIsKeyboard, keyToAction, resetFocusModality, trackFocusModality } from "./approvalKeys";

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
    expect(keyToAction(key({ key: "m" }), card, ARMED, LATER, ARMED)).toBe("approve");
    expect(keyToAction(key({ key: "a" }), card, ARMED, LATER, ARMED)).toBe("approve");
    expect(keyToAction(key({ key: "k" }), card, ARMED, LATER, ARMED)).toBe("keep_branch");
    expect(keyToAction(key({ key: "r" }), card, ARMED, LATER, ARMED)).toBe("reject");
    expect(keyToAction(key({ key: "d" }), card, ARMED, LATER, ARMED)).toBe("diff");
    expect(keyToAction(key({ key: "x" }), card, ARMED, LATER, ARMED)).toBeNull();
    expect(keyToAction(key({ key: "Enter" }), card, ARMED, LATER, ARMED)).toBeNull();
  });

  it("works_with_focus_on_a_button_inside_the_card", () => {
    (card.querySelector("button") as HTMLButtonElement).focus();
    expect(keyToAction(key({ key: "m" }), card, ARMED, LATER, ARMED)).toBe("approve");
  });

  it("key_ignored_when_focus_elsewhere", () => {
    outside.focus();
    expect(keyToAction(key({ key: "m", target: outside }), card, ARMED, LATER, ARMED)).toBeNull();
    (document.activeElement as HTMLElement).blur();
    expect(keyToAction(key({ key: "m", target: document.body }), card, ARMED, LATER, ARMED)).toBeNull();
  });

  it("key_ignored_when_untrusted", () => {
    card.focus();
    expect(keyToAction(key({ isTrusted: false }), card, ARMED, LATER, ARMED)).toBeNull();
    // a real dispatched event is untrusted too
    const real = new KeyboardEvent("keydown", { key: "m", bubbles: true });
    expect(keyToAction(real, card, ARMED, LATER, ARMED)).toBeNull();
  });

  it("key_ignored_on_repeat_or_composition", () => {
    card.focus();
    expect(keyToAction(key({ repeat: true }), card, ARMED, LATER, ARMED)).toBeNull();
    expect(keyToAction(key({ isComposing: true }), card, ARMED, LATER, ARMED)).toBeNull();
  });

  it("key_ignored_with_modifiers", () => {
    card.focus();
    for (const mod of ["metaKey", "ctrlKey", "altKey", "shiftKey"] as const) {
      expect(keyToAction(key({ [mod]: true }), card, ARMED, LATER, ARMED)).toBeNull();
    }
  });

  it("key_ignored_within_400ms_of_mount", () => {
    card.focus();
    expect(keyToAction(key({}), card, 1000, 1000 + ARM_MS - 1, 0)).toBeNull();
    expect(keyToAction(key({}), card, 1000, 1000 + ARM_MS, 0)).toBe("approve");
  });

  it("key_ignored_in_text_input_inside_card", () => {
    const inner = document.createElement("input");
    card.append(inner);
    inner.focus();
    expect(keyToAction(key({ target: inner }), card, ARMED, LATER, ARMED)).toBeNull();
    const ta = document.createElement("textarea");
    card.append(ta);
    ta.focus();
    expect(keyToAction(key({ target: ta }), card, ARMED, LATER, ARMED)).toBeNull();
  });

  it("decision_letters_need_keyboard_focus_at_least_400ms_old", () => {
    card.focus();
    // focus came by pointer (or a pointer pressed inside the card since): no letter decides
    for (const k of ["m", "a", "k", "r"]) expect(keyToAction(key({ key: k }), card, ARMED, 5000, null)).toBeNull();
    // d only toggles the diff: it stays available
    expect(keyToAction(key({ key: "d" }), card, ARMED, 5000, null)).toBe("diff");
    // keyboard focus, but younger than ARM_MS
    expect(keyToAction(key({ key: "m" }), card, ARMED, 5000, 5000 - ARM_MS + 1)).toBeNull();
    expect(keyToAction(key({ key: "m" }), card, ARMED, 5000, 5000 - ARM_MS)).toBe("approve");
  });

  it("focus_modality_pointer_press_is_never_keyboard_focus", () => {
    resetFocusModality();
    trackFocusModality(document);
    // no pointer yet: the browser's :focus-visible decides (jsdom: true)
    card.focus();
    expect(focusIsKeyboard(card)).toBe(true);
    // after any pointer press, focus is never keyboard focus (until a trusted key; see ApprovalCard.keys.test)
    card.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    expect(focusIsKeyboard(card)).toBe(false);
    // an untrusted key doesn't switch back to keyboard
    document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
    expect(focusIsKeyboard(card)).toBe(false);
    resetFocusModality();
  });

  // last: it leaves a recent paste in the module-level tracker
  it("key_ignored_within_1s_after_paste", () => {
    const stop = trackPastes(document);
    const t0 = 50_000; // an integer: t0 + 1000 - t0 must be exactly 1000
    vi.spyOn(performance, "now").mockImplementation(() => t0);
    card.focus();
    document.dispatchEvent(new Event("paste", { bubbles: true }));
    expect(keyToAction(key({}), card, ARMED, LATER, ARMED)).toBeNull();
    vi.spyOn(performance, "now").mockImplementation(() => t0 + 999);
    expect(keyToAction(key({}), card, ARMED, LATER, ARMED)).toBeNull();
    vi.spyOn(performance, "now").mockImplementation(() => t0 + 1000);
    expect(keyToAction(key({}), card, ARMED, LATER, ARMED)).toBe("approve");
    stop();
  });
});
