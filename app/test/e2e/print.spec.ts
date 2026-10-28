// Print page against the fake core: base scenario + a PrusaLink printer. Slicing finishes after
// 300 ms; "Send to printer" raises the print_send approval card in the console thread and nothing
// is "sent" until a trusted click approves it. Both themes for the main flow.
import type { Page } from "@playwright/test";
import { loadScenario } from "../fake-core/server";
import type { PrintScenario } from "../fake-core/printing";
import { expect, test, type FakeControl } from "./fixtures";

const KEY = "pl-E2E-SECRET-key-never-shown";
const ARM_WAIT_MS = 450;
const MK4 = { id: "bench_mk4", kind: "prusalink", host: "192.168.1.20", name: "Bench MK4", model: "MK4" };

const scenario = (print: PrintScenario) => ({ ...loadScenario("base"), print: { slice_ms: 300, card_ms: 50, ...print } }) as unknown as Record<string, unknown>;
const pageRegion = (page: Page) => page.locator("[data-region=page]");
const sendBtn = (page: Page) => pageRegion(page).getByRole("button", { name: "Send to printer" });
const printCard = (page: Page) => page.locator("[data-region=console] [data-card][aria-label='Needs you: print_send']");
const calls = async (fake: FakeControl, method: string, path: string) => (await fake.calls()).filter((c) => c.method === method && c.path === path);

async function openPrint(page: Page, theme: "dark" | "light" = "dark") {
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
  await page.locator("[data-region=topbar]").getByRole("tab", { name: "Print" }).click();
}

async function slice(page: Page) {
  await expect(pageRegion(page).getByRole("status", { name: "Printer status" })).toContainText("idle");
  await pageRegion(page).getByRole("button", { name: "Slice", exact: true }).click();
  await expect(pageRegion(page).getByRole("region", { name: "Slice result" })).toContainText("1d 2h 3m 22s");
}

test.beforeEach(async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  page.on("dialog", (d) => { void d.dismiss(); throw new Error(`unexpected native dialog: ${d.message()}`); });
});

