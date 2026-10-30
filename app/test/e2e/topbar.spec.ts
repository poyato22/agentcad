import { expect, test } from "./fixtures";

test.beforeEach(async ({ fake, page }) => {
  await fake.reset("topbar");
  await page.setViewportSize({ width: 1440, height: 900 });
});

const light = (page: import("@playwright/test").Page, name: string) => page.locator(`[data-light=${name}]`);

test("active_tab_blue_underline_and_tabs_navigate", async ({ page }) => {
  await page.goto("/");
  const design = page.getByRole("tab", { name: "Design" });
  await expect(design).toHaveCSS("border-bottom-color", "rgb(61, 139, 255)");
  await expect(design).toHaveCSS("border-bottom-width", "2px");
  const studies = page.getByRole("tab", { name: "Studies" });
  await expect(studies).not.toHaveAttribute("aria-disabled", "true");
  await studies.click();
  await expect(studies).toHaveAttribute("aria-selected", "true");
  await expect(design).toHaveAttribute("aria-selected", "false");
  await expect(studies).toHaveCSS("border-bottom-color", "rgb(61, 139, 255)");
  await expect(page.locator('[data-region="page"]')).toContainText("Studies");
});

test("topbar_geometry_and_colors", async ({ page }) => {
  await page.goto("/");
  const bar = page.locator("header.topbar");
  await expect(bar).toHaveCSS("height", "44px");
  await expect(bar).toHaveCSS("background-color", "rgb(20, 20, 20)");
  await expect(bar).toHaveCSS("border-bottom-color", "rgb(51, 51, 51)");
  await expect(bar).toHaveCSS("grid-template-columns", /px \d+(\.\d+)?px \d+(\.\d+)?px/);
});

test("status_lights_reflect_core", async ({ page }) => {
  await page.goto("/");
  await expect(light(page, "core").locator(".led")).toHaveClass(/led-green/);
  // the count lives in the budget meter; the light keeps the tone, with the count in its title
  await expect(light(page, "onshape")).toContainText("connected");
  await expect(light(page, "onshape")).toHaveAttribute("title", "Onshape connected: 214 of 2,500 calls used this year");
  await expect(page.locator("[data-budget-used]")).toHaveText("214");
  await expect(light(page, "agents")).toContainText("2 agents");
  await expect(light(page, "agents")).toContainText("· 1 needs you");
  // U2b: the bar shows one Status button whose headline is what matters now
  await expect(page.getByRole("button", { name: /^Status/ })).toContainText("1 needs you");
  await expect(page.locator("#status-card")).toBeHidden();
});

test("stop_all_stops_running_runs", async ({ page, fake }) => {
  await page.goto("/");
  await expect(light(page, "agents")).toContainText("2 agents");
  await page.getByRole("button", { name: /stop all/i }).click();
  await expect(page.getByRole("status")).toHaveText("stopped 2");
  const calls = await fake.calls();
  expect(calls.filter((c) => c.method === "POST" && c.path === "/runs/cancel_all")).toHaveLength(1);
  // run_finished events moved every run to cancelled; the AGENTS light follows
  await expect(light(page, "agents")).toContainText("0 agents");
  const runs = (await (await fetch(`${fake.url}/runs`, { headers: { Authorization: `Bearer ${fake.token}` } })).json()) as { status: string }[];
  expect(runs.map((r) => r.status)).toEqual(["cancelled", "cancelled"]);
});

test("shortcut_stops_all_from_a_text_input", async ({ page, fake }) => {
  await page.goto("/");
  await expect(light(page, "agents")).toContainText("2 agents");
  await page.evaluate(() => {
    const i = document.createElement("input");
    i.id = "probe";
    document.body.append(i);
    i.focus();
  });
  await page.keyboard.press("Meta+Shift+.");
  await expect(light(page, "agents")).toContainText("0 agents");
  const calls = await fake.calls();
  expect(calls.filter((c) => c.path === "/runs/cancel_all")).toHaveLength(1);
});
