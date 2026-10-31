// I7: every interactive control is reachable by Tab — in WebKit too. The app runs in WKWebView,
// which (with macOS's keyboard-navigation setting off, as in Playwright WebKit) only tabs to text
// fields and elements with an explicit tabindex; lib/tabReach and the Button component give every
// control one. This walks the Tab order of the main screens and checks the controls the final
// review found unreachable. Runs in both engines.
import type { Page } from "@playwright/test";
import { expect, openHome, openPartView, test } from "./fixtures";

/** Press Tab until focus comes back round (or `max` presses), collecting each stop's name. */
async function tabStops(page: Page, max = 400): Promise<string[]> {
  const seen: string[] = [];
  let first: string | null = null;
  for (let i = 0; i < max; i++) {
    await page.keyboard.press("Tab");
    const id = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      if (!el || el === document.body) return null;
      if (!el.dataset.tabProbe) el.dataset.tabProbe = String(Math.random());
      const text = (el.textContent ?? "").trim().replace(/\s+/g, " ").slice(0, 60);
      const name = el.getAttribute("aria-label") ?? (text || el.getAttribute("title") || "");
      return `${el.dataset.tabProbe}|${name}`;
    });
    if (id === null) continue;
    if (id === first) break;
    first ??= id;
    seen.push(id.slice(id.indexOf("|") + 1));
  }
  return seen;
}

const has = (stops: string[], want: RegExp) => stops.some((s) => want.test(s));

test.beforeEach(async ({ fake, page }) => {
  await fake.reset("base");
  await page.setViewportSize({ width: 1440, height: 900 });
});

test("design: Tab reaches Home, the part list, View, All parts, the console header and the command line", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  // Plan 9: the build view's pill, then a part opened alone (View, All parts, ← Assembly)
  const pill = await tabStops(page);
  for (const want of [/^X-ray$/]) expect(has(pill, want), `${want} in ${JSON.stringify(pill)}`).toBe(true);
  await openPartView(page);
  await page.locator("[data-region=viewer]").click({ position: { x: 300, y: 400 } });
  const stops = await tabStops(page);
  for (const want of [/^Home$/, /^View options$/, /^All parts$/, /Assembly/, /^Command line$/, /^New$/, /^Manage$/, /mount plate/, /^Add a build$/]) {
    expect(has(stops, want), `${want} in ${JSON.stringify(stops)}`).toBe(true);
  }
});

test("home: Tab reaches search, sort, Agents, Trash, New build and the build tiles", async ({ page }) => {
  await openHome(page);
  await page.goto("/");
  await expect(page.locator('[data-testid="home"]')).toBeVisible();
  const stops = await tabStops(page);
  for (const want of [/^Search builds$/, /^Sort builds$/, /^Agents/, /Show trashed builds|^Trash/, /New build/, /^◇?Default/]) {
    expect(has(stops, want), `${want} in ${JSON.stringify(stops)}`).toBe(true);
  }
});

test("agents panel: Tab reaches its rows' menus and Clean up", async ({ page, fake }) => {
  await fake.reset("agent-ux");
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  await page.getByRole("button", { name: "Manage", exact: true }).focus();
  await page.keyboard.press("Enter");
  const manager = page.getByRole("dialog", { name: "Agent manager" });
  await expect(manager).toBeVisible();
  await expect(manager.locator("[data-agent-row]").first()).toBeVisible();
  const stops = await tabStops(page, 120);
  for (const want of [/^More for /, /Clean up finished/, /Close/]) {
    expect(has(stops, want), `${want} in ${JSON.stringify(stops)}`).toBe(true);
  }
});
