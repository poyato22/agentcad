// First-run setup wizard against the fake core ("setup" scenario: nothing saved, Claude Code
// installed but signed out, Onshape keys accepted, no local model server unless a test says so).
// The app mounts <SetupGate/> at its root: the wizard covers the app while setup is incomplete.
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

const PAGE = "/";
const appShell = (page: Page) => page.locator("[data-region=app]");
const API_SECRET = "sk-ant-api03-E2E-SECRET-never-shown";
const ONSHAPE_ACCESS = "on-access-E2E-1111";
const ONSHAPE_SECRET = "on-secret-E2E-2222";

const wizard = (page: Page) => page.getByRole("dialog", { name: "Set up Mises" });
const heading = (page: Page, name: string) => page.getByRole("heading", { name });
const field = (page: Page, name: string) => page.locator(`input[aria-label="${name}"]`);

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

test.beforeEach(async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
});

test("wizard_hidden_when_setup_is_complete", async ({ page, fake }) => {
  await fake.reset("base");
  const answered = page.waitForResponse((r) => r.url().endsWith("/setup") && r.request().method() === "GET");
  await page.goto(PAGE);
  await expect(appShell(page)).toBeVisible();
  await answered;
  await expect(wizard(page)).toHaveCount(0);
});

/** The profile step (first after Welcome): name (+ material), then on to "Connect a model". */
async function fillProfile(page: Page, name = "Hartigan", material?: string) {
  await expect(heading(page, "Your profile")).toBeVisible();
  await field(page, "Display name").fill(name);
  if (material) await field(page, "Default material").fill(material);
  await page.getByRole("button", { name: "Save and continue" }).click();
}

