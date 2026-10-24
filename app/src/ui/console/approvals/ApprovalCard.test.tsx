// Card behaviour against the fake core (real routes, real 409 stale_version). jsdom can't make a
// trusted click, so the click trust check is stubbed here; the untrusted-click path is tested in
// ApprovalCard.keys.test.tsx and in e2e/approvals.spec.ts with real browser input.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { startFakeCore, type FakeCore } from "../../../../test/fake-core/server";
import { CoreClient } from "../../../api/client";
import type { Approval, Step } from "../../../api/types";
import { actions, approvals, bindClient, focusedStepId, resetStore, steps } from "../../../state/store";
import { ApprovalCard } from "./ApprovalCard";
import { resetApprovalUi } from "./decide";

vi.mock("./approvalKeys", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./approvalKeys")>()),
  isTrustedActivation: () => true,
}));

const RID = "5e1f0c2a9d3b";
const AID = "ap_merge_01";
let core: FakeCore;
let realNow: () => number;

const decideCalls = () => core.state.calls.filter((c) => c.path.endsWith("/decide"));
const pending = (): Approval => core.state.approvals.get(AID) as Approval;

/** Moves the clock past the 400 ms arm delay. */
let offset = 0;
function armed() {
  offset += 10_000;
  vi.spyOn(performance, "now").mockImplementation(() => realNow() + offset);
}

/** A mouse click: pointerdown then a single click (detail 1). */
function tap(el: Element, detail = 1) {
  fireEvent.pointerDown(el);
  fireEvent.click(el, { detail });
}

function mount(a: Approval = pending(), runLabel = "designer · stiffen upright bracket") {
  actions.upsertApproval(a);
  return render(<ApprovalCard approval={a} runLabel={runLabel} />);
}

beforeEach(async () => {
  realNow = performance.now.bind(performance);
  offset = 0;
  core = await startFakeCore({ port: 0, token: "t", scenario: "approvals" });
  bindClient(new CoreClient({ baseUrl: core.url, wsUrl: "", token: "t", owned: false }));
});

afterEach(async () => {
  cleanup();
  resetStore();
  resetApprovalUi();
  await core.stop();
});

