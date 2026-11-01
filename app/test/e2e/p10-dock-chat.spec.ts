// P10 A2 against the fake core: the dock's View | Simulations | Conversation side by side as
// columns (E6) that never select text while dragged (E10), the compact design focus (E23), the
// critique card at view-card scale (E18), one chat per build with Edit / Retry / Cancel (E9,
// E24), and error cards as plain wrapped text with the cause and what to do (E3). Steps and cards
// are the real core's (core/tests/fixtures/ui); the chat and queued-cancel routes are the app's
// contract for core C1, served by the fake core. MISES_SHOTS=<dir> saves screenshots.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";
import { expect, test, useDefaultDock } from "./fixtures";

const SHOTS = process.env.MISES_SHOTS;
const FX = resolve(import.meta.dirname, "../../../core/tests/fixtures/ui");
const BASE = JSON.parse(readFileSync(resolve(import.meta.dirname, "../fake-core/scenarios/base.json"), "utf8")) as { runs: Record<string, unknown>[] } & Record<string, unknown>;
const G1 = JSON.parse(readFileSync(resolve(import.meta.dirname, "../fixtures/core-ui/g1_cards.json"), "utf8")) as Record<string, Record<string, unknown>>;
const BUILD = (JSON.parse(readFileSync(resolve(FX, "projects.json"), "utf8")) as { id: string }[])[0].id;
const LEAD = String(BASE.runs[0].id);
const SSL = "Claude Code: API Error: Unable to connect to API due to SSL error (UNABLE_TO_GET_ISSUER_CERT_LOCALLY)";

const scenario = (status = "done") => ({ ...BASE, runs: [{ ...BASE.runs[0], role: "lead", project_id: BUILD, status }] });
const con = (page: Page) => page.locator("[data-region=console]");
const col = (page: Page, s: "view" | "sim" | "text") => con(page).locator(`[data-dock] [data-stream=${s}]`);
const box = (page: Page) => page.getByLabel("Command line");

async function open(page: Page, theme: "dark" | "light" = "dark", layout?: Record<string, unknown>) {
  if (layout) await page.addInitScript((l) => { try { localStorage.setItem("mises.dock.columns", JSON.stringify(l)); } catch { /* none */ } }, layout);
  await page.goto("/");
  await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  await expect(col(page, "text").getByRole("log", { name: "Conversation" })).toContainText("thicken the mount plate to 6 mm");
}

async function rect(page: Page, sel: string) {
  const b = await page.locator(sel).first().boundingBox();
  if (!b) throw new Error(`no box for ${sel}`);
  return b;
}

