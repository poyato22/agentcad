// Keyboard wiring of the card against the fake core. jsdom can't make trusted events, so here
// isTrustedKey accepts events marked `testTrusted` (and still rejects repeats); everything else
// (focus check, arm delay, paste guard, modifiers) is the real code. Clicks are NOT stubbed in
// this file, so jsdom's untrusted clicks must never approve. Real browser input: e2e/approvals.spec.ts.
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { startFakeCore, type FakeCore } from "../../../../test/fake-core/server";
import { CoreClient } from "../../../api/client";
import type { Approval } from "../../../api/types";
import { trackPastes } from "../../../lib/keys";
import { actions, approvals, bindClient, resetStore } from "../../../state/store";
import { ApprovalCard } from "./ApprovalCard";
import { resetFocusModality } from "./approvalKeys";
import { resetApprovalUi } from "./decide";

vi.mock("../../../lib/keys", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../../lib/keys")>()),
  isTrustedKey: (e: KeyboardEvent) => (e as KeyboardEvent & { testTrusted?: boolean }).testTrusted === true && !e.repeat && !e.isComposing,
}));

const AID = "ap_merge_01";
let core: FakeCore;
let clock = 0;
let stopPastes: () => void;

const decideCalls = () => core.state.calls.filter((c) => c.path.endsWith("/decide"));
const settle = () => new Promise((r) => setTimeout(r, 40));

function press(target: Element, key: string, init: KeyboardEventInit & { trusted?: boolean } = {}) {
  const e = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...init });
  if (init.trusted !== false) Object.defineProperty(e, "testTrusted", { value: true });
  target.dispatchEvent(e);
  return e;
}

/** Focus arriving by keyboard: a trusted Tab keydown, then the focus move it causes. */
function keyboardFocus(el: HTMLElement) {
  press(document.body, "Tab");
  el.focus();
}

function mount(): HTMLElement {
  const a = core.state.approvals.get(AID) as Approval;
  actions.upsertApproval(a);
  render(<div><input data-testid="elsewhere" /><ApprovalCard approval={a} runLabel="designer · stiffen upright bracket" /></div>);
  return document.querySelector("[data-card]") as HTMLElement;
}

beforeEach(async () => {
  // monotonic across tests: the paste tracker keeps the last paste time between tests
  clock += 100_000;
  vi.spyOn(performance, "now").mockImplementation(() => clock);
  stopPastes = trackPastes(document);
  core = await startFakeCore({ port: 0, token: "t", scenario: "approvals" });
  bindClient(new CoreClient({ baseUrl: core.url, wsUrl: "", token: "t", owned: false }));
});

afterEach(async () => {
  stopPastes();
  cleanup();
  resetStore();
  resetApprovalUi();
  resetFocusModality();
  await core.stop();
});

