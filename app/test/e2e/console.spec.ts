// Agent console (Task 7) against the fake core: live text, folding, filters, follow, volume.
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

const HERE = dirname(fileURLToPath(import.meta.url));
const SCENARIO = JSON.parse(readFileSync(resolve(HERE, "../fake-core/scenarios/console.json"), "utf8")) as Record<string, unknown>;
const DESIGNER = "b7c79a57cbb4";
const CHECKER = "c4e1a9f0d2b3";

const log = (page: Page) => page.locator("[data-log-scroll]");
const rows = (page: Page, kind?: string) => page.locator(kind ? `[data-row][data-kind="${kind}"]` : "[data-row]");
const chip = (page: Page, name: RegExp | string) => page.locator("[data-filters] button", { hasText: name });

async function scrollInfo(page: Page) {
  return log(page).evaluate((el) => ({ top: el.scrollTop, height: el.scrollHeight, client: el.clientHeight }));
}

function assistant(text: string, ts: number) {
  return { run_id: DESIGNER, kind: "message" as const, ts, payload: { role: "assistant", text, stop_reason: "end_turn" } };
}

test.beforeEach(async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
});

test("live_text_streams_then_settles", async ({ fake, page }) => {
  await fake.reset("console");
  await page.goto("/");
  await expect(rows(page, "message").first()).toContainText("thicken the mount plate to 6 mm");
  await fake.emit({ type: "text_delta", run_id: DESIGNER, data: { text: "Checking the " } });
  await expect(page.locator("[data-live]")).toContainText("Checking the");
  await fake.emit({ type: "text_delta", run_id: DESIGNER, data: { text: "holes next." } });
  await expect(page.locator("[data-live]")).toContainText("Checking the holes next.");
  // the assistant step lands (with its tool call): the live line becomes a message row, once
  await fake.emit({ type: "tool_call", run_id: DESIGNER, data: { call_id: "c5", name: "mirror_measure", input: { part_id: "mount_plate" } } }, {
    steps: [
      { run_id: DESIGNER, kind: "message", payload: { role: "assistant", stop_reason: "tool_use", text: "Checking the holes next.", tool_calls: [{ id: "c5", name: "mirror_measure" }] } },
      { run_id: DESIGNER, kind: "tool_call", payload: { call_id: "c5", name: "mirror_measure", input: { part_id: "mount_plate" } } },
    ],
  });
  await expect(page.locator('[data-row][data-key="c:c5"]')).toBeVisible();
  await expect(page.locator("[data-live]")).toHaveCount(0);
  await expect(rows(page, "message").filter({ hasText: "Checking the holes next." })).toHaveCount(1);
  await expect(page.getByText("Checking the holes next.")).toHaveCount(1);
});

test("fold_expand_collapse", async ({ fake, page }) => {
  await fake.reset("console");
  await page.goto("/");
  await page.locator(`[data-run="${CHECKER}"]`).click();
  const fold = page.getByRole("button", { name: "▸ 5 more tool calls · mirror_measure" });
  await expect(fold).toBeVisible();
  await expect(rows(page, "call")).toHaveCount(2);          // first measure + the failed set_vars
  await expect(page.locator('[data-row][data-key="c:m7"]')).toContainText("✕");
  await fold.click();
  await expect(rows(page, "call")).toHaveCount(7);
  await page.getByRole("button", { name: "▾ fold tool calls" }).click();
  await expect(rows(page, "call")).toHaveCount(2);
  await expect(fold).toBeVisible();
});

test("filters_switch", async ({ fake, page }) => {
  await fake.reset("console");
  await page.goto("/");
  await expect(chip(page, /^errors/)).toHaveText("errors 0");
  await page.locator(`[data-run="${CHECKER}"]`).click();
  await expect(chip(page, /^errors/)).toHaveText("errors 1");
  await chip(page, /^errors/).click();
  await expect(rows(page)).toHaveCount(1);
  await expect(rows(page).first()).toContainText("wall 0.4 mm is below the 0.8 mm minimum");
  await chip(page, "tool calls").click();
  await expect(rows(page, "message")).toHaveCount(0);
  await expect(rows(page, "fold")).toHaveCount(1);
  await chip(page, "tool calls").click();                    // back to everything
  await expect(rows(page, "message")).toHaveCount(2);
  await chip(page, /^all$/).click();
  // both runs interleaved, each row prefixed with its run label; the approval card sits after the designer's last step
  await expect(rows(page, "message").filter({ hasText: "designer · " })).toHaveCount(2);
  await expect(rows(page, "message").filter({ hasText: "checker · " })).toHaveCount(2);
  await expect(page.locator("[data-row] [data-card]")).toHaveCount(1);
  const last = await rows(page).last().getAttribute("data-kind");
  expect(last).toBe("approval");
});

