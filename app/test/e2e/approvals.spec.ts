// Approval safety with real browser input (trusted clicks and keys, real paste events).
// Until Task 7/10 put ApprovalCard into the console, each test mounts the real ApprovalCard
// module into the running app (same Vite module graph, so the same store, client and event
// stream as the app) over the viewer area.
import type { Page } from "@playwright/test";
import type { Approval } from "../../src/api/types";
import { expect, test, type FakeControl } from "./fixtures";

const RID = "5e1f0c2a9d3b";
const AID = "ap_merge_01";
const HASH = "3f9a1c2e7b6d5a4f3e2d1c0b9a8f7e6d5c4b3a2f1e0d9c8b7a6f5e4d3c2b1a09";
const ARM_WAIT_MS = 450;

async function mountCard(page: Page, approvalId = AID): Promise<void> {
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  await page.evaluate(async (id) => {
    const load = (p: string) => import(/* @vite-ignore */ p);
    // the exact preact module URL the app uses (one preact instance, so signals + hooks work)
    const mainSrc = await (await fetch("/src/main.tsx")).text();
    const m = /from\s*["']([^"']*\/preact\.js(?:\?[^"']*)?)["']/.exec(mainSrc);
    if (!m) throw new Error("preact import not found in /src/main.tsx");
    const preact = await load(m[1]);
    const store = await load("/src/state/store.ts");
    const { ApprovalCard } = await load("/src/ui/console/approvals/ApprovalCard.tsx");
    const { QuestionCard } = await load("/src/ui/console/approvals/QuestionCard.tsx");
    const host = document.createElement("div");
    host.id = "card-host";
    host.style.cssText = "position:fixed;left:260px;top:60px;width:420px;z-index:50;background:#111";
    const outside = document.createElement("input");
    outside.id = "outside-input";
    outside.placeholder = "outside";
    const cardRoot = document.createElement("div");
    host.append(outside, cardRoot);
    document.body.append(host);
    function Host() {
      const a = store.approvals.value.get(id);
      const label = "designer · stiffen upright bracket";
      const qs = [...store.questions.value.values()].map((q) => preact.h(QuestionCard, { key: q.question_id, question: q, runLabel: label }));
      return preact.h("div", null, a ? preact.h(ApprovalCard, { approval: a, runLabel: label }) : null, ...qs);
    }
    preact.render(preact.h(Host, null), cardRoot);
  }, approvalId);
  await expect(page.locator(`[data-approval="${approvalId}"]`)).toBeVisible();
}

const card = (page: Page) => page.locator(`[data-approval="${AID}"]`);
const decideCalls = async (fake: FakeControl) => (await fake.calls()).filter((c) => c.path.endsWith("/decide"));
/** Gives a wrongly-sent request time to show up before asserting there is none. */
const settle = (page: Page) => page.waitForTimeout(250);

const NEW_HASH = "77aa55cc".padEnd(64, "0");

/** The proposal changes in the core and the UI hears about it (approval_requested, same id). */
async function changeProposal(fake: FakeControl): Promise<void> {
  const res = await fetch(`${fake.url}/approvals?status=pending&run_id=${RID}`, { headers: { Authorization: `Bearer ${fake.token}` } });
  const shown = ((await res.json()) as Approval[])[0];
  const changed = { ...shown, payload_hash: NEW_HASH, deltas: { ...shown.deltas, summary: "merge: 3 ribs now" } };
  await fake.emit({ type: "approval_requested", run_id: RID, data: changed as unknown as Record<string, unknown> });
}

test.beforeEach(async ({ fake, page }) => {
  await fake.reset("approvals");
  await page.setViewportSize({ width: 1440, height: 900 });
});

