// The approval card's r (reject) and a (approve) letter keys, on Chromium and WebKit (the app
// runs in WKWebView). WebKit's Tab skips <button>s unless they carry an explicit tabindex (macOS
// "keyboard navigation" is off by default), so "click the card, Tab into its buttons, press r"
// used to leave the card in WebKit and the letters never armed. The card's buttons now carry
// tabIndex=0, so the same keyboard path works in both engines.
import type { Page } from "@playwright/test";
import { expect, test, type FakeControl } from "./fixtures";

const AID = "ap_merge_01";
const HASH = "3f9a1c2e7b6d5a4f3e2d1c0b9a8f7e6d5c4b3a2f1e0d9c8b7a6f5e4d3c2b1a09";
const ARM_WAIT_MS = 450;

const card = (page: Page) => page.locator(`[data-approval="${AID}"]`);
const decideCalls = async (fake: FakeControl) => (await fake.calls()).filter((c) => c.path.endsWith("/decide"));
const settle = (page: Page) => page.waitForTimeout(250);

async function mountCard(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  await expect(page.locator(`[data-region=console] [data-approval="${AID}"]`)).toBeVisible();
}

/** Presses Tab until focus is on the button whose name starts with `name` (at most 12 presses). */
async function tabTo(page: Page, name: RegExp): Promise<void> {
  const btn = page.getByRole("button", { name });
  for (let i = 0; i < 12 && !(await btn.evaluate((el) => el === document.activeElement)); i++) {
    await page.keyboard.press("Tab");
  }
  await expect(btn).toBeFocused();
}

test.beforeEach(async ({ fake, page }) => {
  await fake.reset("approvals");
  await page.setViewportSize({ width: 1440, height: 900 });
});

for (const [key, approve, status] of [["r", false, "rejected"], ["a", true, "approved"]] as const) {
  test(`key_${key}_on_focused_card_decides_once`, async ({ page, fake }) => {
    await mountCard(page);
    await card(page).focus();
    await page.waitForTimeout(ARM_WAIT_MS);
    await page.keyboard.press(key);
    await expect(card(page)).toHaveAttribute("data-status", status);
    expect((await decideCalls(fake)).map((c) => c.body)).toEqual([{ payload_hash: HASH, approve }]);
  });

  test(`click_card_then_tab_into_its_buttons_then_${key}_decides_once`, async ({ page, fake }) => {
    await mountCard(page);
    await page.waitForTimeout(ARM_WAIT_MS);
    // a click on the card's text turns the letters off; Tab moves focus into the card's own
    // buttons by keyboard, which turns them back on after ARM_MS
    await card(page).locator(".acard-what").click();
    await page.keyboard.press("Tab");
    await expect.poll(() => card(page).evaluate((el) => el.contains(document.activeElement) && el !== document.activeElement)).toBe(true);
    await page.waitForTimeout(ARM_WAIT_MS);
    await page.keyboard.press(key);
    await expect(card(page)).toHaveAttribute("data-status", status);
    await settle(page);
    expect((await decideCalls(fake)).map((c) => c.body)).toEqual([{ payload_hash: HASH, approve }]);
  });
}

test("tab_reaches_every_card_button_in_order", async ({ page, fake }) => {
  await mountCard(page);
  await card(page).focus();
  await tabTo(page, /^Merge/);
  await tabTo(page, /^Diff/);
  await tabTo(page, /^Keep branch/);
  await tabTo(page, /^Reject/);
  // focus on Reject then r: one reject
  await page.waitForTimeout(ARM_WAIT_MS);
  await page.keyboard.press("r");
  await expect(card(page)).toHaveAttribute("data-status", "rejected");
  expect((await decideCalls(fake)).map((c) => c.body)).toEqual([{ payload_hash: HASH, approve: false }]);
});
