// Profile panel against the fake core ("profile" scenario: setup finished, Claude Code signed in,
// Anthropic key in the Keychain, OpenAI key from the environment, Onshape keys saved, Ollama set).
// The panel opens from the app's top-bar Profile button (the display name's initials).
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

const PAGE = "/";
const API_SECRET = "sk-gem-E2E-SECRET-never-shown";
const ONSHAPE_ACCESS = "on-access-E2E-1111";
const ONSHAPE_SECRET = "on-secret-E2E-2222";

const panel = (page: Page) => page.getByRole("dialog", { name: "Profile" });
const nav = (page: Page, name: string) => panel(page).getByRole("navigation", { name: "Profile sections" }).getByRole("button", { name, exact: true });
const field = (page: Page, name: string) => panel(page).locator(`[aria-label="${name}"]`);

async function getJson(fake: { url: string; token: string }, path: string): Promise<unknown> {
  const r = await fetch(fake.url + path, { headers: { Authorization: `Bearer ${fake.token}` } });
  return r.json();
}

async function noSecretsAnywhere(page: Page, secrets: string[]) {
  const html = await page.content();
  const values = await page.locator("input").evaluateAll((els) => els.map((e) => (e as HTMLInputElement).value));
  for (const s of secrets) {
    expect(html).not.toContain(s);
    expect(values).not.toContain(s);
  }
}

/** The top-bar button (reads "H" for "Hartigan"; its accessible name is always "Profile"). */
async function openPanel(page: Page) {
  const btn = page.locator("[data-region=topbar]").getByRole("button", { name: "Profile", exact: true });
  await expect(btn).not.toHaveText("Profile");   // the name has loaded
  await btn.click();
  await expect(panel(page)).toBeVisible();
}

async function open(page: Page, section?: string) {
  await page.goto(PAGE);
  await expect(page.locator("[data-region=topbar]").getByRole("button", { name: "Profile", exact: true })).toHaveText("H");
  await openPanel(page);
  await expect(nav(page, "Models")).toBeVisible();
  if (section) await nav(page, section).click();
}

test.beforeEach(async ({ page, fake }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await fake.reset("profile");
  // a native confirm()/alert() would hang the run: removing must ask inside the panel
  page.on("dialog", (d) => { void d.dismiss(); throw new Error(`unexpected native dialog: ${d.message()}`); });
});

test("edit_name_and_material_persist_across_a_reload_and_never_send_setup_completed_at", async ({ page, fake }) => {
  await open(page);
  await expect(field(page, "Name")).toHaveValue("Hartigan");
  await field(page, "Name").fill("Hartigan D");
  await field(page, "Default material").fill("PETG");
  await panel(page).getByRole("button", { name: "Save", exact: true }).click();
  await expect(panel(page).getByText("saved", { exact: true })).toBeVisible();

  const puts = (await fake.calls()).filter((c) => c.method === "PUT" && c.path === "/profile");
  expect(puts).toHaveLength(1);
  expect(puts[0].body).toEqual({ display_name: "Hartigan D", default_material: "PETG" });
  expect(await getJson(fake, "/profile")).toMatchObject({ display_name: "Hartigan D", default_material: "PETG", units: "mm", setup_completed_at: 1790000000 });

  await page.reload();
  // the top-bar button shows the new name's initials
  await expect(page.locator("[data-region=topbar]").getByRole("button", { name: "Profile", exact: true })).toHaveText("HD");
  await openPanel(page);
  await expect(field(page, "Name")).toHaveValue("Hartigan D");
  await expect(field(page, "Default material")).toHaveValue("PETG");
});