test("full_merge_from_card", async ({ page, fake }) => {
  await mountCard(page);
  await expect(card(page)).toContainText("▲ NEEDS YOU");
  await expect(card(page)).toContainText("hash 3f9a1c2e");
  await expect(card(page)).toContainText("+4.1 g");
  await page.waitForTimeout(ARM_WAIT_MS);
  await page.getByRole("button", { name: /^Merge/ }).click();
  await expect(card(page)).toContainText(/approved by local-user \d\d:\d\d/);
  const calls = await decideCalls(fake);
  expect(calls).toHaveLength(1);
  expect(calls[0].body).toEqual({ payload_hash: HASH, approve: true });
  // the core then runs the merge and finishes the run; the card stays collapsed as approved
  await fake.emit({ type: "tool_result", run_id: RID, data: { call_id: "c3", name: "onshape_merge_request", ok: true, summary: "Merged" } }, {
    steps: [{ kind: "decision", payload: { call_id: "c3", name: "onshape_merge_request", output: { ok: true, summary: "Merged", data: {}, handles: [], warnings: [], step_kind: "decision" } } }],
  });
  await fake.emit({ type: "run_finished", run_id: RID, data: { status: "done", stop_reason: "end_turn", error: null, final_text: "merged" } });
  await expect.poll(async () => (await fake.calls()).filter((c) => c.path === "/approvals").length).toBeGreaterThan(1);
  await expect(card(page)).toHaveAttribute("data-status", "approved");
  await expect(card(page)).toContainText(/approved by local-user/);
  await expect(page.getByRole("button", { name: /^Merge/ })).toHaveCount(0);
  expect(await decideCalls(fake)).toHaveLength(1);
});

test("no_global_approve_shortcut", async ({ page, fake }) => {
  await mountCard(page);
  await page.waitForTimeout(ARM_WAIT_MS);
  await page.evaluate(() => { (document.activeElement as HTMLElement | null)?.blur(); });
  for (const k of ["m", "a", "k", "r", "Enter", "Meta+Enter", "Shift+M"]) await page.keyboard.press(k);
  await settle(page);
  expect(await decideCalls(fake)).toEqual([]);
  await expect(card(page)).toHaveAttribute("data-status", "pending");
});

test("key_m_approves_only_when_card_focused", async ({ page, fake }) => {
  await mountCard(page);
  await page.waitForTimeout(ARM_WAIT_MS);
  await card(page).focus();
  await page.keyboard.press("m");
  await expect(card(page)).toHaveAttribute("data-status", "approved");
  const calls = await decideCalls(fake);
  expect(calls).toHaveLength(1);
  expect(calls[0].body).toEqual({ payload_hash: HASH, approve: true });
});

test("key_r_rejects_and_key_k_keeps_branch", async ({ page, fake }) => {
  await mountCard(page);
  await page.waitForTimeout(ARM_WAIT_MS);
  await card(page).focus();
  await page.keyboard.press("d");
  await expect(card(page).locator("[data-diff]")).toBeVisible();
  await page.keyboard.press("k");
  await expect(card(page)).toContainText(/kept branch · rejected by local-user/);
  expect((await decideCalls(fake)).map((c) => c.body)).toEqual([{ payload_hash: HASH, approve: false }]);
});

test("key_ignored_when_focus_elsewhere", async ({ page, fake }) => {
  await mountCard(page);
  await page.waitForTimeout(ARM_WAIT_MS);
  await page.locator("#outside-input").focus();
  await page.keyboard.press("m");
  await page.keyboard.press("a");
  await settle(page);
  expect(await decideCalls(fake)).toEqual([]);
  await expect(page.locator("#outside-input")).toHaveValue("ma");
});

test("key_ignored_when_untrusted", async ({ page, fake }) => {
  await mountCard(page);
  await page.waitForTimeout(ARM_WAIT_MS);
  await card(page).focus();
  await page.evaluate(() => {
    const el = document.activeElement as HTMLElement;
    for (const key of ["m", "a", "Enter"]) el.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
  });
  await settle(page);
  expect(await decideCalls(fake)).toEqual([]);
});

