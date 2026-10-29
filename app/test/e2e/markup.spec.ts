// Plan 5 Task A2: Mises markup in the real browser engines. jsdom has no layout, so the collapse
// ("Show more" must stay clickable outside the clamp) and the visible link domain are checked here.
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

const DES = "b7c79a57cbb4";       // the scenario's designer run (shown by default)

async function open(page: Page) {
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
}
const thread = (page: Page) => page.getByRole("log", { name: "Conversation" });

test.beforeEach(async ({ page, fake }) => {
  await fake.reset("agent-ux");
  await page.setViewportSize({ width: 1440, height: 900 });
});

test("a_long_reply_collapses_and_show_more_is_clickable_and_reveals_the_end", async ({ page, fake }) => {
  await open(page);
  const lines = Array.from({ length: 30 }, (_, i) => `Line ${i + 1}: arm section ${i + 1} is **${(2 + i / 10).toFixed(1)} mm**.`);
  lines.push("FINAL-LINE: chose the ribbed arm.");
  await fake.emit({ type: "note", run_id: DES, data: {} },
    { steps: [{ kind: "message", payload: { role: "assistant", text: lines.join("\n\n") } }] });
  const msg = thread(page).locator("[data-msg=agent]").last();
  const last = msg.getByText("FINAL-LINE: chose the ribbed arm.");
  await expect(msg.locator(".mk-clamp")).toHaveCount(1);
  await expect(last).not.toBeInViewport();             // hidden behind the clamp...
  const more = msg.getByRole("button", { name: "Show more" });
  await more.scrollIntoViewIfNeeded();
  await expect(more).toBeVisible();
  // ...and the control itself is really on top (not clipped by its own clamp)
  const box = await more.boundingBox();
  const hit = await page.evaluate(([x, y]) => document.elementFromPoint(x, y)?.textContent ?? null, [box!.x + box!.width / 2, box!.y + box!.height / 2]);
  expect(hit).toBe("Show more");
  await more.click();
  await expect(msg.locator(".mk-clamp")).toHaveCount(0);
  await last.scrollIntoViewIfNeeded();
  await expect(last).toBeVisible();
  await expect(msg.getByRole("button", { name: "Show less" })).toBeVisible();
});

test("a_link_shows_its_real_domain_next_to_the_label", async ({ page, fake }) => {
  await open(page);
  await fake.emit({ type: "note", run_id: DES, data: {} },
    { steps: [{ kind: "message", payload: { role: "assistant", text: "Motor specs: [datasheet](https://parts.example.test/m2207) and [https://good.test](https://evil.test/x)." } }] });
  const msg = thread(page).locator("[data-msg=agent]").last();
  await expect(msg.locator("[data-host]").first()).toBeVisible();
  await expect(msg.locator("[data-host]").first()).toHaveText(" ↗ parts.example.test");
  await expect(msg.getByRole("link", { name: "evil.test" })).toBeVisible();   // the spoofed label is not shown
  await expect(msg).not.toContainText("good.test");
});