for (const theme of ["dark", "light"] as const) {
  test(`dock_columns_open_side_by_side_1_2_3_and_the_dock_grows_${theme}`, async ({ page, fake }) => {
    await fake.reset(scenario() as never);
    await page.setViewportSize({ width: 1440, height: 900 });
    await useDefaultDock(page);
    await open(page, theme);
    await fake.emit({ type: "agent_announce", run_id: LEAD, data: G1.view_presented });
    // the default: the Conversation alone, View and Simulations as slim tabs
    await expect(col(page, "view")).toHaveAttribute("data-collapsed", "");
    await expect(col(page, "sim")).toHaveAttribute("data-collapsed", "");
    await expect(con(page).locator("[data-dock]")).toHaveAttribute("data-cols", "1");
    const one = await rect(page, "[data-region=console]");
    expect(Math.round(one.width)).toBe(440 + 60);
    expect((await rect(page, "[data-dock] [data-stream=view]")).width).toBeLessThanOrEqual(31);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/dock-1col-${theme}.png` });

    await col(page, "view").click();
    await expect(con(page).locator("[data-dock]")).toHaveAttribute("data-cols", "2");
    const two = await rect(page, "[data-region=console]");
    // 790 wanted; beside the open Parts surface the model keeps 520 px (1440 − 264 − 520 − 24:
    // ui/shell/room), and two columns fit that, so the Parts surface stays open
    expect(Math.round(two.width)).toBe(1440 - 264 - 520 - 24);
    await expect(page.locator(".app")).toHaveAttribute("data-left", "open");
    const v = await rect(page, "[data-dock] [data-stream=view]");
    const t = await rect(page, "[data-dock] [data-stream=text]");
    expect(v.x + v.width).toBeLessThanOrEqual(t.x + 1);                       // side by side, View left
    expect(Math.abs(v.y - t.y)).toBeLessThan(1);
    expect(Math.abs(v.height - t.height)).toBeLessThan(1);                     // full height each
    await expect(col(page, "view").locator("[data-testid=view-card]")).toBeVisible();
    const inset2 = () => page.locator("[data-region=viewer]").evaluate((el) => parseFloat(getComputedStyle(el).getPropertyValue("--inset-r")));
    await expect.poll(inset2).toBeGreaterThan(two.width);
    const insetTwo = await inset2();
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/dock-2col-${theme}.png` });

    await col(page, "sim").click();
    await expect(con(page).locator("[data-dock]")).toHaveAttribute("data-cols", "3");
    const three = await rect(page, "[data-region=console]");
    // 1080 wanted; three columns don't fit beside the Parts surface, so it folds to its tab by
    // itself and the model keeps 520 px (1440 − 58 − 520 − 24)
    await expect(page.locator(".app")).toHaveAttribute("data-left", "rail");
    expect(Math.round(three.width)).toBe(1440 - 58 - 520 - 24);
    const xs = await Promise.all((["view", "sim", "text"] as const).map(async (s) => (await rect(page, `[data-dock] [data-stream=${s}]`)).x));
    expect([...xs].sort((a, b) => a - b)).toEqual(xs);
    // the 3D view centres in what is left (shell/insets follows the dock)
    // (shared with the Parts surface when both cannot fit: shell/insets caps the pair; it is
    // re-measured on the next resize observation)
    const insetR = () => page.locator("[data-region=viewer]").evaluate((el) => parseFloat(getComputedStyle(el).getPropertyValue("--inset-r")));
    await expect.poll(insetR).toBeGreaterThan(Math.max(insetTwo, three.width * 0.9));
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/dock-3col-${theme}.png` });

    // remembered
    await page.reload();
    await expect(con(page).locator("[data-dock]")).toHaveAttribute("data-cols", "3");
  });
}

test("dragging_the_dock_edge_or_a_divider_never_selects_text", async ({ page, fake }) => {
  await fake.reset(scenario() as never);
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page, "dark", { collapsed: { view: false, sim: true, text: false } });
  const edge = await rect(page, "[data-dock] .dock-edge");
  const x = edge.x + edge.width / 2;
  const y = edge.y + 300;
  await page.mouse.move(x, y);
  await page.mouse.down();
  // sweep across the conversation's text and back: a plain drag here would select it
  await page.mouse.move(x + 120, y + 40, { steps: 6 });
  await page.mouse.move(x - 200, y + 80, { steps: 8 });
  expect(await page.evaluate(() => document.body.classList.contains("dock-resizing"))).toBe(true);
  await page.mouse.up();
  expect(await page.evaluate(() => window.getSelection()?.toString() ?? "")).toBe("");
  expect(await page.evaluate(() => document.body.classList.contains("dock-resizing"))).toBe(false);
  const div = await rect(page, '[data-divider="view|text"]');
  await page.mouse.move(div.x + 3, div.y + 200);
  await page.mouse.down();
  await page.mouse.move(div.x + 250, div.y + 260, { steps: 8 });
  await page.mouse.up();
  expect(await page.evaluate(() => window.getSelection()?.toString() ?? "")).toBe("");
  const share = await page.evaluate(() => JSON.parse(localStorage.getItem("mises.dock.columns") ?? "{}").share);
  expect(share.view).toBeGreaterThan(share.text);
});

test("design_focus_fits_the_conversation_header_at_360_px", async ({ page, fake }) => {
  await fake.reset({ ...scenario(), runs: [{ ...scenario().runs[0], design_class: { cls: "design", source: "classifier", score: 0.8 } }] } as never);
  await page.setViewportSize({ width: 1280, height: 800 });
  for (const width of [360, 440, 560]) {
    await open(page, "dark", { collapsed: { view: true, sim: true, text: false }, widths: [width, 760, 1080] });
    const head = col(page, "text").locator(".dock-h").first();
    const chip = head.locator("[data-design-focus]");
    await expect(chip).toBeVisible();
    // the header's right end is the pinned collapse button (the ⋯ menu is gone, E22 / E24)
    const parts = [await head.locator(".dock-t").boundingBox(), await chip.boundingBox(),
      await page.getByRole("button", { name: "Collapse the agent console" }).boundingBox()];
    for (const p of parts) expect(p).not.toBeNull();
    const [title, focus, fold] = parts as { x: number; width: number; y: number; height: number }[];
    expect(title.x + title.width).toBeLessThanOrEqual(focus.x + 0.5);          // no overlap, in order
    expect(focus.x + focus.width).toBeLessThanOrEqual(fold.x + 0.5);
    expect(focus.height).toBeLessThan(30);                                       // one line
    if (SHOTS && width === 360) await head.screenshot({ path: `${SHOTS}/design-focus-360.png` });
  }
});

test("the_critique_card_fills_the_view_column_like_a_view_card", async ({ page, fake }) => {
  await fake.reset(scenario() as never);
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page, "dark", { collapsed: { view: false, sim: true, text: false } });
  await fake.emit({ type: "agent_announce", run_id: LEAD, data: G1.view_presented });
  await expect(col(page, "view").locator("[data-testid=view-card]")).toBeVisible();
  const viewH = (await col(page, "view").locator("[data-testid=view-card]").boundingBox())?.height ?? 0;
  await fake.emit({ type: "agent_announce", run_id: LEAD, data: G1.critique_round });
  const crit = col(page, "view").locator("[data-view-main] [data-testid=critique-card]");
  await expect(crit).toHaveAttribute("data-large", "");
  const critH = (await crit.boundingBox())?.height ?? 0;
  expect(critH).toBeGreaterThan(viewH * 0.85);
  const stage = (await crit.locator("[data-crit-stage]").boundingBox())?.height ?? 0;
  expect(stage).toBeGreaterThan(critH * 0.6);
  if (SHOTS) await col(page, "view").screenshot({ path: `${SHOTS}/critique-view-column.png` });
});

for (const theme of ["dark", "light"] as const) {
  test(`an_error_card_is_plain_wrapped_text_with_the_cause_and_retry_${theme}`, async ({ page, fake }) => {
    await fake.reset(scenario("failed") as never);
    await page.setViewportSize({ width: 1440, height: 900 });
    await open(page, theme);
    await fake.emit({ type: "note", run_id: LEAD, data: { text: "" } }, {
      steps: [
        { run_id: LEAD, kind: "message", ts: Date.now() / 1000, payload: { role: "user", text: "add a wrist joint" } },
        { run_id: LEAD, kind: "message", ts: Date.now() / 1000 + 1, payload: { role: "assistant", text: `API Error: ${SSL}`, stop_reason: "end_turn" } },
        { run_id: LEAD, kind: "error", ts: Date.now() / 1000 + 2, payload: { error: "tool_failed", message: SSL, source: "claude_code" } },
      ],
    });
    const card = col(page, "text").locator("[data-error]").last();
    await expect(card).toHaveAttribute("data-error-kind", "tls");
    await expect(card.locator("[data-error-message]")).toHaveText(SSL);
    await expect(card.locator("[data-error-hint]")).toContainText("NODE_EXTRA_CA_CERTS");
    await expect(col(page, "text").locator("[data-plain-error]")).toContainText("UNABLE_TO_GET_ISSUER_CERT_LOCALLY");
    // never one character per line: the message is as wide as the card
    const msg = await card.locator("[data-error-message]").boundingBox();
    const whole = await card.boundingBox();
    expect(msg?.width ?? 0).toBeGreaterThan((whole?.width ?? 0) * 0.7);
    expect(msg?.height ?? 999).toBeLessThan(80);
    if (SHOTS) await card.screenshot({ path: `${SHOTS}/error-card-${theme}.png` });
    await card.getByRole("button", { name: "Retry" }).click();
    await expect.poll(async () => (await fake.calls()).filter((c) => c.method === "POST" && c.path === `/projects/${BUILD}/chat`).map((c) => c.body))
      .toEqual([{ text: "add a wrist joint" }]);
    // the way out without a terminal: Profile & settings → Claude Code → Network
    await card.getByRole("button", { name: "Network settings" }).click();
    const prof = page.getByRole("dialog", { name: "Profile" });
    await expect(prof.getByRole("button", { name: "Claude Code", exact: true })).toHaveAttribute("aria-current", "page");
    await expect(prof.getByRole("region", { name: "Network" }).getByLabel("Extra CA certificates")).toBeVisible();
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/network-settings-${theme}.png` });
  });
}

