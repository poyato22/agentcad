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

function mount(): HTMLElement {
  const a = core.state.approvals.get(AID) as Approval;
  actions.upsertApproval(a);
  render(<div><input data-testid="elsewhere" /><ApprovalCard approval={a} runLabel="designer · stiffen upright bracket" /></div>);
  return document.querySelector("[data-card]") as HTMLElement;
}

beforeEach(async () => {
  clock = 100_000;
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
  await core.stop();
});

describe("ApprovalCard keys", () => {
  it("key_m_approves_only_when_card_focused", async () => {
    const card = mount();
    clock += 500;
    card.focus();
    const e = press(card, "m");
    expect(e.defaultPrevented).toBe(true);
    await vi.waitFor(() => expect(decideCalls()).toHaveLength(1));
    expect(decideCalls()[0].body).toEqual({ payload_hash: (core.state.approvals.get(AID) as Approval).payload_hash, approve: true });
    await vi.waitFor(() => expect(approvals.value.get(AID)?.status).toBe("approved"));
  });

  it("key_r_and_k_send_approve_false_and_d_toggles_diff", async () => {
    const card = mount();
    clock += 500;
    card.focus();
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
    card.focus();
    card.dispatchEvent(new KeyboardEvent("keydown", { key: "m", bubbles: true }));
    press(card, "a", { trusted: false });
    await settle();
    expect(decideCalls()).toEqual([]);
  });

  it("key_ignored_on_repeat", async () => {
    const card = mount();
    clock += 500;
    card.focus();
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
    card.focus();
    press(card, "m", { metaKey: true });
    press(card, "m", { ctrlKey: true });
    await settle();
    expect(decideCalls()).toEqual([]);
  });

  it("key_ignored_within_400ms_of_mount", async () => {
    const card = mount();
    card.focus();
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
    card.focus();
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
    card.focus();
    const paste = new Event("paste", { bubbles: true, cancelable: true });
    Object.defineProperty(paste, "clipboardData", { value: { getData: () => "m\n", types: ["text/plain"] } });
    card.dispatchEvent(paste);
    fireEvent.input(card, { data: "m\n" });
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
