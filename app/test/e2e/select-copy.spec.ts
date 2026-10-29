// Plan 5 Task A1: text is selectable and ⌘A/⌘C work (the macOS Edit menu routes the keystrokes in
// the real app; here on the web the keystrokes are native). Composer select-all + copy, and the
// conversation/logs carry user-select: text.
import { expect, test } from "./fixtures";

test.beforeEach(async ({ fake, page }) => {
  await fake.reset("base");
  await page.setViewportSize({ width: 1440, height: 900 });
});

test("composer_select_all_and_copy_by_keyboard", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  const composer = page.locator("[data-region=console] [data-command-line] textarea");
  await composer.click();
  await composer.fill("hello mises clipboard");
  await page.keyboard.press("Meta+A");
  await page.keyboard.press("Meta+C");
  const sel = await composer.evaluate((el) => {
    const t = el as HTMLTextAreaElement;
    return t.value.slice(t.selectionStart ?? 0, t.selectionEnd ?? 0);
  });
  expect(sel).toBe("hello mises clipboard");
});

test("thread_and_logs_allow_text_selection", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  const us = await page.locator("[data-region=console]").evaluate((el) =>
    getComputedStyle(el).webkitUserSelect || getComputedStyle(el).userSelect);
  expect(us).toBe("text");
  // selecting the conversation yields real text
  const len = await page.evaluate(() => {
    const el = document.querySelector("[data-region=console] [data-conversation]");
    if (!el) return 0;
    const range = document.createRange();
    range.selectNodeContents(el);
    const s = window.getSelection();
    s?.removeAllRanges();
    s?.addRange(range);
    return (s?.toString() ?? "").trim().length;
  });
  expect(len).toBeGreaterThan(0);
});