test("untrusted_click_never_approves", async ({ page, fake }) => {
  await mountCard(page);
  await page.waitForTimeout(ARM_WAIT_MS);
  await page.evaluate(() => {
    const merge = [...document.querySelectorAll("[data-card] button")].find((b) => b.textContent?.startsWith("Merge")) as HTMLButtonElement;
    merge.click();
    merge.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 }));
  });
  await settle(page);
  expect(await decideCalls(fake)).toEqual([]);
});

test("key_ignored_on_repeat", async ({ page, fake }) => {
  await mountCard(page);
  // a key already held when the card appears: the first press is not armed, the rest are repeats
  await card(page).focus();
  await page.keyboard.down("m");
  await page.waitForTimeout(ARM_WAIT_MS);
  await page.keyboard.down("m");
  await page.keyboard.down("m");
  await page.keyboard.up("m");
  await settle(page);
  expect(await decideCalls(fake)).toEqual([]);
  // same for a held Enter on the focused Merge button: its repeats must not auto-click it
  await mountCard(page);
  await page.getByRole("button", { name: /^Merge/ }).focus();
  await page.keyboard.down("Enter");
  await page.waitForTimeout(ARM_WAIT_MS);
  await page.keyboard.down("Enter");
  await page.keyboard.down("Enter");
  await page.keyboard.up("Enter");
  await settle(page);
  expect(await decideCalls(fake)).toEqual([]);
});

test("key_ignored_within_400ms_of_mount", async ({ page, fake }) => {
  await mountCard(page);
  await card(page).focus();
  await page.keyboard.press("m");
  await settle(page);
  expect(await decideCalls(fake)).toEqual([]);
  // the same key once armed works, so the guard above was the arm delay
  await page.waitForTimeout(ARM_WAIT_MS);
  await page.keyboard.press("m");
  await expect(card(page)).toHaveAttribute("data-status", "approved");
  expect(await decideCalls(fake)).toHaveLength(1);
});

test("key_ignored_within_1s_after_paste", async ({ page, fake }) => {
  await mountCard(page);
  await page.waitForTimeout(ARM_WAIT_MS);
  await card(page).focus();
  await page.evaluate(() => {
    const dt = new DataTransfer();
    dt.setData("text/plain", "m");
    document.activeElement?.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true }));
  });
  await page.keyboard.press("m");
  await settle(page);
  expect(await decideCalls(fake)).toEqual([]);
  await page.waitForTimeout(1000);
  await page.keyboard.press("m");
  await expect(card(page)).toHaveAttribute("data-status", "approved");
});

test("pasting_text_into_card_never_approves", async ({ page, fake, context }) => {
  // WebKit has no clipboard permissions in Playwright: there the real ⌘V pastes an empty
  // clipboard and the dispatched ClipboardEvent below carries the "m\n"
  const clipboard = await context.grantPermissions(["clipboard-read", "clipboard-write"]).then(() => true, () => false);
  await mountCard(page);
  await page.waitForTimeout(ARM_WAIT_MS);
  if (clipboard) await page.evaluate(() => navigator.clipboard.writeText("m\n"));
  await card(page).focus();
  await page.keyboard.press("Meta+V");
  await page.keyboard.press("Control+V");
  await page.evaluate(() => {
    const dt = new DataTransfer();
    dt.setData("text/plain", "m\n");
    document.activeElement?.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
  });
  await settle(page);
  expect(await decideCalls(fake)).toEqual([]);
  await expect(card(page)).toHaveAttribute("data-status", "pending");
});

test("double_click_sends_one_request", async ({ page, fake }) => {
  await mountCard(page);
  await page.waitForTimeout(ARM_WAIT_MS);
  await page.getByRole("button", { name: /^Merge/ }).dblclick();
  await expect(card(page)).toHaveAttribute("data-status", "approved");
  await settle(page);
  expect(await decideCalls(fake)).toHaveLength(1);
});