test("one_chat_per_build_edit_resends_and_a_queued_message_can_be_cancelled", async ({ page, fake }) => {
  await fake.reset(scenario("running") as never);
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page);
  // no New run anywhere; ⌘N does nothing
  await expect(page.getByRole("button", { name: "Conversation options" })).toHaveCount(0);
  await page.keyboard.press("Meta+n");
  await expect(page.getByRole("dialog", { name: "New run" })).toHaveCount(0);
  // the lead works: the message waits for it, with Cancel
  await box(page).fill("and a lid");
  await box(page).press("Enter");
  const queued = col(page, "text").locator("[data-queued-message]").last();
  await expect(queued).toHaveAttribute("data-queued-status", "queued");
  await queued.getByRole("button", { name: "Cancel this queued message" }).click();
  await expect(queued).toHaveAttribute("data-queued-status", "dropped");
  await expect(queued.locator("[data-queued-chip]")).toHaveText("cancelled");
  const calls = await fake.calls();
  expect(calls.filter((c) => c.method === "POST" && c.path === `/projects/${BUILD}/chat`).map((c) => c.body)).toEqual([{ text: "and a lid" }]);
  expect(calls.filter((c) => c.method === "POST" && /\/queued\/[^/]+\/cancel$/.test(c.path))).toHaveLength(1);
  // Edit: the words back in the composer, sent as a new turn; the old one is marked edited
  const first = col(page, "text").locator("[data-msg=user][data-kind=message]").first();
  await first.hover();
  await first.getByRole("button", { name: "Edit and resend" }).click();
  await expect(box(page)).toHaveValue("thicken the mount plate to 6 mm");
  await expect(page.getByTestId("editing-chip")).toBeVisible();
  await box(page).fill("thicken the mount plate to 7 mm");
  await box(page).press("Enter");
  await expect(first).toHaveAttribute("data-edited", "");
  await expect.poll(async () => (await fake.calls()).filter((c) => c.method === "POST" && c.path === `/projects/${BUILD}/chat`).length).toBe(2);
  if (SHOTS) await col(page, "text").screenshot({ path: `${SHOTS}/chat-edit-cancel.png` });
});