test("follow_does_not_jump_when_scrolled_up", async ({ fake, page }) => {
  await fake.reset("console");
  await page.goto("/");
  await expect(rows(page, "message").first()).toBeVisible();
  const t0 = 1792869500;
  await fake.emit({ type: "note", run_id: DESIGNER, data: { text: "" } }, {
    steps: Array.from({ length: 60 }, (_, i) => assistant(`progress line ${i}`, t0 + i)),
  });
  await expect(page.getByText("progress line 59")).toBeVisible();
  let s = await scrollInfo(page);
  expect(s.height).toBeGreaterThan(s.client);                 // the log really scrolls
  expect(s.top + s.client).toBeGreaterThanOrEqual(s.height - 4); // following: at the bottom
  // the user scrolls up
  await log(page).hover();
  await page.mouse.wheel(0, -400);
  await expect(chip(page, "⤓ follow")).toHaveAttribute("aria-pressed", "false");
  const before = (await scrollInfo(page)).top;
  await fake.emit({ type: "note", run_id: DESIGNER, data: { text: "" } }, {
    steps: Array.from({ length: 5 }, (_, i) => assistant(`late line ${i}`, t0 + 100 + i)),
  });
  await expect(page.getByRole("button", { name: "5 new ↓" })).toBeVisible();
  s = await scrollInfo(page);
  expect(s.top).toBe(before);                                  // no jump
  await page.getByRole("button", { name: "5 new ↓" }).click();
  await expect(page.getByText("late line 4")).toBeInViewport();
  await expect(chip(page, "⤓ follow")).toHaveAttribute("aria-pressed", "true");
  s = await scrollInfo(page);
  expect(s.top + s.client).toBeGreaterThanOrEqual(s.height - 4);
});

test("volume_500_tool_calls_in_2s_one_render_per_frame", async ({ fake, page }) => {
  const runId = "b0b5e7a1c9d4";
  const t0 = 1792869600;
  const script: Record<string, unknown>[] = [];
  for (let i = 0; i < 500; i++) {
    const call = { call_id: `v${i}`, name: "mirror_measure", input: { part_id: "mount_plate" } };
    script.push({ at_ms: 200 + i * 4, event: { type: "tool_call", run_id: runId, data: call }, steps: [{ run_id: runId, kind: "tool_call", ts: t0 + i, payload: call }] });
    script.push({
      at_ms: 202 + i * 4, event: { type: "tool_result", run_id: runId, data: { call_id: `v${i}`, name: "mirror_measure", ok: true, summary: "ok" } },
      steps: [{ run_id: runId, kind: "measure", ts: t0 + i + 0.5, payload: { call_id: `v${i}`, name: "mirror_measure", output: { ok: true, summary: "ok", data: {}, handles: [], warnings: [], step_kind: "measure" } } }],
    });
  }
  const runs = [...(SCENARIO.runs as Record<string, unknown>[]), {
    id: runId, parent_id: null, agent: "checker", provider: "anthropic", model: "claude-haiku", task: "measure every hole",
    status: "running", branch: null, onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0, created_at: t0, updated_at: t0,
  }];
  await fake.reset({ ...SCENARIO, runs, script });
  await page.addInitScript(() => {
    const w = window as unknown as { __frames: number };
    w.__frames = 0;
    const tick = () => { w.__frames++; requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
  });
  await page.goto("/");
  await expect(page.locator(`[data-run="${runId}"][aria-selected="true"]`)).toBeVisible();
  const start = await page.evaluate(() => ({
    frames: (window as unknown as { __frames: number }).__frames,
    renders: (window as unknown as { __misesConsole: { stepLog: number } }).__misesConsole.stepLog,
  }));
  await expect(page.getByRole("button", { name: /^▸ 499 more tool calls · mirror_measure$/ })).toBeVisible({ timeout: 15_000 });
  const end = await page.evaluate(() => ({
    frames: (window as unknown as { __frames: number }).__frames,
    renders: (window as unknown as { __misesConsole: { stepLog: number } }).__misesConsole.stepLog,
    rows: document.querySelectorAll("[data-row]").length,
  }));
  const frames = end.frames - start.frames;
  const renders = end.renders - start.renders;
  expect(frames).toBeGreaterThan(60);                          // the burst really spanned many frames
  expect(renders).toBeGreaterThan(0);
  expect(renders).toBeLessThanOrEqual(frames + 1);
  expect(end.rows).toBeLessThanOrEqual(501);
  expect(end.rows).toBe(2);                                    // first call + the fold
});
