// Plan 9 G-A e2e: the concept sketch card, the full-window markup (circle / lasso / pin, each
// with a comment), feedback to the lead, the agent's v2, compare, approve; and the pitch cards
// (pick / mix). Fake core: scenarios concept / concept-pitch, built from the real core's dump.
import type { Locator, Page } from "@playwright/test";
import { expect, test, type FakeCall } from "./fixtures";

const SHOTS = process.env.MISES_SHOTS_DIR ?? "";

async function shot(page: Page, name: string): Promise<void> {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png` });
}

/** Drag on the sketch overlay in normalised image coordinates. */
async function drag(page: Page, overlay: Locator, pts: [number, number][]): Promise<void> {
  const box = await overlay.boundingBox();
  if (!box) throw new Error("no overlay");
  const at = (p: [number, number]) => [box.x + p[0] * box.width, box.y + p[1] * box.height] as const;
  await page.mouse.move(...at(pts[0]));
  await page.mouse.down();
  for (const p of pts.slice(1)) await page.mouse.move(...at(p), { steps: 4 });
  await page.mouse.up();
}

async function comment(page: Page, text: string): Promise<void> {
  const box = page.getByTestId("mark-comment");
  await expect(box).toBeVisible();
  await box.locator("textarea").fill(text);
  await box.getByTestId("mark-done").click();
  await expect(box).toHaveCount(0);
}

const posts = async (calls: () => Promise<FakeCall[]>, re: RegExp) => (await calls()).filter((c) => c.method === "POST" && re.test(c.path));

test.describe("concept sketch", () => {
  test.beforeEach(async ({ fake, page }) => {
    await fake.reset("concept");
    await page.setViewportSize({ width: 1440, height: 900 });
  });

  test("sketch v1 → circle, lasso and pin with comments → v2 appears → approve", async ({ page, fake }) => {
    await page.goto("/");
    const card = page.getByTestId("sketch-card").last();
    await expect(card).toHaveAttribute("data-version", "1", { timeout: 15_000 });
    await expect(card.getByTestId("sketch-status")).toHaveText("Waiting for your review");
    await expect(card.locator(".sk-sheet img")).toBeVisible();
    await shot(page, "card-v1-dark");
    // FW-app I6: a sketch is a picture: the dock's View stream, not the conversation
    await expect(page.locator("[data-dock] [data-stream=view] [data-testid=sketch-card]")).toHaveCount(1);
    await expect(page.locator("[data-dock] [data-stream=text] [data-testid=sketch-card]")).toHaveCount(0);

    await card.getByTestId("sketch-markup").click();
    const review = page.getByTestId("sketch-review");
    await expect(review).toBeVisible();
    const overlay = review.getByTestId("sketch-overlay");

    // circle: drag from the centre out
    await drag(page, overlay, [[0.42, 0.42], [0.5, 0.42]]);
    await comment(page, "Rounder, friendlier head");
    // lasso: draw around the body
    await review.locator("[data-tool=lasso]").click();
    await drag(page, overlay, [[0.5, 0.3], [0.72, 0.32], [0.75, 0.5], [0.55, 0.55], [0.48, 0.4]]);
    await comment(page, "Shorter body");
    // pin: a click (P picks the tool)
    await page.keyboard.press("p");
    await expect(review.locator("[data-tool=pin]")).toHaveAttribute("aria-pressed", "true");
    const box = await overlay.boundingBox();
    await page.mouse.click(box!.x + 0.4 * box!.width, box!.y + 0.75 * box!.height);
    await comment(page, "Thicker legs");
    await review.getByTestId("review-comment").fill("Love the stance overall.");
    await expect(review.getByTestId("mark-list").locator("li")).toHaveCount(3);
    await expect(overlay.locator(".sk-mark")).toHaveCount(3);
    await shot(page, "review-marks-dark");

    await review.getByTestId("review-send").click();
    await expect(review).toHaveCount(0);
    const [fb] = await posts(fake.calls, /^\/concept\/sketches\/[^/]+\/feedback$/);
    const body = fb.body as { marks: { kind: string; comment: string; image: number; cx?: number; r?: number; points?: number[][]; x?: number; y?: number }[]; comment: string };
    expect(body.comment).toBe("Love the stance overall.");
    expect(body.marks.map((m) => m.kind)).toEqual(["circle", "lasso", "pin"]);
    expect(body.marks.map((m) => m.comment)).toEqual(["Rounder, friendlier head", "Shorter body", "Thicker legs"]);
    const [c, l, p] = body.marks;
    expect(c.cx).toBeCloseTo(0.42, 1);
    expect(c.r).toBeCloseTo(0.08, 1);
    expect(l.points!.length).toBeGreaterThanOrEqual(3);
    for (const pt of l.points!) { expect(pt[0]).toBeGreaterThanOrEqual(0); expect(pt[0]).toBeLessThanOrEqual(1); }
    expect(p.x).toBeCloseTo(0.4, 1);
    expect(p.y).toBeCloseTo(0.75, 1);
    // the thread shows the short line; the agent answers with v2
    await expect(page.locator(".cv-thread")).toContainText("Sketch feedback v1: 3 marks and a comment…");
    await expect(card).toHaveAttribute("data-version", "2", { timeout: 10_000 });
    await expect(card.getByTestId("sketch-version")).toHaveText(["v1", "v2"]);
    await card.getByTestId("sketch-compare").click();
    await expect(card.locator(".sk-stage.is-compare .sk-fig")).toHaveCount(2);
    await shot(page, "card-v2-compare-dark");
    // v1 keeps the marks the user sent
    await card.getByTestId("sketch-version").first().click();
    await expect(card.locator(".sk-mark")).toHaveCount(3);
    await card.getByTestId("sketch-version").last().click();

    await card.getByTestId("sketch-approve").click();
    await expect(card.getByTestId("sketch-status")).toHaveText("Approved v2");
    await expect(card.getByTestId("sketch-approve")).toHaveCount(0);
    const [ap] = await posts(fake.calls, /\/concept\/approve$/);
    expect(ap.body).toEqual({ sketch_id: expect.stringMatching(/^cks_/) as unknown as string });
  });

  test("skip sketch opens the gate; Esc closes the review", async ({ page, fake }) => {
    await page.goto("/");
    const card = page.getByTestId("sketch-card").last();
    await expect(card).toHaveAttribute("data-version", "1", { timeout: 15_000 });
    await card.getByTestId("sketch-open").click();
    await expect(page.getByTestId("sketch-review")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("sketch-review")).toHaveCount(0);
    await card.getByTestId("sketch-skip").click();
    await expect(card.getByTestId("sketch-status")).toHaveText("Sketch skipped");
    expect(await posts(fake.calls, /\/concept\/skip$/)).toHaveLength(1);
  });
});

test.describe("concept sketch, light theme", () => {
  test.use({ colorScheme: "light" });
  test("the review with marks reads in light mode", async ({ page, fake }) => {
    await fake.reset("concept");
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    const card = page.getByTestId("sketch-card").last();
    await expect(card).toHaveAttribute("data-version", "1", { timeout: 15_000 });
    await shot(page, "card-v1-light");
    await card.getByTestId("sketch-markup").click();
    const review = page.getByTestId("sketch-review");
    const overlay = review.getByTestId("sketch-overlay");
    await drag(page, overlay, [[0.42, 0.42], [0.5, 0.42]]);
    await comment(page, "Rounder, friendlier head");
    await review.locator("[data-tool=pin]").click();
    const box = await overlay.boundingBox();
    await page.mouse.click(box!.x + 0.4 * box!.width, box!.y + 0.75 * box!.height);
    await expect(page.getByTestId("mark-comment")).toBeVisible();
    await page.getByTestId("mark-comment").locator("textarea").fill("Thicker legs");
    await shot(page, "review-marks-light");
    await expect(overlay.locator(".sk-mark")).toHaveCount(2);
  });
});

test.describe("design directions", () => {
  test.beforeEach(async ({ fake, page }) => {
    await fake.reset("concept-pitch");
    await page.setViewportSize({ width: 1440, height: 900 });
  });

  test("three option cards with silhouettes, CMF and trade-offs; mix two", async ({ page, fake }) => {
    await page.goto("/");
    const pitch = page.getByTestId("pitch-cards").last();
    await expect(pitch.getByTestId("pitch-option")).toHaveCount(3, { timeout: 15_000 });
    await expect(page.locator("[data-dock] [data-stream=view] [data-testid=pitch-cards]")).toHaveCount(1);   // FW-app I6
    await expect(page.locator("[data-dock] [data-stream=text] [data-testid=pitch-cards]")).toHaveCount(0);
    const rec = pitch.locator("[data-option=Companion]");
    await expect(rec.locator(".sk-tag.is-rec")).toHaveText("Recommended");
    await expect(pitch.locator("[data-option=Honest] .sk-tag")).toHaveText("Baseline");
    await expect(rec.locator(".sk-sil img")).toHaveCount(2);
    await expect(rec.locator(".sk-trade")).toContainText("+6%");
    await shot(page, "pitch-dark");
    await pitch.getByTestId("pitch-mix").click();
    await pitch.locator("[data-option=Companion] input[type=checkbox]").check();
    await pitch.locator("[data-option=Companion] .sk-take").fill("body");
    await pitch.locator("[data-option=Athlete] input[type=checkbox]").check();
    await pitch.locator("[data-option=Athlete] .sk-take").fill("legs");
    await pitch.getByTestId("pitch-send-mix").click();
    await expect(pitch.getByTestId("pitch-chosen")).toContainText("Companion's body + Athlete's legs");
    const [ch] = await posts(fake.calls, /^\/concept\/pitches\/[^/]+\/choose$/);
    expect(ch.body).toMatchObject({ mix: [{ option: "Companion", take: "body" }, { option: "Athlete", take: "legs" }] });
  });
});