test("stale_hash_409_refreshes_card_without_retry", async ({ page, fake }) => {
  await mountCard(page);
  // the proposal changes in the core after the card was shown (no event tells the UI)
  const res = await fetch(`${fake.url}/approvals?status=pending&run_id=${RID}`, { headers: { Authorization: `Bearer ${fake.token}` } });
  const shown = ((await res.json()) as Approval[])[0];
  await fake.emit({ type: "test_noop", run_id: RID, data: {} }, {
    approval: { ...shown, payload_hash: NEW_HASH, deltas: { ...shown.deltas, summary: "merge: 3 ribs now" } },
  });
  await page.waitForTimeout(ARM_WAIT_MS);
  await page.getByRole("button", { name: /^Merge/ }).click();
  await expect(card(page)).toContainText("proposal changed — review again");
  await expect(card(page)).toContainText("hash 77aa55cc");
  await expect(card(page)).toContainText("merge: 3 ribs now");
  await expect(card(page)).toHaveAttribute("data-status", "pending");
  await settle(page);
  const calls = await decideCalls(fake);
  expect(calls).toHaveLength(1);
  expect(calls[0].body).toEqual({ payload_hash: HASH, approve: true });
  // an explicit second click after review approves the new payload
  await page.waitForTimeout(ARM_WAIT_MS);
  await page.getByRole("button", { name: /^Merge/ }).click();
  await expect(card(page)).toHaveAttribute("data-status", "approved");
  expect((await decideCalls(fake)).map((c) => c.body)).toEqual([
    { payload_hash: HASH, approve: true }, { payload_hash: NEW_HASH, approve: true },
  ]);
});

test("expired_card_disabled", async ({ page, fake }) => {
  const res = await fetch(`${fake.url}/approvals?status=pending&run_id=${RID}`, { headers: { Authorization: `Bearer ${fake.token}` } });
  const a = ((await res.json()) as Approval[])[0];
  await fake.emit({ type: "test_noop", run_id: RID, data: {} }, { approval: { ...a, expires_at: Date.now() / 1000 - 10 } });
  await mountCard(page);
  await expect(card(page)).toHaveAttribute("data-status", "expired");
  for (const name of [/^Merge/, /^Keep branch/, /^Reject/]) await expect(page.getByRole("button", { name })).toBeDisabled();
  await page.waitForTimeout(ARM_WAIT_MS);
  await card(page).focus();
  await page.keyboard.press("m");
  await page.getByRole("button", { name: /^Merge/ }).click({ force: true });
  await settle(page);
  expect(await decideCalls(fake)).toEqual([]);
});

test("card_styles_blue_border_square_corners", async ({ page }) => {
  await mountCard(page);
  const s = await card(page).evaluate((el) => {
    const c = getComputedStyle(el);
    return { border: c.borderTopColor, width: c.borderTopWidth, radius: c.borderRadius, bg: c.backgroundColor };
  });
  expect(s).toEqual({ border: "rgb(61, 139, 255)", width: "1px", radius: "0px", bg: "rgba(61, 139, 255, 0.1)" });
  const primary = await page.getByRole("button", { name: /^Merge/ }).evaluate((el) => getComputedStyle(el).backgroundColor);
  expect(primary).toBe("rgb(61, 139, 255)");
});

// ---- fix round 1: native button activation, presses across a change, question cards

test("enter_on_focused_merge_after_arming_decides_once", async ({ page, fake }) => {
  await mountCard(page);
  await page.waitForTimeout(ARM_WAIT_MS);
  await page.getByRole("button", { name: /^Merge/ }).focus();
  await page.keyboard.press("Enter");
  await expect(card(page)).toHaveAttribute("data-status", "approved");
  await settle(page);
  expect((await decideCalls(fake)).map((c) => c.body)).toEqual([{ payload_hash: HASH, approve: true }]);
});

test("space_on_focused_merge_after_arming_decides_once", async ({ page, fake }) => {
  await mountCard(page);
  await page.waitForTimeout(ARM_WAIT_MS);
  await page.getByRole("button", { name: /^Merge/ }).focus();
  await page.keyboard.press(" ");
  await expect(card(page)).toHaveAttribute("data-status", "approved");
  await settle(page);
  expect(await decideCalls(fake)).toHaveLength(1);
});

