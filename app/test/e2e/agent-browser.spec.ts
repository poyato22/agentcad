// Plan 7 U4a (R3/R4/R5): the agent Browser tab, the part card, the per-build parts list (BOM)
// and the Profile Browsing editor, against the fake core's "parts-list" scenario: the base run
// plus the real core's dumped snapshots, part cards and parts list (scenarios/make_parts_list.py).
import type { Page } from "@playwright/test";
import { consoleMenu, expect, test } from "./fixtures";

test.beforeEach(async ({ fake, page }) => {
  await fake.reset("parts-list");
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
});

test("the parts list shows one total per currency, estimates and the unpriced count, edits and exports", async ({ page, fake }) => {
  await page.getByTestId("bom-summary").getByRole("button", { name: "Open" }).click();   // Plan 9 D9: the left surface's BOM section
  await expect(page.locator("[data-bom-page]")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Made in-house" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Bought" })).toBeVisible();
  await expect(page.getByText("Arm bracket")).toBeVisible();
  await expect(page.locator('[data-bom-line="auto_arm_bracket"]').getByText("estimate")).toBeVisible();
  // FW-app M8: the build's own part is a made row by itself, like the core (its mass x PLA $/kg)
  await expect(page.locator('[data-bom-line="auto_mount_plate"]').getByText("estimate")).toBeVisible();
  // a formula-like name from the core is plain text
  await expect(page.getByText('=HYPERLINK("https://evil.example")')).toBeVisible();
  // one total per currency, never summed
  const totals = page.locator("[data-bom-totals]");
  await expect(totals.locator('[data-currency="USD"]')).toContainText("$36.10");     // 35.97 + the mount plate's 0.13
  await expect(totals.locator('[data-currency="EUR"]')).toContainText("€2.40");
  await expect(totals.locator('[data-currency="unknown"]')).toContainText("3.00 (currency unknown)");
  await expect(totals.getByText("1 part has no price yet, so it isn't in the totals.")).toBeVisible();

  // editing a quantity persists via PATCH (only the core's fields)
  const qty = page.getByLabel("Quantity of Micro servo SG92R");
  await qty.fill("8");
  await qty.press("Enter");
  await expect.poll(async () => (await fake.calls()).find((c) => c.method === "PATCH" && /\/bom\//.test(c.path))?.body).toEqual({ qty: 8 });
  await expect(totals.locator('[data-currency="USD"]')).toContainText("$48.00");    // 8 × 5.95 + 0.27 + 0.13, re-read from the core

  // adding a bought part with no currency keeps it unknown
  await page.getByRole("button", { name: "Add bought part" }).click();
  await page.getByLabel("Part name", { exact: true }).fill("M3 nut");
  await page.getByLabel("Unit cost", { exact: true }).fill("0.10");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByText("M3 nut")).toBeVisible();
  await expect(totals.locator('[data-currency="unknown"]')).toContainText("3.10 (currency unknown)");

  await page.getByRole("button", { name: "Export CSV" }).click();
  await expect.poll(async () => (await fake.calls()).some((c) => /\/bom\.csv$/.test(c.path))).toBe(true);

  await expect(page.getByRole("button", { name: "Back to design" })).toHaveCount(0);   // FW-app M8: a tab, no back button
  await page.getByRole("tab", { name: "Design" }).click();
  await expect(page.locator('[data-region="viewer"]')).toBeVisible();
});