for (const theme of ["dark", "light"] as const) {
  test(`slice_then_send_needs_the_card_in_the_console_${theme}`, async ({ page, fake }) => {
    await fake.reset(scenario({ printers: [MK4] }));
    await openPrint(page, theme);
    const light = pageRegion(page).getByRole("status", { name: "Printer status" });
    await expect(light).toContainText("idle");
    await expect(light.locator("[data-tone]")).toHaveAttribute("data-tone", "green");
    await slice(page);
    const result = pageRegion(page).getByRole("region", { name: "Slice result" });
    await expect(result).toContainText("7.51 g");
    await expect(result).toContainText("$0.17");
    await expect(result).toContainText("PLA");
    const preview = result.getByRole("img", { name: /Preview/ });
    await expect.poll(() => preview.evaluate((i) => (i as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
    expect((await calls(fake, "POST", "/slices"))[0].body).toEqual({ part_id: "mount_plate", printer_id: "bench_mk4", material: "PLA" });

    // collapse the console first: sending must open it on the card
    await page.getByRole("button", { name: "Collapse the agent console" }).click();
    await sendBtn(page).click();
    await expect(pageRegion(page).getByRole("status", { name: "Print request" })).toContainText("Approve or reject the card in the console");
    await expect(page.locator("[data-region=app]")).toHaveAttribute("data-console", "open");
    await expect(printCard(page)).toBeVisible();
    await expect(printCard(page).locator("[data-print-facts]")).toContainText("Bench MK4 · MK4 · sliced for MK4 · 192.168.1.20 · IDLE");
    await expect(printCard(page).locator("[data-print-facts]")).toContainText("1d 2h 3m 22s");
    await expect(sendBtn(page)).toBeDisabled(); // one request at a time
    expect((await calls(fake, "POST", "/prints"))[0].body).toEqual({ slice_id: expect.stringMatching(/^slice_/), printer_id: "bench_mk4" });

    // nothing happens until the human approves on the card
    await page.waitForTimeout(ARM_WAIT_MS);
    await expect(pageRegion(page).getByRole("status", { name: "Print request" })).toContainText("Approve or reject the card");
    await printCard(page).getByRole("button", { name: /^Approve/ }).click();
    await expect(pageRegion(page).getByRole("status", { name: "Print request" })).toContainText("sent mises_mount_plate.gcode to Bench MK4");
    await expect(light).toContainText("printing", { timeout: 10_000 });
    await expect(light.locator("[data-tone]")).toHaveAttribute("data-tone", "amber");
  });
}

test("never_sends_to_a_printer_that_reports_an_error", async ({ page, fake }) => {
  await fake.reset(scenario({ printers: [MK4], states: { bench_mk4: { state: "ERROR" } } }));
  await openPrint(page);
  const light = pageRegion(page).getByRole("status", { name: "Printer status" });
  await expect(light).toContainText("error");
  await expect(light.locator("[data-tone]")).toHaveAttribute("data-tone", "red");
  await pageRegion(page).getByRole("button", { name: "Slice", exact: true }).click();
  await expect(pageRegion(page).getByRole("region", { name: "Slice result" })).toContainText("1d 2h 3m 22s");
  await expect(sendBtn(page)).toBeDisabled();
  await expect(pageRegion(page)).toContainText("reports ERROR");
  await sendBtn(page).click({ force: true });
  await page.waitForTimeout(250);
  expect(await calls(fake, "POST", "/prints")).toHaveLength(0);
  expect(await printCard(page).count()).toBe(0);
});

test("rejecting_the_card_sends_nothing", async ({ page, fake }) => {
  await fake.reset(scenario({ printers: [MK4] }));
  await openPrint(page);
  await slice(page);
  await sendBtn(page).click();
  await expect(printCard(page)).toBeVisible();
  await page.waitForTimeout(ARM_WAIT_MS);
  await printCard(page).getByRole("button", { name: /^Reject/ }).click();
  await expect(pageRegion(page).getByRole("status", { name: "Print request" })).toContainText("Not sent: approval rejected");
  await expect(pageRegion(page).getByRole("status", { name: "Printer status" })).toContainText("idle");
});

test("slicer_missing_shows_the_install_command", async ({ page, fake }) => {
  await fake.reset(scenario({ printers: [MK4], slicer_installed: false }));
  await openPrint(page);
  const card = pageRegion(page).getByRole("region", { name: "Install PrusaSlicer" });
  await expect(card).toContainText("brew install --cask prusaslicer");
  await expect(pageRegion(page).getByRole("button", { name: "Slice", exact: true })).toBeDisabled();
});

test("a_slice_can_be_cancelled", async ({ page, fake }) => {
  await fake.reset(scenario({ printers: [MK4], slice_ms: 60_000 }));
  await openPrint(page);
  await expect(pageRegion(page).getByRole("status", { name: "Printer status" })).toContainText("idle");
  await pageRegion(page).getByRole("button", { name: "Slice", exact: true }).click();
  await pageRegion(page).getByRole("button", { name: "Cancel slice" }).click();
  await expect(pageRegion(page)).toContainText("Slice cancelled.");
  expect((await fake.calls()).some((c) => c.method === "POST" && /^\/slices\/slice_[0-9a-f]+\/cancel$/.test(c.path))).toBe(true);
});

test("add_a_printer_the_key_goes_out_once_and_is_never_shown", async ({ page, fake }) => {
  await fake.reset(scenario({}));
  await openPrint(page);
  const form = pageRegion(page).getByRole("form", { name: "Add a printer" });
  await expect(form).toContainText("PrusaLink is plain HTTP");
  await expect(form.getByLabel("API key")).toHaveAttribute("type", "password");
  await expect(form.getByRole("combobox", { name: "Kind" }).locator("option[value=bambu]")).toHaveJSProperty("disabled", true);
  await form.getByLabel("Name").fill("Bench MK4");
  await form.getByLabel("Host").fill("192.168.1.20");
  await form.getByLabel("API key").fill(KEY);
  await form.getByRole("button", { name: "Add printer" }).click();
  await expect(pageRegion(page).getByRole("status", { name: "Printer status" })).toContainText("idle");
  const posts = await calls(fake, "POST", "/printers");
  expect(posts).toHaveLength(1);
  expect((posts[0].body as { api_key: string }).api_key).toBe(KEY);
  expect(posts[0].query).toEqual({});
  expect(await page.content()).not.toContain(KEY);
  const values = await page.locator("input").evaluateAll((els) => els.map((e) => (e as HTMLInputElement).value));
  expect(values).not.toContain(KEY);
});

test("profile_printers_section_lists_and_removes", async ({ page, fake }) => {
  await fake.reset(scenario({ printers: [MK4] }));
  await page.goto("/");
  const btn = page.locator("[data-region=topbar]").getByRole("button", { name: "Profile", exact: true });
  await expect(btn).not.toHaveText("Profile");
  await btn.click();
  const panel = page.getByRole("dialog", { name: "Profile" });
  await panel.getByRole("navigation", { name: "Profile sections" }).getByRole("button", { name: "Printers" }).click();
  const row = panel.getByRole("row", { name: /Bench MK4/ });
  await expect(row).toContainText("192.168.1.20");
  await expect(row).toContainText("idle");
  await row.getByRole("button", { name: "Remove" }).click();
  await row.getByRole("button", { name: "Remove" }).click();
  await expect(panel.getByText("No printers.")).toBeVisible();
  expect(await calls(fake, "DELETE", "/printers/bench_mk4")).toHaveLength(1);
});
