// The user's own Claude Code end to end against the fake core: first run → Sign in (Anthropic's
// flow, faked: signed in after one status poll) → finish setup → a new run on
// "Claude Code (your account) · sonnet" → its tool rows → the approval card (Gate) → done.
// The run's script is core-loop.json's (shapes from the real-core fixtures); setup is setup.json's.
import type { Page } from "@playwright/test";
import { expect, test, useDetailsView } from "./fixtures";
import { CORE_URL } from "./ports";

test.use({ launchOptions: { args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] } });

const ARM_WAIT_MS = 450;
const TASK = "make the plate 6 mm thick";
const HASH = "5c0e1a7d9b3f2e4c6a8d0b1f3e5c7a9d2b4f6e8a0c1d3e5f7a9b1c3d5e7f9a0b";

const wizard = (page: Page) => page.getByRole("dialog", { name: "Set up Mises" });
const heading = (page: Page, name: string) => page.getByRole("heading", { name, exact: true });

async function scenario(signedIn = false): Promise<Record<string, unknown>> {
  const load = async (n: string) => JSON.parse(JSON.stringify((await import(`../fake-core/scenarios/${n}.json`, { with: { type: "json" } })).default)) as Record<string, unknown>;
  const loop = await load("core-loop");
  const setup = await load("setup") as Record<string, unknown> & { setup: Record<string, unknown> };
  if (signedIn) {
    setup.setup.claude = { installed: true, version: "2.1.4", signed_in: true, auth_method: "claude.ai" };
    setup.setup.profile = { display_name: "Hartigan", setup_completed_at: 1790000000 };
  }
  // a fresh install with one mount plate: no runs yet, the first-run setup state, core-loop's run script
  return { ...loop, runs: [], models: setup.models, setup: setup.setup };
}

async function getJson(fake: { token: string }, path: string): Promise<unknown> {
  const r = await fetch(CORE_URL + path, { headers: { Authorization: `Bearer ${fake.token}` } });
  return r.json();
}

test.beforeEach(async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await useDetailsView(page);          // the roster and step-log rows below
});
test.afterEach(async ({ fake }) => { await fake.reset("base"); });

test("first_run_sign_in_then_a_claude_code_run_through_the_gate_to_done", async ({ page, fake }) => {
  await fake.reset(await scenario());
  await page.goto("/");

  // ---- first run: the wizard covers the app; connect the user's own Claude Code
  await expect(wizard(page)).toBeVisible();
  await page.getByRole("button", { name: "Start" }).click();
  // the profile comes first (it's what "signing in" to Mises sets up)
  await page.locator('input[aria-label="Display name"]').fill("Hartigan");
  await page.getByRole("button", { name: "Continue" }).click();
  const claude = page.getByRole("region", { name: "Use my Claude Code" });
  await expect(claude).toContainText("Runs your Claude Code");
  await claude.getByRole("button", { name: "Sign in" }).click();
  await expect(claude).toContainText("signed in · claude.ai", { timeout: 6000 });
  // a model works now: pick the default for new runs with the shared picker
  const dflt = page.getByRole("region", { name: "Default model" });
  await dflt.getByRole("button", { name: /^Default model/ }).click();
  await dflt.getByRole("group", { name: "Your Claude Code" }).locator("[data-ref='claude-code/sonnet']").click();
  await expect(dflt.getByRole("button", { name: /^Default model: Claude Code · sonnet/ })).toBeVisible();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Skip" }).click();            // no Onshape
  await expect(heading(page, "Done")).toBeVisible();
  await page.getByRole("button", { name: "Finish" }).click();
  await expect(wizard(page)).toHaveCount(0);
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  await expect(page.locator("[data-region=topbar]").getByRole("button", { name: "Profile", exact: true })).toHaveText("H");

  // ---- a new run on the user's Claude Code (the profile default is prefilled)
  await page.keyboard.press("Meta+n");
  const dialog = page.getByRole("dialog", { name: "New run" });
  await expect(dialog).toBeVisible();
  const chip = dialog.getByRole("button", { name: /^Model: Claude Code · sonnet/ });
  await expect(chip).toBeVisible();
  await chip.click();
  const yours = dialog.getByRole("group", { name: "Your Claude Code" });
  expect(await yours.locator("[data-ref]").evaluateAll((els) => els.map((e) => e.getAttribute("data-ref")))).toEqual(
    ["claude-code/sonnet", "claude-code/opus", "claude-code/haiku", "claude-code/fable"]);
  await chip.click();                                                           // close the picker
  await dialog.getByRole("button", { name: "Use a specific agent…" }).click();  // R8: override the router
  await dialog.getByLabel("Agent").selectOption("designer");
  await dialog.getByLabel("Task").fill(TASK);
  await dialog.getByRole("button", { name: "Start" }).click();
  await expect(dialog).toBeHidden();

  const post = (await fake.calls()).filter((c) => c.method === "POST" && c.path === "/runs");
  expect(post.map((c) => c.body)).toEqual([{ agent: "designer", prompt: TASK, model_ref: "claude-code/sonnet", edit_scope: ["*"], read_scope: ["*"], project_id: expect.any(String) }]);
  const runs = await getJson(fake, "/runs") as { id: string; provider: string; model: string; external: boolean }[];
  expect(runs).toHaveLength(1);
  const run = runs[0];
  expect([run.provider, run.model, run.external]).toEqual(["claude-code", "sonnet", true]);

  // the roster names the provider the user's way
  const row = page.locator(`[data-roster] [data-run="${run.id}"]`);
  await expect(row).toContainText("Claude Code (your account) · sonnet");
  await expect(row.locator(".led")).toHaveClass(/led-blue/);

  // ---- tool rows: every call went through the Gate (the core's steps), and they show
  await expect(page.locator('[data-row][data-key="c:c1"]')).toContainText("mirror_part_list");
  await expect(page.locator('[data-row][data-kind="diff"]')).toContainText("Part · mount plate");

  // ---- the merge needs the human: approve it on its card
  const card = page.locator(`[data-card][data-approval="ap_${run.id}"]`);
  await expect(card).toBeVisible();
  await expect(card).toContainText(`hash ${HASH.slice(0, 8)}`);
  await page.waitForTimeout(ARM_WAIT_MS);
  await card.getByRole("button", { name: /^(Merge|Approve)/ }).click();
  await expect(card).toHaveAttribute("data-status", "approved");
  const decides = (await fake.calls()).filter((c) => c.path.endsWith("/decide"));
  expect(decides.map((c) => c.body)).toEqual([{ payload_hash: HASH, approve: true }]);

  // ---- done
  await expect(row.locator(".led")).toHaveClass(/led-green/);
  await expect(row).toContainText("done");
  await expect(page.locator("[data-roster-head]")).toContainText("0 needs you");

  // the selection reached the core for mises_context_get (PUT /context)
  await expect.poll(async () => ((await getJson(fake, "/context")) as { selected_run_id: string | null }).selected_run_id).toBe(run.id);
});