describe("ApprovalCard", () => {
  it("renders_header_summary_deltas_hash_and_buttons", () => {
    const { container } = mount();
    const card = container.querySelector("[data-card]") as HTMLElement;
    expect(card.getAttribute("role")).toBe("group");
    expect(card.getAttribute("tabindex")).toBe("0");
    expect(card.getAttribute("aria-label")).toBe("Needs you: onshape_merge_request");
    expect(card.textContent).toContain("▲ NEEDS YOU");
    expect(card.textContent).toContain("designer · stiffen upright bracket");
    expect(card.textContent).toContain("merge agent/designer into main: 2 ribs on the upright");
    expect(card.textContent).toContain("+4.1 g");
    expect(card.textContent).toContain("+3200 mm³");
    expect(card.textContent).toContain("hash 3f9a1c2e");
    expect(card.textContent).toContain("mv 9a1b2c3d");
    expect(screen.getByRole("button", { name: /^Merge/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^Keep branch/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^Reject/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^Diff/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^Merge/ }).className).toContain("btn-primary");
  });

  it("non_merge_action_shows_approve_and_no_keep_branch", () => {
    mount({ ...pending(), action: "fixture_merge", deltas: {}, payload: { part_id: "mount_plate" } });
    expect(screen.getByRole("button", { name: /^Approve/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Keep branch/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Merge/ })).toBeNull();
    // no agent summary: nothing claims to be one; the payload fields are on the face
    expect(document.querySelector("[data-agent-says]")).toBeNull();
    expect(document.querySelector("[data-payload-fields]")?.textContent).toBe("part_id: mount_plate");
  });

  it("merge_card_face_shows_branch_to_target_and_agent_summary_as_unverified", () => {
    mount();
    const route = document.querySelector("[data-route]") as HTMLElement;
    expect(route.textContent).toBe("branch https://cad.onshape.com/documents/d1/w/w2 → target https://cad.onshape.com/documents/d1/w/w1");
    const says = document.querySelector("[data-agent-says]") as HTMLElement;
    expect(says.textContent).toBe("agent says: merge agent/designer into main: 2 ribs on the upright");
    expect(says.className).toContain("faint");
    expect(says.className).not.toMatch(/tone-(blue|green)|needs-you/);
    // the full JSON is only behind Diff
    expect(document.querySelector("[data-diff]")).toBeNull();
  });

  it("merge_route_falls_back_to_the_payload_url_the_real_core_sends", () => {
    mount({ ...pending(), payload: { url: "https://cad.onshape.com/documents/d9/w/main", summary: "s" }, deltas: { branch_url: "https://cad.onshape.com/documents/d9/w/agent" } });
    expect(document.querySelector("[data-route]")?.textContent).toBe("branch https://cad.onshape.com/documents/d9/w/agent → target https://cad.onshape.com/documents/d9/w/main");
    expect(document.querySelector("[data-agent-says]")?.textContent).toBe("agent says: s");
  });

  it("non_merge_card_lists_top_level_payload_fields_truncated", () => {
    const long = "x".repeat(300);
    mount({ ...pending(), action: "mirror_export", deltas: {}, payload: { part_id: "mount_plate", vars: { thickness: 6 }, note: long, summary: "export it" } });
    const items = [...document.querySelectorAll("[data-payload-fields] [data-field]")].map((e) => e.textContent ?? "");
    expect(items[0]).toBe("part_id: mount_plate");
    expect(items[1]).toBe('vars: {"thickness":6}');
    expect(items[2].startsWith("note: xxx")).toBe(true);
    expect(items[2].endsWith("…")).toBe(true);
    expect(items[2].length).toBeLessThan(100);
    // the summary is the agent's claim, shown once as such
    expect(items.some((t) => t.startsWith("summary"))).toBe(false);
    expect(document.querySelector("[data-agent-says]")?.textContent).toBe("agent says: export it");
  });

  it("short_hash_is_8_chars", () => {
    const { container } = mount();
    const hash = container.querySelector("[data-hash]") as HTMLElement;
    expect(hash.textContent).toBe("hash 3f9a1c2e");
    expect(hash.getAttribute("data-hash")).toHaveLength(8);
  });

  it("merge_click_sends_approve_true_with_hash", async () => {
    mount();
    armed();
    tap(screen.getByRole("button", { name: /^Merge/ }));
    await waitFor(() => expect(decideCalls()).toHaveLength(1));
    const call = decideCalls()[0];
    expect(call.path).toBe(`/approvals/${AID}/decide`);
    expect(call.body).toEqual({ payload_hash: pending().payload_hash, approve: true });
    expect(Object.keys(call.body as object).sort()).toEqual(["approve", "payload_hash"]);
    await waitFor(() => expect(document.querySelector("[data-card]")?.textContent).toMatch(/approved by local-user \d\d:\d\d/));
    expect(approvals.value.get(AID)?.status).toBe("approved");
    expect(screen.queryByRole("button", { name: /^Merge/ })).toBeNull();
  });

  it("reject_and_keep_send_approve_false", async () => {
    mount();
    armed();
    tap(screen.getByRole("button", { name: /^Keep branch/ }));
    await waitFor(() => expect(decideCalls()).toHaveLength(1));
    expect(decideCalls()[0].body).toEqual({ payload_hash: pending().payload_hash, approve: false });
    await waitFor(() => expect(document.querySelector("[data-card]")?.textContent).toMatch(/kept branch · rejected by local-user/));
    cleanup();

    core.reset("approvals");
    resetApprovalUi();
    mount(pending());
    armed();
    tap(screen.getByRole("button", { name: /^Reject/ }));
    await waitFor(() => expect(decideCalls()).toHaveLength(1));
    expect(decideCalls()[0].body).toEqual({ payload_hash: pending().payload_hash, approve: false });
    await waitFor(() => expect(document.querySelector("[data-card]")?.textContent).toMatch(/rejected by local-user/));
    expect(document.querySelector("[data-card]")?.textContent).not.toContain("kept branch");
  });

  it("diff_makes_no_request", async () => {
    mount();
    armed();
    fireEvent.click(screen.getByRole("button", { name: /^Diff/ }));
    const pre = document.querySelector("[data-diff]") as HTMLElement;
    expect(pre).not.toBeNull();
    expect(pre.textContent).toContain('"target_url": "https://cad.onshape.com/documents/d1/w/w1"');
    expect(pre.textContent).toContain('"mass_g": 4.1');
    // merge_preview names are untrusted: shown as text, never HTML
    expect(pre.textContent).toContain("<b>Part Studio</b>");
    expect(document.querySelector("[data-card] b")).toBeNull();
    expect(document.querySelector("[data-merge-preview]")?.textContent).toContain("elements: 1");
    fireEvent.click(screen.getByRole("button", { name: /^Diff/ }));
    expect(document.querySelector("[data-diff]")).toBeNull();
    await new Promise((r) => setTimeout(r, 30));
    expect(core.state.calls).toEqual([]);
  });

  it("click_within_400ms_of_mount_is_ignored", async () => {
    mount();
    tap(screen.getByRole("button", { name: /^Merge/ }));
    await new Promise((r) => setTimeout(r, 30));
    expect(decideCalls()).toEqual([]);
  });

  it("double_click_sends_one_request", async () => {
    mount();
    armed();
    const btn = screen.getByRole("button", { name: /^Merge/ });
    tap(btn);
    tap(btn);
    tap(screen.queryByRole("button", { name: /^Reject/ }) ?? btn);
    await waitFor(() => expect(approvals.value.get(AID)?.status).toBe("approved"));
    await new Promise((r) => setTimeout(r, 30));
    expect(decideCalls()).toHaveLength(1);
  });

  it("real_double_click_second_click_never_counts", async () => {
    mount();
    armed();
    const btn = screen.getByRole("button", { name: /^Merge/ });
    // first click lands while a press is still being judged; only the detail-2 click arrives
    fireEvent.pointerDown(btn);
    fireEvent.click(btn, { detail: 2 });
    await new Promise((r) => setTimeout(r, 30));
    expect(decideCalls()).toEqual([]);
  });

  it("mouse_press_started_before_hash_change_does_not_count", async () => {
    const a = pending();
    mount(a);
    armed();
    const btn = screen.getByRole("button", { name: /^Merge/ });
    fireEvent.pointerDown(btn);
    armed();
    actions.upsertApproval({ ...a, payload_hash: "abcdef01".padEnd(64, "9") });
    await waitFor(() => expect(document.querySelector("[data-hash]")?.textContent).toBe("hash abcdef01"));
    armed();
    fireEvent.click(btn, { detail: 1 });
    await new Promise((r) => setTimeout(r, 30));
    expect(decideCalls()).toEqual([]);
    // a fresh press after the change counts
    tap(btn);
    await waitFor(() => expect(decideCalls()).toHaveLength(1));
  });

  it("click_without_pointerdown_does_not_count", async () => {
    mount();
    armed();
    const btn = screen.getByRole("button", { name: /^Merge/ });
    fireEvent.click(btn, { detail: 1 }); // no press on this button
    fireEvent.pointerDown(btn);
    fireEvent.click(btn, { detail: 0 }); // keyboard-style activation
    fireEvent.pointerDown(screen.getByRole("button", { name: /^Reject/ }));
    fireEvent.click(btn, { detail: 1 }); // pressed on another button
    await new Promise((r) => setTimeout(r, 30));
    expect(decideCalls()).toEqual([]);
  });

  it("merge_card_lists_both_approve_keys", () => {
    mount();
    expect(screen.getByRole("button", { name: /^Merge/ }).getAttribute("aria-keyshortcuts")).toBe("m a");
  });

  it("buttons_disabled_while_in_flight", async () => {
    mount();
    armed();
    tap(screen.getByRole("button", { name: /^Merge/ }));
    for (const name of [/^Merge/, /^Keep branch/, /^Reject/]) {
      expect((screen.getByRole("button", { name }) as HTMLButtonElement).disabled).toBe(true);
    }
    await waitFor(() => expect(approvals.value.get(AID)?.status).toBe("approved"));
  });

  it("stale_hash_409_refreshes_card_without_retry", async () => {
    const shown = pending();
    mount(shown);
    // the proposal changes in the core after the card was shown
    const changed: Approval = { ...shown, payload: { ...shown.payload, summary: "stiffen upright, 3 ribs" }, payload_hash: "77aa55cc".padEnd(64, "0"), deltas: { ...shown.deltas, summary: "3 ribs now" } };
    core.state.approvals.set(AID, changed);
    armed();
    tap(screen.getByRole("button", { name: /^Merge/ }));
    await waitFor(() => expect(document.querySelector("[data-card]")?.textContent).toContain("proposal changed — review again"));
    const card = document.querySelector("[data-card]") as HTMLElement;
    expect(card.textContent).toContain("hash 77aa55cc");
    expect(card.textContent).toContain("3 ribs now");
    expect(approvals.value.get(AID)?.payload_hash).toBe(changed.payload_hash);
    await new Promise((r) => setTimeout(r, 50));
    expect(decideCalls()).toHaveLength(1);
    expect(core.state.calls.map((c) => `${c.method} ${c.path}`)).toEqual([`POST /approvals/${AID}/decide`, "GET /approvals"]);
    expect(core.state.calls[1].query).toEqual({ status: "pending", run_id: RID });
    expect(core.state.approvals.get(AID)?.status).toBe("pending");
    // the refreshed card is re-armed: an immediate click does nothing
    vi.spyOn(performance, "now").mockImplementation(() => realNow() + 10_000);
    tap(screen.getByRole("button", { name: /^Merge/ }));
    await new Promise((r) => setTimeout(r, 30));
    expect(decideCalls()).toHaveLength(1);
    // after review, an explicit click approves the new payload
    vi.spyOn(performance, "now").mockImplementation(() => realNow() + 20_000);
    tap(screen.getByRole("button", { name: /^Merge/ }));
    await waitFor(() => expect(decideCalls()).toHaveLength(2));
    expect(decideCalls()[1].body).toEqual({ payload_hash: changed.payload_hash, approve: true });
  });

  it("expired_card_disabled", async () => {
    mount({ ...pending(), expires_at: Date.now() / 1000 - 5 });
    armed();
    for (const name of [/^Merge/, /^Keep branch/, /^Reject/]) {
      const b = screen.getByRole("button", { name }) as HTMLButtonElement;
      expect(b.disabled).toBe(true);
      tap(b);
    }
    expect(document.querySelector("[data-card]")?.textContent).toContain("expired");
    await new Promise((r) => setTimeout(r, 30));
    expect(decideCalls()).toEqual([]);
  });

  it("shows_expiry_countdown", () => {
    mount({ ...pending(), expires_at: Date.now() / 1000 + 125 });
    expect(document.querySelector("[data-expiry]")?.textContent).toMatch(/expires in 2m 0[45]s/);
  });

  it("evidence_click_focuses_step", () => {
    const runSteps = core.state.steps.filter((s) => s.run_id === RID) as Step[];
    steps.value = new Map([[RID, runSteps]]);
    mount();
    const links = [...document.querySelectorAll("[data-evidence]")] as HTMLElement[];
    expect(links.map((l) => l.getAttribute("data-evidence"))).toEqual(["3", "5"]);
    expect(links[1].textContent).toContain("measure · Part · mount plate");
    expect(links[1].textContent).toContain("05050505");
    fireEvent.click(links[1]);
    expect(focusedStepId.value).toBe(5);
    expect(core.state.calls).toEqual([]);
  });

  it("reads_the_latest_approval_from_the_store", async () => {
    const a = pending();
    mount(a);
    actions.upsertApproval({ ...a, payload_hash: "abcdef01".padEnd(64, "9") });
    await waitFor(() => expect(document.querySelector("[data-hash]")?.textContent).toBe("hash abcdef01"));
  });

  it("untrusted_text_renders_as_text", () => {
    mount({ ...pending(), action: "<img src=x onerror=alert(1)>", deltas: { summary: "<script>x()</script>" } });
    const card = document.querySelector("[data-card]") as HTMLElement;
    expect(card.querySelector("img, script")).toBeNull();
    expect(card.textContent).toContain("<script>x()</script>");
  });

  it("already_decided_elsewhere_shows_the_server_outcome", async () => {
    core.state.approvals.set(AID, { ...pending(), status: "rejected", decided_by: "local-user", decided_at: Date.now() / 1000 });
    mount({ ...pending(), status: "pending" });
    armed();
    tap(screen.getByRole("button", { name: /^Merge/ }));
    await waitFor(() => expect(document.querySelector("[data-card]")?.textContent).toMatch(/rejected by local-user/));
    expect(decideCalls()).toHaveLength(1);
  });
});
