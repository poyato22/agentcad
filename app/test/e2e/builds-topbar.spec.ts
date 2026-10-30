// Plan 5 A3: the top bar with many open builds at 1280 and 1440 px, both themes. Tabs keep a
// readable width (the ones that don't fit go behind "+N"), the part crumb keeps its place, nothing
// runs under the mode tabs or past the traffic-light inset.
import { readFileSync } from "node:fs";
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

const SHOTS = process.env.A3_SHOTS ?? "";
const NAMES = ["Quad frame", "Bench rig", "Gimbal mount with a long name", "Camera cage", "Landing gear"];

function scenario(): Record<string, unknown> {
  const sc = JSON.parse(readFileSync("test/fake-core/scenarios/builds.json", "utf8")) as { builds: { builds: { id: string; name: string }[]; open: string[]; active: string } };
  const extra = NAMES.slice(2).map((name, i) => ({ id: `x${i}extrabuild0`.slice(0, 12), name }));
  sc.builds.builds = [...sc.builds.builds, ...extra];
  sc.builds.open = ["q0quadframe0", "b0benchrig00", ...extra.map((b) => b.id)];
  sc.builds.active = "q0quadframe0";
  return sc as unknown as Record<string, unknown>;
}

async function geometry(page: Page) {
  return page.evaluate(() => {
    const r = (el: Element | null) => (el ? el.getBoundingClientRect() : null);
    const tabs = [...document.querySelectorAll("[data-testid=build-tabs] .btab")].map((e) => {
      const b = e.getBoundingClientRect();
      const name = e.querySelector(".btab-name") as HTMLElement | null;
      return { text: name?.textContent ?? "", width: b.width, right: b.right, clipped: name ? name.scrollWidth > name.clientWidth + 1 : false };
    });
    return {
      tabs, crumbPart: r(document.querySelector('.crumb [data-crumb="part"]')), modeTabs: r(document.querySelector(".tabs[aria-label=mode]")),
      left: r(document.querySelector(".crumb")), more: document.querySelector(".btab-more")?.textContent ?? null, bodyW: document.body.scrollWidth,
    };
  });
}

for (const width of [1280, 1440]) {
  for (const scheme of ["dark", "light"] as const) {
    test(`topbar_${width}_${scheme}_tabs_stay_readable_and_the_crumb_keeps_its_place`, async ({ page, fake }) => {
      await fake.reset(scenario());
      await page.setViewportSize({ width, height: 800 });
      await page.emulateMedia({ colorScheme: scheme });
      await page.goto("/");
      await expect(page.getByRole("tab", { name: "Quad frame" })).toHaveAttribute("aria-selected", "true");
      await page.getByTestId("part-tree").getByRole("button", { name: "Quad arm", exact: true }).click();
      await page.waitForTimeout(200);
      const g = await geometry(page);
      expect(g.tabs.length).toBeGreaterThanOrEqual(1);
      for (const t of g.tabs) expect(t.width, t.text).toBeGreaterThanOrEqual(83.5);
      // the active tab reads in full
      expect(g.tabs.find((t) => t.text === "Quad frame")?.clipped).toBe(false);
      // every open build is a tab or counted in "+N"
      const hidden = g.more ? Number(g.more.replace("+", "")) : 0;
      expect(g.tabs.length + hidden).toBe(NAMES.length);
      if (width === 1280) expect(hidden).toBeGreaterThan(0);
      // the part crumb is whole, after the tabs, and clear of the mode tabs
      expect(g.crumbPart && g.modeTabs).toBeTruthy();
      expect(g.crumbPart!.right).toBeLessThanOrEqual(g.modeTabs!.left - 8);
      expect(g.crumbPart!.left).toBeGreaterThanOrEqual(Math.max(...g.tabs.map((t) => t.right)));
      expect(g.left!.left).toBeGreaterThanOrEqual(78);
      expect(g.bodyW).toBeLessThanOrEqual(width);
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/topbar-${width}-${scheme}.png`, clip: { x: 0, y: 0, width, height: 120 } });
      // "+N" lists the rest; picking one makes it the active, visible tab
      if (hidden) {
        await page.locator(".btab-more").click();
        await page.getByRole("menuitemradio", { name: /Landing gear/ }).click();
        await expect(page.getByRole("tab", { name: "Landing gear" })).toHaveAttribute("aria-selected", "true");
      }
    });
  }
}
