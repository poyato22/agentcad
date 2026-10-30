import { expect, test, useDetailsView } from "./fixtures";

const PLANNER = "c0ffee000001";

test.beforeEach(async ({ fake, page }) => {
  await fake.reset("command");
  await page.setViewportSize({ width: 1440, height: 900 });
  await useDetailsView(page);
});

test("new_run_flow", async ({ page, fake }) => {
  await page.goto("/");
  await page.locator("[data-region=console]").waitFor();
  await page.keyboard.press("Meta+n");
  const dialog = page.getByRole("dialog", { name: "New run" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Agent").selectOption("designer");
  await expect(dialog.getByRole("button", { name: /^Model: anthropic · claude-sonnet-5-5/ })).toBeVisible();
  await dialog.getByLabel("Task").fill("stiffen the upright");
  await dialog.getByRole("button", { name: "Start" }).click();
  await expect(dialog).toBeHidden();

  const calls = await fake.calls();
  const post = calls.filter((c) => c.method === "POST" && c.path === "/runs");
  expect(post).toHaveLength(1);
  expect(post[0].body).toEqual({
    agent: "designer", prompt: "stiffen the upright", model_ref: "anthropic/claude-sonnet-5-5", edit_scope: ["*"], read_scope: ["*"],
    project_id: expect.any(String),   // a new run always names its build (final review I-2)
  });

  // the new run shows up in the roster as "designer · stiffen upright", selected
  const row = page.locator("[data-roster] [data-run]", { hasText: "designer · stiffen upright" });
  await expect(row).toHaveCount(1);
  await expect(row).toHaveAttribute("aria-selected", "true");
});

test("message_routes_to_at_target", async ({ page, fake }) => {
  await page.goto("/");
  const cmd = page.getByLabel("Command line");
  await cmd.focus();
  await cmd.fill("@planner make it 6 mm");
  await expect(page.getByTestId("target-chip")).toHaveText("→ planner · plan bracket");
  await cmd.press("Enter");
  await expect(cmd).toHaveValue("");
  const msg = (await fake.calls()).filter((c) => c.method === "POST" && c.path === `/runs/${PLANNER}/message`);
  expect(msg).toHaveLength(1);
  expect(msg[0].body).toEqual({ text: "make it 6 mm" });
});

test("still_working_run_shows_core_hint", async ({ page }) => {
  await page.goto("/");
  const cmd = page.getByLabel("Command line");
  await cmd.fill("@designer hello");
  await cmd.press("Enter");
  await expect(page.getByRole("alert")).toContainText("run is still working");
});

test("stop_all_from_command_line", async ({ page, fake }) => {
  await page.goto("/");
  const cmd = page.getByLabel("Command line");
  await cmd.fill("/stopall");
  await cmd.press("Enter");
  await expect.poll(async () => (await fake.calls()).some((c) => c.path === "/runs/cancel_all")).toBe(true);
});