test("agents_and_budgets_persist", async ({ page, fake }) => {
  await open(page, "Agents & budgets");
  await field(page, "Default agent").selectOption("verifier");
  // the default model comes from the shared model picker
  await panel(page).getByRole("button", { name: /^Default model/ }).click();
  await panel(page).getByRole("group", { name: "Your Claude Code" }).locator("[data-ref='claude-code/opus']").click();
  // a saved number shows as a manual choice; "Auto" is the other option
  await expect(panel(page).getByRole("radio", { name: "Choose a number" })).toBeChecked();
  await field(page, "Max parallel agents").fill("4");
  await field(page, "Onshape calls per run").fill("120");
  await panel(page).getByRole("button", { name: "Save", exact: true }).click();
  await expect(panel(page).getByText("saved", { exact: true })).toBeVisible();
  expect(await getJson(fake, "/profile")).toMatchObject({
    default_agent: "verifier", default_model: "claude-code/opus", max_parallel_agents: 4, onshape_calls_run_cap: 120,
  });

  await page.reload();
  await openPanel(page);
  await nav(page, "Agents & budgets").click();
  await expect(field(page, "Default agent")).toHaveValue("verifier");
  await expect(panel(page).getByRole("button", { name: /^Default model: Claude Code · opus/ })).toBeVisible();
  await expect(field(page, "Max parallel agents")).toHaveValue("4");
  await expect(field(page, "Onshape calls per run")).toHaveValue("120");
});

test("api_key_add_replace_remove_with_an_in_panel_confirm_and_no_secret_in_the_page", async ({ page, fake }) => {
  await open(page, "Models");
  const table = panel(page).getByRole("table", { name: "API keys" });
  await expect(table.getByRole("row", { name: /anthropic.*Keychain/ })).toBeVisible();
  await expect(table.getByRole("row", { name: /openai.*environment/ })).toBeVisible();
  await expect(table.getByRole("row", { name: /gemini.*no key/ })).toBeVisible();

  // add (gemini has none): the field is a password field, cleared after the save
  await panel(page).getByRole("button", { name: "Add gemini key" }).click();
  await expect(field(page, "gemini API key")).toHaveAttribute("type", "password");
  await field(page, "gemini API key").fill(API_SECRET);
  await panel(page).getByRole("button", { name: "Save key" }).click();
  await expect(table.getByRole("row", { name: /gemini.*Keychain/ })).toBeVisible();
  await noSecretsAnywhere(page, [API_SECRET]);
  // the core's listing is status only
  expect(JSON.stringify(await getJson(fake, "/keys"))).not.toContain(API_SECRET);

  // remove asks first, inside the panel; "Keep" changes nothing
  await panel(page).getByRole("button", { name: "Remove gemini key" }).click();
  await expect(panel(page).getByText(/Remove the gemini key from the Keychain\?/)).toBeVisible();
  await panel(page).getByRole("button", { name: "Keep" }).click();
  await expect(table.getByRole("row", { name: /gemini.*Keychain/ })).toBeVisible();
  await panel(page).getByRole("button", { name: "Remove gemini key" }).click();
  await panel(page).getByRole("button", { name: "Yes, remove" }).click();
  await expect(table.getByRole("row", { name: /gemini.*no key/ })).toBeVisible();
  const keys = (await getJson(fake, "/keys")) as { vendor: string; present: boolean }[];
  expect(keys.find((k) => k.vendor === "gemini")?.present).toBe(false);
  expect(keys.find((k) => k.vendor === "anthropic")?.present).toBe(true);
});

test("local_endpoint_test_save_and_remove", async ({ page, fake }) => {
  await open(page, "Models");
  await panel(page).getByRole("button", { name: "Test ollama" }).click();
  await expect(panel(page).getByText("answering", { exact: true })).toBeVisible();

  await field(page, "lmstudio endpoint").fill("http://localhost:1234/v1");
  await panel(page).getByRole("button", { name: "Test lmstudio" }).click();
  await expect(panel(page).getByText("no answer from http://localhost:1234/v1")).toBeVisible();
  await panel(page).getByRole("button", { name: "Save lmstudio" }).click();
  await expect.poll(async () => ((await getJson(fake, "/profile")) as { local_endpoints: Record<string, string> }).local_endpoints)
    .toEqual({ ollama: "http://localhost:11434/v1", lmstudio: "http://localhost:1234/v1" });

  await panel(page).getByRole("button", { name: "Remove ollama endpoint" }).click();
  await panel(page).getByRole("button", { name: "Yes, remove" }).click();
  await expect.poll(async () => ((await getJson(fake, "/profile")) as { local_endpoints: Record<string, string> }).local_endpoints)
    .toEqual({ lmstudio: "http://localhost:1234/v1" });
});