test("the part card loads from the core and is display-only (the core inserts and lists it)", async ({ page, fake }) => {
  const card = page.locator('[data-part-card][data-state="ready"]');
  await expect(card).toBeVisible();
  await expect(card).toContainText("Micro servo - TowerPro SG92R");
  await expect(card).toContainText("$5.95");
  // C1: the All-parts overview once shared .pcard-* classes and squeezed the card's info column to
  // 0 px while the text stayed in the DOM. Assert the layout, not just the text.
  const infoW = await card.locator(".pcard-info").evaluate((el) => el.getBoundingClientRect().width);
  expect(infoW).toBeGreaterThan(100);
  for (const sel of [".pcard-name", ".pcard-price"]) {
    const box = await card.locator(sel).first().boundingBox();
    expect(box?.width ?? 0, sel).toBeGreaterThan(0);
    await expect(card.locator(sel).first()).toBeVisible();
  }
  const imgW = await card.locator(".pcard-img").first().evaluate((el) => el.getBoundingClientRect().width);
  expect(imgW).toBeLessThanOrEqual(64);
  await expect(card.getByRole("link", { name: "Open · www.adafruit.com" })).toBeVisible();
  // P10 E15: no "Add to parts list" / "Insert" on the card; the app never adds it itself
  await expect(card.getByRole("button")).toHaveCount(0);
  expect((await fake.calls()).some((c) => c.method === "POST" && (/\/bom$/.test(c.path) || /\/insert$/.test(c.path)))).toBe(false);
});

test("the agent Browser tab shows the search, the page as escaped text, and the blocked page", async ({ page }) => {
  await consoleMenu(page, "Agent browser");
  const panel = page.locator("[data-browser-panel]");
  const product = panel.locator('[data-kind="page"]:not([data-blocked])');
  await expect(product.locator(".brz-title")).toHaveText("Micro servo - TowerPro SG92R : ID 169 : Adafruit");
  await expect(product.locator(".brz-host")).toHaveText("www.adafruit.com");
  await expect(product.getByText(/Ignore all previous/)).toBeVisible();          // shown verbatim, never obeyed
  await expect(product.locator("table").getByText("Weight")).toBeVisible();
  await expect(product.locator('[data-proxy-img="loaded"]')).toHaveCount(2);      // bytes through the core proxy
  await expect(product.getByRole("link", { name: "Open in browser" })).toHaveAttribute("href", "https://www.adafruit.com/product/169");
  await expect(panel.locator('[data-kind="search"]')).toContainText("Search · Adafruit");
  await expect(panel.locator('[data-kind="search"] li')).toHaveCount(3);
  await expect(panel.locator("[data-blocked]")).toContainText("site blocked automated access");
  await page.getByRole("button", { name: "Conversation" }).click();
  await expect(page.locator("[data-browser-panel]")).toHaveCount(0);
});

async function openProfile(page: Page) {
  const btn = page.locator("[data-region=topbar]").getByRole("button", { name: "Profile", exact: true });
  await expect(btn).not.toHaveText("Profile");
  await btn.click();
  await expect(page.getByRole("dialog", { name: "Profile" })).toBeVisible();
}

test("the Browsing profile section keeps every default when adding a site, and edits the fetch limit", async ({ page, fake }) => {
  await openProfile(page);
  const dialog = page.getByRole("dialog", { name: "Profile" });
  await dialog.getByRole("button", { name: "Browsing", exact: true }).click();
  await expect(dialog.getByText("Texas Instruments")).toBeVisible();
  await dialog.getByLabel("Site name").fill("Bot Shop");
  await dialog.getByLabel("Site domain").fill("botshop.io");
  await dialog.getByLabel("Search URL").fill("https://www.botshop.io/search?q={q}");
  await dialog.getByRole("button", { name: "Add site" }).click();
  await expect(dialog.getByText("botshop.io")).toBeVisible();
  const put = (await fake.calls()).find((c) => c.method === "PUT" && c.path === "/profile");
  const sites = (put?.body as { suggested_sites: { domain: string; search_url: string | null }[] }).suggested_sites;
  expect(sites).toHaveLength(19);
  expect(sites.filter((s) => s.search_url === null).length).toBe(6);          // the datasheet hosts survive
  const fetches = dialog.getByRole("textbox", { name: "Browser fetches per run" });
  await fetches.fill("120");
  await fetches.locator("xpath=ancestor::section[1]").getByRole("button", { name: "Save" }).click();
  await expect.poll(async () => (await fake.calls()).some((c) => c.method === "PUT" && (c.body as { browser_fetches_per_run?: number }).browser_fetches_per_run === 120)).toBe(true);
});
