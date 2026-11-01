// Plan 10 A1 against the fake core: Home's Agents toggles the agent manager (E1); one Settings menu
// holds the parts library, Onshape, the Trash and Profile & settings (E2); every opener closes what
// it opened (E7); back / forward through where you went — ← →, ⌘← / ⌘→ outside text fields, the
// mouse's side buttons (E11); a sub-agent's thread has "← Back to lead" and Esc (E13); the empty
// assembly's note is centred in what the floating surfaces leave (E37).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";
import { expect, homeMenu, openHome, test } from "./fixtures";

const FX = resolve(import.meta.dirname, "../../../core/tests/fixtures/ui");
const BASE = JSON.parse(readFileSync(resolve(import.meta.dirname, "../fake-core/scenarios/base.json"), "utf8")) as { runs: Record<string, unknown>[] } & Record<string, unknown>;
const BUILD = (JSON.parse(readFileSync(resolve(FX, "projects.json"), "utf8")) as { id: string }[])[0].id;
const LEAD = String(BASE.runs[0].id);
const withLead = () => ({ ...BASE, runs: [{ ...BASE.runs[0], role: "lead", project_id: BUILD, status: "running" }] });

const home = (page: Page) => page.locator('[data-testid="home"]');
const topbar = (page: Page) => page.locator("[data-region=topbar]");
async function connected(page: Page): Promise<void> {
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
}

