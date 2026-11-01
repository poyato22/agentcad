import { expect, test, useDetailsView } from "./fixtures";

const PLANNER = "c0ffee000001";

test.beforeEach(async ({ fake, page }) => {
  await fake.reset("command");
  await page.setViewportSize({ width: 1440, height: 900 });
  await useDetailsView(page);
});

test("one_chat_per_build_the_first_message_starts_the_lead", async ({ page, fake }) => {
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  // P10 E24: no ⌘N, no New run; the composer talks to the build's lead
  await page.keyboard.press("Meta+n");
  await expect(page.getByRole("dialog", { name: "New run" })).toHaveCount(0);
  const cmd = page.getByLabel("Command line");
  await cmd.fill("stiffen the upright");
  await cmd.press("Enter");
  await expect(cmd).toHaveValue("");
  const calls = await fake.calls();
  const chat = calls.filter((c) => c.method === "POST" && /^\/projects\/[^/]+\/chat$/.test(c.path));
  expect(chat.map((c) => c.body)).toEqual([{ text: "stiffen the upright" }]);
  // the new lead shows up in the roster, selected
  const row = page.locator("[data-roster] [data-run]", { hasText: "stiffen upright" });
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
  // M9 (flaky under load): send only once the runs are in, so @designer names the running run
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  const cmd = page.getByLabel("Command line");
  await cmd.fill("@designer hello");
  await expect(page.getByTestId("target-chip")).toContainText("designer");
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