test("signed_out_claude_code_cannot_start_and_points_to_profile", async ({ page, fake }) => {
  const sc = await scenario(true) as { setup: Record<string, unknown> };
  sc.setup.claude = { installed: true, version: "2.1.4", signed_in: false, auth_method: null };
  sc.setup.keys = { anthropic: "keychain" };          // a model works, so setup stays finished
  sc.setup.profile = { display_name: "Hartigan", setup_completed_at: 1790000000, default_model: "claude-code/sonnet" };
  await fake.reset(sc);
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  await expect(wizard(page)).toHaveCount(0);

  await page.keyboard.press("Meta+n");
  const dialog = page.getByRole("dialog", { name: "New run" });
  const chip = dialog.getByRole("button", { name: /^Model: Claude Code · sonnet/ });
  await chip.click();
  const opt = dialog.getByRole("group", { name: "Your Claude Code" }).locator("[data-ref='claude-code/sonnet']");
  await expect(opt).toContainText("signed out — sign in in Profile");
  await expect(opt).toBeDisabled();
  await chip.click();
  await dialog.getByLabel("Task").fill(TASK);
  await expect(dialog.getByRole("button", { name: "Start" })).toBeDisabled();
  await dialog.getByRole("button", { name: "Sign in" }).click();
  await expect(dialog).toBeHidden();
  const profile = page.getByRole("dialog", { name: "Profile" });
  await expect(profile).toBeVisible();
  await expect(profile.getByRole("region", { name: "Your Claude Code" })).toContainText("signed out");
  expect((await fake.calls()).some((c) => c.method === "POST" && c.path === "/runs")).toBe(false);
});

test("finished_setup_without_a_model_shows_the_connect_a_model_notice", async ({ page, fake }) => {
  const sc = await scenario(true) as { setup: Record<string, unknown> };
  sc.setup.claude = { installed: true, version: "2.1.4", signed_in: false, auth_method: null };
  await fake.reset(sc);
  await page.goto("/");
  const notice = page.getByRole("status", { name: "Connect a model" });
  await expect(notice).toBeVisible();
  await expect(wizard(page)).toHaveCount(0);
  await notice.getByRole("button", { name: "Connect" }).click();
  await expect(page.getByRole("dialog", { name: "Profile" }).getByRole("region", { name: "Your Claude Code" })).toBeVisible();
});
