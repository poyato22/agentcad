// Integration (Plan 5 wave D): the top bar carries A1/A2's activity pill beside the Status button
// (Plan 7 U2b folded the lights and A6's Onshape meter into it), profile and STOP ALL. Nothing may clip or overlap at the two
// window widths the app is designed for (1280 and 1440), in either theme. MISES_SHOTS=<dir> also
// saves a screenshot of each.
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

const SHOTS = process.env.MISES_SHOTS;

type Box = { x: number; y: number; width: number; height: number };
const right = (b: Box) => b.x + b.width;

async function boxOf(page: Page, sel: string): Promise<Box> {
  const b = await page.locator(sel).first().boundingBox();
  expect(b, `${sel} is laid out`).not.toBeNull();
  return b as Box;
}

for (const width of [1280, 1440]) {
  for (const theme of ["dark", "light"] as const) {
    test(`topbar_pill_and_budget_fit_at_${width}_${theme}`, async ({ page, fake }) => {
      await fake.reset("topbar");          // two running agents (the pill) and Onshape connected (the meter)
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/");
      await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
      await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
      // the widest pill: both running agents active, one of them thinking ("2 working · 1 thinking")
      const now = Date.now() / 1000;
      await fake.emit({ type: "agent_activity", run_id: "b7c79a57cbb4", data: { state: "thinking", detail: "", since: now } });
      await fake.emit({ type: "agent_activity", run_id: "c2d4e6f8a0b1", data: { state: "tool", detail: "measuring mount plate", since: now } });
      await expect(page.locator(".activity-pill")).toBeVisible();
      await expect(page.locator(".status-btn")).toBeVisible();
      await expect(page.locator(".budget-chip")).toBeVisible();   // Onshape connected: the live count is in the bar

      if (SHOTS) await page.locator("header.topbar").screenshot({ path: `${SHOTS}/topbar-${width}-${theme}.png` });
      const bar = await boxOf(page, "header.topbar");
      const tabs = await boxOf(page, '[role="tablist"][aria-label="mode"]');   // the mode tabs (A3's build tabs are a tablist too)
      const left = await boxOf(page, ".topbar-left");
      const r = await boxOf(page, ".topbar-right");
      // the three groups sit side by side inside the window
      expect(right(left)).toBeLessThanOrEqual(tabs.x + 0.5);
      expect(right(tabs)).toBeLessThanOrEqual(r.x + 0.5);
      expect(right(r)).toBeLessThanOrEqual(width + 0.5);
      expect(right(bar)).toBeLessThanOrEqual(width + 0.5);
      // every right-side item is fully visible and its text is not cut off
      for (const sel of [".activity-pill", ".budget-chip", ".status-btn", ".profile-btn", ".stop-all"]) {
        const items = page.locator(`.topbar-right ${sel}`);
        for (let i = 0; i < await items.count(); i++) {
          const el = items.nth(i);
          const b = await el.boundingBox();
          expect(b, `${sel} #${i} laid out`).not.toBeNull();
          expect(b!.x).toBeGreaterThanOrEqual(r.x - 0.5);
          expect(right(b!), `${sel} #${i} right edge ${right(b!)} vs group ${right(r)}`).toBeLessThanOrEqual(right(r) + 0.5);
          expect(b!.y).toBeGreaterThanOrEqual(bar.y - 0.5);
          expect(b!.y + b!.height).toBeLessThanOrEqual(bar.y + bar.height + 0.5);
          const clipped = await el.evaluate((n) => n.scrollWidth > n.clientWidth + 1);
          expect(clipped, `${sel} #${i} text is not clipped`).toBe(false);
        }
      }
      // the mode tabs all fit (⌘1–⌘7), and A3's build tabs never run under them
      for (const sel of [".btab", ".btab-add"]) {
        const els = page.locator(`.topbar-left ${sel}`);
        for (let i = 0; i < await els.count(); i++) {
          const b = await els.nth(i).boundingBox();
          if (b) expect(right(b), `${sel} #${i} stays left of the mode tabs`).toBeLessThanOrEqual(tabs.x + 0.5);
        }
      }
      const tabEls = page.getByRole("tablist", { name: "mode" }).getByRole("tab");
      for (let i = 0; i < await tabEls.count(); i++) {
        const b = await tabEls.nth(i).boundingBox();
        expect(b && right(b) <= right(tabs) + 0.5 && b.x >= tabs.x - 0.5).toBe(true);
      }
    });
  }
}
