// U8 (R20): the part list must not lag. A 100-part build, real mouse clicks, and the browser's
// Event Timing entries (input → next paint) for each one: select a row, toggle its eye, open its
// appearance swatch and its menu. Plus a burst of part_changed events (each refetches the list),
// timed by the long tasks it causes. Target: every click under 50 ms (the first use of each, which
// compiles the viewer's shaders, is reported apart).
import { readFileSync } from "node:fs";
import type { Page } from "@playwright/test";
import type { PartSummary } from "../../src/api/types";
import { expect, test } from "./fixtures";

const N = 100;
const TARGET_MS = 50;
const THROTTLE = Number(process.env.MISES_PERF_THROTTLE ?? 1);

function bigScenario(): Record<string, unknown> {
  const base = JSON.parse(readFileSync("test/fake-core/scenarios/base.json", "utf8")) as { parts: PartSummary[] } & Record<string, unknown>;
  const proto = base.parts[0];
  const parts: PartSummary[] = Array.from({ length: N }, (_, i) => ({
    ...structuredClone(proto), part_id: `p${String(i).padStart(3, "0")}`, label: `part ${String(i).padStart(3, "0")}`,
  }));
  return { ...base, parts };
}

/** Starts collecting Event Timing entries (every pointer / click event, any duration). */
async function watch(page: Page): Promise<void> {
  await page.evaluate(() => {
    const w = window as unknown as { __ev: { name: string; duration: number; processing: number }[]; __lt: number[] };
    w.__ev = []; w.__lt = [];
    new PerformanceObserver((l) => {
      for (const e of l.getEntries() as PerformanceEventTiming[]) {
        w.__ev.push({ name: e.name, duration: e.duration, processing: e.processingEnd - e.processingStart });
      }
    }).observe({ type: "event", buffered: false, durationThreshold: 16 } as PerformanceObserverInit);
    new PerformanceObserver((l) => { for (const e of l.getEntries()) w.__lt.push(e.duration); }).observe({ type: "longtask" });
  });
}

/** The slowest click-ish event (pointerdown / pointerup / click) since the last call, in ms; 0
 *  when none took 16 ms or more (Event Timing only reports those). */
async function take(page: Page): Promise<number> {
  // let the paint after the click land, so its entry is reported
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 50)))));
  return page.evaluate(() => {
    const w = window as unknown as { __ev: { name: string; duration: number }[] };
    const d = Math.max(0, ...w.__ev.filter((e) => /pointer|click|mouse|key/.test(e.name)).map((e) => e.duration));
    w.__ev = [];
    return d;
  });
}

test("parts_list_clicks_stay_under_50ms_with_100_parts", async ({ page, fake }) => {
  await fake.reset(bigScenario());
  await page.goto("/");
  const tree = page.getByTestId("part-tree");
  await expect(tree.locator("[data-part]")).toHaveCount(N);
  // MISES_PERF_THROTTLE=4: a 4x slower CPU, closer to the webview on a busy laptop (the numbers in the U8 report)
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: THROTTLE });
  await watch(page);
  // first use, reported apart: the viewer's first model load compiles its shaders on that frame
  const first: Record<string, number> = {};
  const warm = tree.locator(`[data-part="p001"] > .tree-row`);
  await warm.getByRole("button", { name: "p001", exact: true }).click();
  first.select = await take(page);
  await warm.locator(".tree-swatch").click();
  first.swatch = await take(page);
  await page.keyboard.press("Escape");
  await warm.click({ button: "right" });
  first.menu = await take(page);
  await page.keyboard.press("Escape");
  await take(page);
  const results: Record<string, number[]> = { select: [], eye: [], swatch: [], menu: [], rightclick: [] };
  for (const i of [10, 50, 90, 30, 70]) {
    const id = `p${String(i).padStart(3, "0")}`;
    const row = tree.locator(`[data-part="${id}"] > .tree-row`);
    await row.getByRole("button", { name: id, exact: true }).click();
    results.select.push(await take(page));
    await row.locator(".tree-eye").click();
    results.eye.push(await take(page));
    await row.locator(".tree-eye").click();
    await take(page);
    await row.locator(".tree-swatch").click();
    results.swatch.push(await take(page));
    await page.keyboard.press("Escape");
    await take(page);
    await row.locator(".tree-more").click();
    results.menu.push(await take(page));
    await page.keyboard.press("Escape");
    await take(page);
    await row.click({ button: "right", position: { x: 60, y: 10 } });
    results.rightclick.push(await take(page));
    await page.keyboard.press("Escape");
    await take(page);
  }
  // an agent busy on the build: 10 part_changed events, each refetching the list
  await page.evaluate(() => { (window as unknown as { __lt: number[] }).__lt = []; });
  for (let k = 0; k < 10; k++) {
    await fake.emit({ type: "part_changed", run_id: null, data: { part_id: `p${String(k).padStart(3, "0")}`, version: 2 + k, by: "agent", run_id: null, summary: "set wall" } });
  }
  await page.waitForTimeout(1500);
  const storm = await page.evaluate(() => (window as unknown as { __lt: number[] }).__lt);
  const worst = Object.fromEntries(Object.entries(results).map(([k, v]) => [k, Math.max(...v)]));
  const report = { throttle: THROTTLE, first_use_ms: first, worst_ms: worst, all_ms: results, storm_longtasks_ms: storm, storm_total_ms: storm.reduce((a, b) => a + b, 0) };
  console.log(`PARTS_PERF ${JSON.stringify(report)}`);
  for (const [k, v] of Object.entries(worst)) expect(v, `${k} click`).toBeLessThan(TARGET_MS);
});