test("paste_then_enter_on_focused_merge_is_ignored", async ({ page, fake }) => {
  await mountCard(page);
  await page.waitForTimeout(ARM_WAIT_MS);
  await page.getByRole("button", { name: /^Merge/ }).focus();
  await page.evaluate(() => {
    const dt = new DataTransfer();
    dt.setData("text/plain", "\n");
    document.activeElement?.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true }));
  });
  await page.keyboard.press("Enter");
  await page.keyboard.press(" ");
  await settle(page);
  expect(await decideCalls(fake)).toEqual([]);
});

test("modified_enter_on_focused_merge_is_ignored", async ({ page, fake }) => {
  await mountCard(page);
  await page.waitForTimeout(ARM_WAIT_MS);
  await page.getByRole("button", { name: /^Merge/ }).focus();
  for (const k of ["Meta+Enter", "Shift+Enter", "Alt+Enter", "Control+Enter", "Shift+Space"]) await page.keyboard.press(k);
  await settle(page);
  expect(await decideCalls(fake)).toEqual([]);
  await expect(card(page)).toHaveAttribute("data-status", "pending");
});

test("held_space_across_hash_change_never_decides", async ({ page, fake }) => {
  await mountCard(page);
  await page.getByRole("button", { name: /^Merge/ }).focus();
  await page.keyboard.down(" ");
  await changeProposal(fake);
  await expect(card(page)).toContainText("hash 77aa55cc");
  await page.waitForTimeout(ARM_WAIT_MS + 100);
  await page.keyboard.down(" "); // auto-repeat while held
  await page.keyboard.up(" ");
  await settle(page);
  expect(await decideCalls(fake)).toEqual([]);
  await expect(card(page)).toHaveAttribute("data-status", "pending");
});

test("mouse_press_across_hash_change_does_not_count", async ({ page, fake }) => {
  await mountCard(page);
  await page.waitForTimeout(ARM_WAIT_MS);
  const box = await page.getByRole("button", { name: /^Merge/ }).boundingBox();
  if (!box) throw new Error("no Merge button");
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await changeProposal(fake);
  await expect(card(page)).toContainText("hash 77aa55cc");
  await page.waitForTimeout(ARM_WAIT_MS + 100);
  await page.mouse.up();
  await settle(page);
  expect(await decideCalls(fake)).toEqual([]);
  // a fresh click after the change decides the new payload
  await page.getByRole("button", { name: /^Merge/ }).click();
  await expect(card(page)).toHaveAttribute("data-status", "approved");
  expect((await decideCalls(fake)).map((c) => c.body)).toEqual([{ payload_hash: NEW_HASH, approve: true }]);
});

test("question_option_needs_trusted_armed_click", async ({ page, fake }) => {
  await mountCard(page);
  await fake.emit({ type: "question_asked", run_id: RID, data: { question_id: "q_merge_now", question: "merge now?", options: ["yes", "no"] } });
  const q = page.locator('[data-question="q_merge_now"]');
  await expect(q).toBeVisible();
  await page.evaluate(() => {
    const yes = [...document.querySelectorAll('[data-question="q_merge_now"] button')].find((b) => b.textContent === "yes") as HTMLButtonElement;
    yes.click();
  });
  await settle(page);
  expect((await fake.calls()).filter((c) => c.path.endsWith("/answer"))).toEqual([]);
  await page.waitForTimeout(ARM_WAIT_MS);
  await q.getByRole("button", { name: "yes" }).click();
  await expect(q).toHaveCount(0);
  const answers = (await fake.calls()).filter((c) => c.path.endsWith("/answer"));
  expect(answers.map((c) => c.body)).toEqual([{ text: "yes" }]);
  expect(await decideCalls(fake)).toEqual([]);
});