test.describe("Home", () => {
  test.beforeEach(async ({ fake, page }) => {
    await fake.reset("base");
    await openHome(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await connected(page);
  });

  test("E1_agents_opens_the_agent_manager_and_clicking_it_again_closes_it", async ({ page }) => {
    const btn = home(page).getByRole("button", { name: /^Agents/ });
    await btn.click();
    await expect(page.getByRole("dialog", { name: "Agent manager" })).toBeVisible();
    await expect(btn).toHaveAttribute("aria-expanded", "true");
    await btn.click();
    await expect(page.getByRole("dialog", { name: "Agent manager" })).toHaveCount(0);
  });

  test("E2_one_settings_menu_and_each_item_toggles_its_view", async ({ page }) => {
    const bar = home(page).locator(".home-bar");
    for (const name of ["Onshape", "Parts library"]) await expect(bar.getByRole("button", { name, exact: true })).toHaveCount(0);
    await expect(bar.getByRole("button", { name: /^Trash/ })).toHaveCount(0);
    const settings = bar.getByRole("button", { name: "Settings", exact: true });
    await settings.click();
    const menu = page.getByRole("menu", { name: "Settings" });
    await expect(menu.locator("[role^=menuitem] .ovm-label")).toHaveText(["Parts library", "Onshape", "Trash", "Profile & settings"]);
    await settings.click();                                     // E7: the button closes its menu
    await expect(menu).toHaveCount(0);
    await homeMenu(page, "Parts library");
    await expect(home(page)).toHaveAttribute("data-home-view", "library");
    await homeMenu(page, "Parts library");                      // again: back to the grid
    await expect(home(page)).toHaveAttribute("data-home-view", "builds");
    await homeMenu(page, "Trash");
    await expect(home(page).getByRole("heading", { name: "Trash" })).toBeVisible();
    await home(page).getByRole("button", { name: /Builds/ }).click();
    await expect(home(page).getByRole("heading", { name: "Builds" })).toBeVisible();
    await homeMenu(page, "Profile & settings");
    await expect(page.getByRole("dialog", { name: "Profile" })).toBeVisible();
  });

  test("E11_back_and_forward_with_the_buttons_cmd_arrows_and_the_mouse_buttons", async ({ page }) => {
    const back = () => page.getByRole("button", { name: "Back", exact: true });
    await expect(back()).toBeDisabled();
    await home(page).getByTitle("Open Default").click();
    await expect(topbar(page)).toBeVisible();
    await page.locator("[data-part] .tree-name").first().click();       // a part alone
    await expect(page.locator("[data-region=viewer]")).toHaveAttribute("data-mode", "part");
    await topbar(page).getByRole("tab", { name: "Studies" }).click();
    await expect(page.locator(".app")).toHaveAttribute("data-page", "studies");
    // ← in the top bar: Design with the part open
    await topbar(page).getByRole("button", { name: "Back", exact: true }).click();
    await expect(page.locator(".app")).toHaveAttribute("data-page", "design");
    await expect(page.locator("[data-region=viewer]")).toHaveAttribute("data-mode", "part");
    // ⌘←: the assembly; the mouse's back button: Home
    await page.locator("body").press("Meta+ArrowLeft");
    await expect(page.locator("[data-region=viewer]")).toHaveAttribute("data-mode", "assembly");
    await page.evaluate(() => window.dispatchEvent(new MouseEvent("mouseup", { button: 3 })));
    await expect(home(page)).toBeVisible();
    // forward: the button on Home, ⌘→, and the mouse's forward button
    await home(page).getByRole("button", { name: "Forward", exact: true }).click();
    await expect(topbar(page)).toBeVisible();
    await page.locator("body").press("Meta+ArrowRight");
    await expect(page.locator("[data-region=viewer]")).toHaveAttribute("data-mode", "part");
    await page.evaluate(() => window.dispatchEvent(new MouseEvent("mouseup", { button: 4 })));
    await expect(page.locator(".app")).toHaveAttribute("data-page", "studies");
    await expect(topbar(page).getByRole("button", { name: "Forward", exact: true })).toBeDisabled();
  });

  test("E11_cmd_arrows_in_a_text_field_move_the_caret_not_the_app", async ({ page }) => {
    await home(page).getByTitle("Open Default").click();
    await expect(topbar(page)).toBeVisible();
    const line = page.getByRole("textbox", { name: "Command line" });
    await line.fill("hello");
    await line.press("Meta+ArrowLeft");
    await expect(topbar(page)).toBeVisible();                    // still in the build
    expect(await line.evaluate((el: HTMLTextAreaElement | HTMLInputElement) => el.selectionStart)).toBe(0);
  });
});

test.describe("inside a build", () => {
  test.beforeEach(async ({ fake, page }) => {
    await fake.reset(withLead() as never);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await connected(page);
  });

  test("E7_the_activity_pill_and_profile_button_close_what_they_opened", async ({ page, fake }) => {
    await fake.emit({ type: "agent_activity", run_id: LEAD, data: { state: "tool", detail: "working", since: Date.now() / 1000 } });
    const pill = topbar(page).locator(".activity-pill");
    await pill.click();
    await expect(page.getByRole("dialog", { name: "Agent manager" })).toBeVisible();
    await pill.click();
    await expect(page.getByRole("dialog", { name: "Agent manager" })).toHaveCount(0);
    const profile = topbar(page).getByRole("button", { name: "Profile", exact: true });
    await profile.click();
    await expect(page.getByRole("dialog", { name: "Profile" })).toBeVisible();
    await profile.click();
    await expect(page.getByRole("dialog", { name: "Profile" })).toHaveCount(0);
  });

  test("E7_the_part_tree_actions_button_closes_its_own_menu", async ({ page }) => {
    const more = page.locator("[data-part] .tree-more").first();
    await more.click({ force: true });                          // shown on hover; the click is real
    await expect(page.getByTestId("part-menu")).toHaveCount(1);
    await expect(more).toHaveAttribute("aria-expanded", "true");
    await more.click({ force: true });
    await expect(page.getByTestId("part-menu")).toHaveCount(0);
    await expect(more).toHaveAttribute("aria-expanded", "false");
    await more.click({ force: true });                          // and opens it again
    await expect(page.getByTestId("part-menu")).toHaveCount(1);
  });

  test("E13_a_spawned_agent_s_thread_has_back_to_lead_and_esc", async ({ page, fake }) => {
    const SUB = "sub0nav1";
    const base = BASE.runs[0];
    await fake.emit({ type: "run_started", run_id: SUB, data: {} },
      { run: { ...base, id: SUB, parent_id: LEAD, role: "sub", agent: "simulation", task: "check the beam", title: "Check the beam", project_id: BUILD, status: "running" } as never });
    await fake.emit({ type: "agent_announce", run_id: LEAD, data: { run_id: LEAD, kind: "agent_spawned", title: "Spawned simulation: Check the beam", detail: "", ref: { agent: "simulation", parent_id: LEAD, run_id: SUB } } });
    const head = page.locator("[data-conversation-head]");
    const open = () => page.locator("[data-announce=agent_spawned]").getByRole("button", { name: /Open agent/ }).click();
    await open();
    await expect(head.locator("[data-run-label]")).toBeVisible();
    await head.getByRole("button", { name: /Back to lead/ }).click();
    await expect(head.locator("[data-run-label]")).toHaveCount(0);
    await open();
    await expect(head.getByRole("button", { name: /Back to lead/ })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(head.locator("[data-run-label]")).toHaveCount(0);
    // and ← goes back to it (the history has the thread)
    await topbar(page).getByRole("button", { name: "Back", exact: true }).click();
    await expect(head.locator("[data-run-label]")).toBeVisible();
  });
});

test("E37_the_empty_assembly_note_is_centred_between_the_floating_surfaces", async ({ page, fake }) => {
  await fake.reset("base");
  await openHome(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await connected(page);
  await home(page).getByRole("button", { name: "New build" }).click();
  await page.getByRole("textbox", { name: "New build name" }).fill("Empty rig");
  await page.keyboard.press("Enter");
  const note = page.getByText("Nothing in this assembly yet", { exact: false });
  await expect(note).toBeVisible();
  // never under the dock or the parts surface, and centred in the gap between them (±8 px); the
  // insets follow a resize within a frame or two, so poll
  const check = () => expect.poll(async () => {
    const n = (await note.boundingBox())!;
    const dock = (await page.locator("[data-region=console]").boundingBox())!;
    const left = (await page.locator("[data-region=left]").boundingBox())!;
    const mid = (left.x + left.width + dock.x) / 2;
    return n.x + n.width <= dock.x && n.x >= left.x + left.width && Math.abs(n.x + n.width / 2 - mid) < 8;
  }, { timeout: 3000 }).toBe(true);
  await check();
  // the dock dragged wider: the note follows
  const edge = page.getByRole("separator", { name: "Resize the agent dock" });
  await edge.focus();
  for (let i = 0; i < 8; i++) await page.keyboard.press("ArrowLeft");
  await expect.poll(async () => (await page.locator("[data-region=console]").boundingBox())!.width).toBeGreaterThan(500);
  await check();
});
