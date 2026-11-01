// P11 F7 against the fake core: images in the composer (📎 / drop / paste), shrunk in the browser,
// shown as thumbnails, sent with the message (POST /projects/{id}/chat {text, images}) and shown
// in the thread from the build's store; a technical drawing offers the drawing import.
// MISES_SHOTS=<dir> saves screenshots.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";
import { expect, test, useDefaultDock } from "./fixtures";

const SHOTS = process.env.MISES_SHOTS;
const FX = resolve(import.meta.dirname, "../../../core/tests/fixtures/ui");
const BASE = JSON.parse(readFileSync(resolve(import.meta.dirname, "../fake-core/scenarios/base.json"), "utf8")) as { runs: Record<string, unknown>[] } & Record<string, unknown>;
const BUILD = (JSON.parse(readFileSync(resolve(FX, "projects.json"), "utf8")) as { id: string }[])[0].id;
const RENDER = readFileSync(resolve(import.meta.dirname, "../fake-core/scenarios/renders/view_case_iso.png"));
const DRAWING = readFileSync(resolve(import.meta.dirname, "../../../core/tests/fixtures/drawings/plate_drawing.png"));

const scenario = () => ({ ...BASE, runs: [{ ...BASE.runs[0], role: "lead", project_id: BUILD, status: "done" }] });
const con = (page: Page) => page.locator("[data-region=console]");

async function open(page: Page, theme: "dark" | "light") {
  await useDefaultDock(page);
  await page.goto("/");
  await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  await expect(con(page).getByRole("log", { name: "Conversation" })).toBeVisible();
}

for (const theme of ["dark", "light"] as const) {
  test(`an_attached_image_goes_with_the_message_and_shows_in_the_thread_${theme}`, async ({ page, fake }) => {
    await fake.reset(scenario() as never);
    await page.setViewportSize({ width: 1440, height: 900 });
    await open(page, theme);
    await con(page).getByTestId("composer-file").setInputFiles({ name: "case.png", mimeType: "image/png", buffer: RENDER });
    const thumb = con(page).locator("[data-attachments] [data-attachment]");
    await expect(thumb).toHaveCount(1);
    await expect(thumb.locator("img")).toHaveJSProperty("complete", true);
    await expect(con(page).getByTestId("make-part-from-drawing")).toHaveCount(0);   // a render, not a drawing
    await con(page).getByRole("textbox", { name: "Command line" }).fill("why does the lid look offset here?");
    if (SHOTS) await con(page).locator("[data-command-line]").screenshot({ path: `${SHOTS}/composer-attachment-${theme}.png` });
    await con(page).getByTestId("composer-send").click();
    await expect.poll(async () => (await fake.calls()).some((c) => c.method === "POST" && c.path === `/projects/${BUILD}/chat`)).toBe(true);
    const sent = (await fake.calls()).filter((c) => c.method === "POST" && c.path === `/projects/${BUILD}/chat`).at(-1)?.body as { text: string; images: { media_type: string; data_b64: string }[] };
    expect(sent.text).toBe("why does the lid look offset here?");
    expect(sent.images).toHaveLength(1);
    expect(sent.images[0].media_type).toMatch(/^image\/(png|jpeg)$/);
    expect(sent.images[0].data_b64.length * 0.75).toBeLessThan(1_000_000);
    await expect(con(page).locator("[data-attachments]")).toHaveCount(0);
    // the thread shows the message with its image (from the build's store)
    const msg = con(page).locator("[data-msg=user]", { hasText: "why does the lid look offset here?" });
    await expect(msg.locator("[data-chat-image] img")).toBeVisible();
    await expect(msg.locator("[data-chat-image] img")).toHaveJSProperty("complete", true);
    if (SHOTS) await msg.screenshot({ path: `${SHOTS}/message-with-image-${theme}.png` });
  });
}

test("a_dropped_drawing_offers_make_a_part_from_this_drawing", async ({ page, fake }) => {
  await fake.reset(scenario() as never);
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page, "dark");
  await con(page).getByTestId("composer-file").setInputFiles({ name: "plate.png", mimeType: "image/png", buffer: DRAWING });
  const make = con(page).getByTestId("make-part-from-drawing");
  await expect(make).toBeVisible();
  if (SHOTS) await con(page).locator("[data-command-line]").screenshot({ path: `${SHOTS}/composer-drawing.png` });
  await make.click();
  await expect.poll(async () => (await fake.calls()).some((c) => c.method === "POST" && c.path.startsWith("/imports/drawing"))).toBe(true);
  await expect(con(page).locator("[data-attachments]")).toHaveCount(0);
});

test("at_most_five_images_per_message", async ({ page, fake }) => {
  await fake.reset(scenario() as never);
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page, "dark");
  const files = Array.from({ length: 6 }, (_, i) => ({ name: `shot${i}.png`, mimeType: "image/png", buffer: RENDER }));
  await con(page).getByTestId("composer-file").setInputFiles(files);
  await expect(con(page).locator("[data-attachment]")).toHaveCount(5);
  await expect(con(page).getByRole("alert")).toContainText("at most 5 images");
  await expect(con(page).getByTestId("composer-attach")).toBeDisabled();
});