test("full_path_profile_claude_code_api_key_onshape_done", async ({ page, fake }) => {
  await fake.reset("setup");
  await page.goto(PAGE);
  await expect(wizard(page)).toBeVisible();
  await expect(wizard(page)).toContainText("Everything stays on this Mac");
  await expect(page.locator(".setup-steps li")).toHaveText(["Welcome", "Profile", "Model", "Onshape", "Done"]);
  await page.getByRole("button", { name: "Get started" }).click();

  // Your profile first: name, units, material, theme (no parallel-agents number: that's automatic)
  await expect(heading(page, "Your profile")).toBeVisible();
  await expect(page.getByLabel("Units")).toHaveValue("mm");
  await expect(page.getByLabel("Parallel agents")).toHaveCount(0);
  await page.getByLabel("Theme").selectOption("dark");
  await fillProfile(page, "Hartigan", "PETG");

  // Connect a model: the user's own Claude Code, then an API key as well
  await expect(heading(page, "Connect a model")).toBeVisible();
  const cont = page.getByRole("button", { name: "Continue" });
  await expect(cont).toBeDisabled();
  const claude = page.getByRole("region", { name: "Use my Claude Code" });
  await expect(claude).toContainText("Runs your Claude Code");
  await claude.getByRole("button", { name: "Sign in" }).click();
  await expect(claude).toContainText("finish signing in in your browser");
  await expect(claude).toContainText("signed in · claude.ai", { timeout: 6000 });
  await expect(cont).toBeEnabled();

  const key = field(page, "API key");
  await expect(key).toHaveAttribute("type", "password");
  await page.getByLabel("Vendor").selectOption("anthropic");
  await key.fill(API_SECRET);
  await page.getByRole("region", { name: "API key" }).getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("region", { name: "API key" })).toContainText("saved ●●●● from Keychain");
  await expect(key).toHaveValue("");
  await noSecretsAnywhere(page, [API_SECRET]);
  // the default model for new runs: the same picker as everywhere (only models that work now)
  const dflt = page.getByRole("region", { name: "Default model" });
  await dflt.getByRole("button", { name: /^Default model/ }).click();
  await expect.poll(() => dflt.locator("[data-ref]:not([disabled])").evaluateAll((els) => [...new Set(els.map((e) => e.getAttribute("data-ref")))]))
    .toEqual(["claude-code/sonnet", "claude-code/opus", "claude-code/haiku", "claude-code/fable", "anthropic/claude-sonnet-5-5"]);
  await dflt.getByRole("group", { name: "Your Claude Code" }).locator("[data-ref='claude-code/sonnet']").click();
  await expect(dflt.getByRole("button", { name: /^Default model: Claude Code · sonnet/ })).toBeVisible();
  await cont.click();

  // Onshape: save both keys (password fields), then one test call
  await expect(heading(page, "Connect Onshape")).toBeVisible();
  await expect(field(page, "Access key")).toHaveAttribute("type", "password");
  await expect(field(page, "Secret key")).toHaveAttribute("type", "password");
  await field(page, "Access key").fill(ONSHAPE_ACCESS);
  await field(page, "Secret key").fill(ONSHAPE_SECRET);
  await page.getByRole("button", { name: "Save" }).click();
  await expect(wizard(page)).toContainText("saved ●●●● from Keychain");
  await expect(field(page, "Access key")).toHaveValue("");
  await expect(field(page, "Secret key")).toHaveValue("");
  await page.getByRole("button", { name: "Test" }).click();
  await expect(wizard(page)).toContainText("connected as Hartigan Deely");
  await noSecretsAnywhere(page, [API_SECRET, ONSHAPE_ACCESS, ONSHAPE_SECRET]);
  await page.getByRole("button", { name: "Continue" }).click();

  await expect(heading(page, "Ready")).toBeVisible();
  await page.getByRole("button", { name: "Start using Mises" }).click();
  await expect(wizard(page)).toHaveCount(0);

  const calls = await fake.calls();
  const n = (method: string, path: string) => calls.filter((c) => c.method === method && c.path === path).length;
  expect(n("POST", "/claude-code/login")).toBe(1);
  expect(n("POST", "/onshape/test")).toBe(1);
  expect(n("POST", "/setup/complete")).toBe(1);
  expect(await getJson(fake, "/setup")).toEqual({ complete: true, has_model: true, has_onshape: true, missing: [] });
  expect(await getJson(fake, "/profile")).toMatchObject({
    display_name: "Hartigan", default_material: "PETG", default_model: "claude-code/sonnet", theme: "dark",
  });

  // a reload does not bring it back
  const again = page.waitForResponse((r) => r.url().endsWith("/setup") && r.request().method() === "GET");
  await page.reload();
  await expect(appShell(page)).toBeVisible();
  await again;
  await expect(wizard(page)).toHaveCount(0);
});

test("reload_mid_way_resumes_at_the_first_missing_step", async ({ page, fake }) => {
  await fake.reset("setup");
  await page.goto(PAGE);
  await page.getByRole("button", { name: "Get started" }).click();
  await page.reload();
  await expect(heading(page, "Welcome to Mises")).toBeVisible();          // nothing saved yet
  await page.getByRole("button", { name: "Get started" }).click();
  await fillProfile(page);
  await page.reload();
  await expect(heading(page, "Connect a model")).toBeVisible();           // the profile is saved
  await page.getByLabel("Vendor").selectOption("openai");
  await field(page, "API key").fill("sk-openai-E2E");
  await page.getByRole("region", { name: "API key" }).getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("region", { name: "API key" })).toContainText("saved ●●●● from Keychain");

  await page.reload();
  await expect(heading(page, "Connect Onshape")).toBeVisible();
  await page.getByRole("button", { name: "Skip for now" }).click();
  await expect(heading(page, "Ready")).toBeVisible();

  await page.reload();
  await expect(heading(page, "Ready")).toBeVisible();
  // closing keeps what's saved; the next launch resumes at the same step
  await page.getByRole("button", { name: "Close — finish later" }).click();
  await expect(wizard(page)).toHaveCount(0);
  await page.reload();
  await expect(heading(page, "Ready")).toBeVisible();
});