describe("ApprovalCard keys", () => {
  it("key_m_approves_only_when_card_focused", async () => {
    const card = mount();
    keyboardFocus(card);
    clock += 500;
    const e = press(card, "m");
    expect(e.defaultPrevented).toBe(true);
    await vi.waitFor(() => expect(decideCalls()).toHaveLength(1));
    expect(decideCalls()[0].body).toEqual({ payload_hash: (core.state.approvals.get(AID) as Approval).payload_hash, approve: true });
    await vi.waitFor(() => expect(approvals.value.get(AID)?.status).toBe("approved"));
  });

  it("click_card_text_then_letter_never_decides", async () => {
    const card = mount();
    clock += 5000;
    // a pointer press on the card's text focuses the card (tabIndex=0): the next typed letter is not a decision
    const text = card.querySelector(".acard-what") as HTMLElement;
    text.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    card.focus();
    clock += 5000;
    for (const k of ["m", "a", "k", "r"]) press(card, k);
    await settle();
    expect(decideCalls()).toEqual([]);
    // keyboard focus first, then a click inside the card: letters are off again
    card.blur();
    keyboardFocus(card);
    clock += 500;
    text.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    press(card, "m");
    await settle();
    expect(decideCalls()).toEqual([]);
  });

  it("window_refocus_after_cmd_tab_does_not_rearm_letters", async () => {
    const card = mount();
    clock += 5000;
    // pointer focus (letters off), then Cmd-Tab away: the trusted Meta key flips the modality to keyboard
    const text = card.querySelector(".acard-what") as HTMLElement;
    text.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    card.focus();
    window.dispatchEvent(new Event("blur"));
    press(document.body, "Meta", { metaKey: true });
    // Cmd-Tab back: the browser re-fires focus/focusin on the element that was active
    window.dispatchEvent(new Event("focus"));
    card.dispatchEvent(new FocusEvent("focusin", { bubbles: true }));
    clock += 5000;
    for (const k of ["m", "a", "k", "r"]) press(card, k);
    await settle();
    expect(decideCalls()).toEqual([]);
    // a real Tab-in afterwards still arms them
    card.blur();
    keyboardFocus(card);
    clock += 500;
    press(card, "m");
    await vi.waitFor(() => expect(decideCalls()).toHaveLength(1));
  });

  it("window_blur_clears_keyboard_focus", async () => {
    const card = mount();
    clock += 5000;
    keyboardFocus(card);
    clock += 500;
    window.dispatchEvent(new Event("blur"));
    window.dispatchEvent(new Event("focus"));
    card.dispatchEvent(new FocusEvent("focusin", { bubbles: true }));
    clock += 5000;
    press(card, "m");
    await settle();
    expect(decideCalls()).toEqual([]);
  });

  it("tab_into_card_then_wait_then_m_decides_once", async () => {
    const card = mount();
    clock += 5000;
    keyboardFocus(card);
    clock += 399;
    press(card, "m");
    await settle();
    expect(decideCalls()).toEqual([]);
    clock += 1;
    press(card, "m");
    press(card, "m");
    await vi.waitFor(() => expect(decideCalls()).toHaveLength(1));
    await settle();
    expect(decideCalls()).toHaveLength(1);
  });

  it("key_r_and_k_send_approve_false_and_d_toggles_diff", async () => {
    const card = mount();
    keyboardFocus(card);
    clock += 500;
    press(card, "d");
    await settle();
    expect(document.querySelector("[data-diff]")).not.toBeNull();
    expect(core.state.calls).toEqual([]);
    press(card, "k");
    await vi.waitFor(() => expect(decideCalls()).toHaveLength(1));
    expect((decideCalls()[0].body as { approve: boolean }).approve).toBe(false);
  });

  it("key_ignored_when_focus_elsewhere", async () => {
    const card = mount();
    clock += 500;
    const input = screen.getByTestId("elsewhere");
    input.focus();
    press(input, "m");
    // even an event aimed at the card does nothing while focus is outside it
    press(card, "m");
    press(document.body, "m");
    await settle();
    expect(decideCalls()).toEqual([]);
  });

  it("key_ignored_when_untrusted", async () => {
    const card = mount();
    clock += 500;
    keyboardFocus(card);
    card.dispatchEvent(new KeyboardEvent("keydown", { key: "m", bubbles: true }));
    press(card, "a", { trusted: false });
    await settle();
    expect(decideCalls()).toEqual([]);
  });

  it("key_ignored_on_repeat", async () => {
    const card = mount();
    clock += 500;
    keyboardFocus(card);
    press(card, "m", { repeat: true });
    // a held Enter on the focused Merge button must not turn into repeated clicks
    const merge = screen.getByRole("button", { name: /^Merge/ });
    merge.focus();
    const held = press(merge, "Enter", { repeat: true });
    expect(held.defaultPrevented).toBe(true);
    await settle();
    expect(decideCalls()).toEqual([]);
  });

  it("key_ignored_with_modifier", async () => {
    const card = mount();
    clock += 500;
    keyboardFocus(card);
    press(card, "m", { metaKey: true });
    press(card, "m", { ctrlKey: true });
    await settle();
    expect(decideCalls()).toEqual([]);
  });

  it("key_ignored_within_400ms_of_mount", async () => {
    const card = mount();
    keyboardFocus(card);
    clock += 399;
    press(card, "m");
    await settle();
    expect(decideCalls()).toEqual([]);
    clock += 1;
    press(card, "m");
    await vi.waitFor(() => expect(decideCalls()).toHaveLength(1));
  });

  it("key_ignored_within_1s_after_paste", async () => {
    const card = mount();
    clock += 500;
    keyboardFocus(card);
    document.dispatchEvent(new Event("paste", { bubbles: true }));
    clock += 999;
    press(card, "m");
    await settle();
    expect(decideCalls()).toEqual([]);
    clock += 1;
    press(card, "m");
    await vi.waitFor(() => expect(decideCalls()).toHaveLength(1));
  });

  it("pasting_text_into_card_never_approves", async () => {
    const card = mount();
    clock += 5000;
    keyboardFocus(card);
    const paste = new Event("paste", { bubbles: true, cancelable: true });
    Object.defineProperty(paste, "clipboardData", { value: { getData: () => "m\n", types: ["text/plain"] } });
    card.dispatchEvent(paste);
    fireEvent.input(card, { data: "m\n" });
    await settle();
    expect(decideCalls()).toEqual([]);
  });

  it("enter_or_space_on_focused_merge_after_arming_decides_once", async () => {
    mount();
    clock += 500;
    const merge = screen.getByRole("button", { name: /^Merge/ });
    merge.focus();
    const down = press(merge, "Enter");
    expect(down.defaultPrevented).toBe(true);
    await vi.waitFor(() => expect(decideCalls()).toHaveLength(1));
    press(merge, "Enter");
    press(merge, " ");
    await settle();
    expect(decideCalls()).toHaveLength(1);
    expect(decideCalls()[0].body).toMatchObject({ approve: true });
  });

  it("space_on_focused_reject_decides_at_keydown_and_blocks_keyup_activation", async () => {
    mount();
    clock += 500;
    const reject = screen.getByRole("button", { name: /^Reject/ });
    reject.focus();
    expect(press(reject, " ").defaultPrevented).toBe(true);
    const up = new KeyboardEvent("keyup", { key: " ", bubbles: true, cancelable: true });
    reject.dispatchEvent(up);
    expect(up.defaultPrevented).toBe(true);
    await vi.waitFor(() => expect(decideCalls()).toHaveLength(1));
    expect(decideCalls()[0].body).toMatchObject({ approve: false });
  });

  it("enter_on_merge_within_1s_after_paste_is_ignored", async () => {
    mount();
    clock += 500;
    const merge = screen.getByRole("button", { name: /^Merge/ });
    merge.focus();
    document.dispatchEvent(new Event("paste", { bubbles: true }));
    clock += 500;
    expect(press(merge, "Enter").defaultPrevented).toBe(true);
    expect(press(merge, " ").defaultPrevented).toBe(true);
    await settle();
    expect(decideCalls()).toEqual([]);
  });

  it("modified_enter_on_merge_is_ignored", async () => {
    mount();
    clock += 500;
    const merge = screen.getByRole("button", { name: /^Merge/ });
    merge.focus();
    for (const mod of ["metaKey", "shiftKey", "ctrlKey", "altKey"] as const) {
      expect(press(merge, "Enter", { [mod]: true }).defaultPrevented).toBe(true);
    }
    await settle();
    expect(decideCalls()).toEqual([]);
  });

  it("enter_on_merge_within_400ms_is_ignored", async () => {
    mount();
    const merge = screen.getByRole("button", { name: /^Merge/ });
    merge.focus();
    clock += 300;
    expect(press(merge, "Enter").defaultPrevented).toBe(true);
    await settle();
    expect(decideCalls()).toEqual([]);
  });

  it("held_space_across_hash_change_never_decides", async () => {
    mount();
    clock += 500;
    const merge = screen.getByRole("button", { name: /^Merge/ });
    merge.focus();
    // the proposal changes while Space is already held (its first keydown came before this card's arm)
    const a = core.state.approvals.get(AID) as Approval;
    const changed = { ...a, payload_hash: "77aa55cc".padEnd(64, "0") };
    core.state.approvals.set(AID, changed);
    actions.upsertApproval(changed);
    await vi.waitFor(() => expect(document.querySelector("[data-hash]")?.textContent).toBe("hash 77aa55cc"));
    press(merge, " ");
    clock += 600;
    press(merge, " ", { repeat: true });
    press(merge, " ", { repeat: true });
    const up = new KeyboardEvent("keyup", { key: " ", bubbles: true, cancelable: true });
    merge.dispatchEvent(up);
    expect(up.defaultPrevented).toBe(true);
    await settle();
    expect(decideCalls()).toEqual([]);
  });

  it("untrusted_click_never_approves", async () => {
    mount();
    clock += 5000;
    fireEvent.click(screen.getByRole("button", { name: /^Merge/ }));
    (screen.getByRole("button", { name: /^Reject/ }) as HTMLButtonElement).click();
    await settle();
    expect(decideCalls()).toEqual([]);
  });
});