test("claude_code_card_signs_out_then_in_with_a_blue_waiting_light", async ({ page }) => {
  await open(page, "Models");
  const card = panel(page).getByRole("region", { name: "Your Claude Code" });
  await expect(card).toContainText("signed in");
  await expect(card).toContainText("2.1.4");
  await expect(card).toContainText("claude.ai");
  await expect(page.locator(".led-blue")).toHaveCount(0);

  await card.getByRole("button", { name: "Sign out" }).click();
  await expect(card).toContainText("signed out");
  await card.getByRole("button", { name: "Sign in" }).click();
  await expect(card.getByText("finish signing in in your browser")).toBeVisible();
  await expect(page.locator(".led-blue")).toHaveCount(1); // needs the human
  await expect(card).toContainText("signed in", { timeout: 10_000 });
  await expect(page.locator(".led-blue")).toHaveCount(0);
});

test("onshape_keys_test_usage_and_caps", async ({ page, fake }) => {
  await open(page, "Onshape");
  await expect(panel(page).getByText(/saved ●●●● from Keychain/)).toBeVisible();
  await expect(panel(page).getByText("340 of 2,500 calls this year")).toBeVisible();
  await expect(panel(page).getByRole("progressbar", { name: "Onshape calls this year" })).toHaveAttribute("aria-valuenow", "340");

  await panel(page).getByRole("button", { name: "Test", exact: true }).click();
  await expect(panel(page).getByText("connected as Hartigan Deely")).toBeVisible();
  expect((await fake.calls()).filter((c) => c.path === "/onshape/test")).toHaveLength(1);

  await panel(page).getByRole("button", { name: "Replace keys" }).click();
  await field(page, "Access key").fill(ONSHAPE_ACCESS);
  await field(page, "Secret key").fill(ONSHAPE_SECRET);
  await panel(page).getByRole("button", { name: "Save keys" }).click();
  await expect(field(page, "Access key")).toHaveCount(0);
  await noSecretsAnywhere(page, [ONSHAPE_ACCESS, ONSHAPE_SECRET]);
  expect(JSON.stringify(await getJson(fake, "/keys"))).not.toContain(ONSHAPE_SECRET);

  await field(page, "Yearly call cap").fill("4000");
  await panel(page).getByRole("button", { name: "Save caps" }).click();
  await expect(panel(page).getByText("340 of 4,000 calls this year")).toBeVisible();
  expect(await getJson(fake, "/profile")).toMatchObject({ onshape_calls_year_cap: 4000 });

  await panel(page).getByRole("button", { name: "Remove keys" }).click();
  await panel(page).getByRole("button", { name: "Yes, remove" }).click();
  await expect(panel(page).getByRole("button", { name: "Add keys" })).toBeVisible();
  const keys = (await getJson(fake, "/keys")) as { vendor: string; present: boolean }[];
  expect(keys.find((k) => k.vendor === "onshape")?.present).toBe(false);
});

test("data_about_and_run_setup_again", async ({ page }) => {
  await open(page, "Data");
  // the folder the core reports (GET /status data_dir)
  await expect(panel(page).getByTestId("data-path")).toHaveText("/Users/fake/Library/Application Support/Mises");
  await expect(panel(page).getByRole("button", { name: "Copy path" })).toBeVisible();

  await nav(page, "About").click();
  const about = panel(page).getByRole("region", { name: "About" });
  await expect(about).toContainText("0.1.0"); // core
  await expect(about).toContainText("2.1.4"); // claude

  await nav(page, "Setup").click();
  await panel(page).getByRole("button", { name: "Run setup again" }).click();
  await expect(panel(page)).toHaveCount(0);
  await expect(page.getByRole("dialog", { name: "Set up Mises" })).toBeVisible();
});

test("visual_rules_square_corners_and_blue_only_on_the_selected_section", async ({ page }) => {
  await open(page, "Models");
  const radii = await panel(page).locator("button, input, select, section").evaluateAll((els) => els.map((e) => getComputedStyle(e).borderRadius));
  expect(radii.length).toBeGreaterThan(10);
  expect(new Set(radii)).toEqual(new Set(["0px"]));
  await expect(nav(page, "Models")).toHaveAttribute("aria-current", "page");
  await expect(nav(page, "Profile")).not.toHaveAttribute("aria-current", "page");
  // inside the panel (the top bar's AGENTS light is rightly blue while a run is working)
  await expect(panel(page).locator(".led-blue")).toHaveCount(0);
});