test("local_only_and_skip_onshape_path", async ({ page, fake }) => {
  const sc = JSON.parse(JSON.stringify(await import("../fake-core/scenarios/setup.json", { with: { type: "json" } }).then((m) => m.default))) as Record<string, unknown> & { setup: Record<string, unknown> };
  sc.setup.local_answering = ["ollama"];         // Ollama runs on this Mac
  await fake.reset(sc);
  await page.goto(PAGE);
  // like the real core, an Ollama answering at its default address already counts as a model:
  // the wizard opens at the profile (always first), then the model step shows it works
  await fillProfile(page);
  await expect(heading(page, "Connect a model")).toBeVisible();
  await expect(page.getByRole("button", { name: "Continue" })).toBeEnabled();
  // Local model: the core checks the endpoint and only an answering one is saved
  await page.getByLabel("Local app").selectOption("ollama");
  await expect(field(page, "Endpoint")).toHaveValue("http://localhost:11434/v1");
  await page.getByRole("region", { name: "Local model" }).getByRole("button", { name: "Test" }).click();
  await expect(page.getByRole("region", { name: "Local model" })).toContainText("answering — saved");
  expect((await fake.calls()).filter((c) => c.path === "/local/test")).toHaveLength(1);
  expect(await getJson(fake, "/profile")).toMatchObject({ local_endpoints: { ollama: "http://localhost:11434/v1" } });
  await page.getByRole("button", { name: "Continue" }).click();

  await page.getByRole("button", { name: "Skip for now" }).click();
  await expect(wizard(page)).toContainText("Onshape skipped");
  await page.getByRole("button", { name: "Start using Mises" }).click();
  await expect(wizard(page)).toHaveCount(0);

  const calls = await fake.calls();
  expect(calls.some((c) => c.path === "/keys/onshape" || c.path === "/onshape/test")).toBe(false);
  expect(await getJson(fake, "/setup")).toMatchObject({ complete: true, has_onshape: false, missing: ["onshape"] });
});

test("drafting_look_square_corners_and_blue_only_on_the_current_step", async ({ page, fake }) => {
  await fake.reset("setup");
  await page.goto(PAGE);
  await page.getByRole("button", { name: "Get started" }).click();
  await fillProfile(page);
  await expect(heading(page, "Connect a model")).toBeVisible();
  await expect(wizard(page)).toHaveCSS("background-color", "rgb(10, 10, 10)");
  await expect(page.locator(".setup-steps li[aria-current=step]")).toHaveText("Model");
  await expect(page.locator(".setup-steps li[aria-current=step]")).toHaveCSS("border-bottom-color", "rgb(61, 139, 255)");
  for (const sel of ["button", "input", "select", ".setup-card"]) {
    const radii = await wizard(page).locator(sel).evaluateAll((els) => els.map((e) => getComputedStyle(e).borderRadius));
    expect(radii.length, sel).toBeGreaterThan(0);
    expect(new Set(radii), sel).toEqual(new Set(["0px"]));
  }
  // nothing else is blue: no blue fills or text in the wizard before anything needs the human
  const blue = await wizard(page).locator("*").evaluateAll((els) => els.filter((e) => {
    const cs = getComputedStyle(e);
    return [cs.color, cs.backgroundColor].some((c) => c === "rgb(61, 139, 255)" || c === "rgb(141, 186, 255)");
  }).length);
  expect(blue).toBe(0);
});

test("claude_code_not_installed_shows_install_hint", async ({ page, fake }) => {
  const sc = JSON.parse(JSON.stringify(await import("../fake-core/scenarios/setup.json", { with: { type: "json" } }).then((m) => m.default))) as Record<string, unknown> & { setup: Record<string, unknown> };
  sc.setup.claude = { installed: false, version: null, signed_in: false, auth_method: null };
  await fake.reset(sc);
  await page.goto(PAGE);
  await page.getByRole("button", { name: "Get started" }).click();
  await fillProfile(page);
  const claude = page.getByRole("region", { name: "Use my Claude Code" });
  await expect(claude).toContainText("isn't installed on this Mac");
  await expect(claude).toContainText("npm install -g @anthropic-ai/claude-code");
  await expect(claude.getByRole("button", { name: "Sign in" })).toHaveCount(0);
});
