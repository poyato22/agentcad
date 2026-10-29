// Engineering loop e2e (Plan 5 A7) against the fake core, whose spec routes serve the real core's
// fixtures (a drone-plate spec and a 6-evaluation CalculiX design study of mount_plate): the
// interview card in the thread, the console spec strip, and the Studies page's design-study
// section with its Pareto scatter and "Use this variant".
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

const BASE = JSON.parse(readFileSync(resolve(import.meta.dirname, "../fake-core/scenarios/base.json"), "utf8")) as { runs: { id: string }[] };
const RUN = BASE.runs[0].id;
const withSpec = (spec: Record<string, unknown>) => ({ ...BASE, spec });
const STUDIES = JSON.parse(readFileSync(resolve(import.meta.dirname, "../../../core/tests/fixtures/ui/studies.json"), "utf8")) as { id: string; best_id: number }[];
const SHOTS = process.env.MISES_SHOTS;

async function open(page: Page, theme: "dark" | "light" = "dark") {
  await page.goto("/");
  await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
}

test("interview_card_answers_the_batch_by_keyboard_and_click", async ({ page, fake }) => {
  await fake.reset("base");
  await open(page);
  await fake.emit({
    type: "question_asked", run_id: RUN, data: {
      question_id: "q_iv1", question: "A few questions first:\n1. What will this drone do?\n2. Prop / size class?\n3. Rank",
      options: [], questions: [
        { id: "use", prompt: "What will this drone do?", options: ["freestyle", "racing", "cinematic", "long-range"], multi: false, rank: false, allow_text: true },
        { id: "size_class", prompt: "Prop / size class?", options: ['3"', '5"', '7"'], multi: false, rank: false, allow_text: true },
        { id: "priorities", prompt: "Rank what matters most, first = most important.", options: ["weight", "impact strength", "stiffness", "print time"], multi: false, rank: true, allow_text: true },
      ],
    },
  });
  const card = page.locator("[data-interview]");
  await expect(card).toBeVisible();
  await expect(card).toContainText("1/3 answered");
  await page.waitForTimeout(450); // the card arms ARM_MS after it appears (approval click rules)
  await card.getByRole("button", { name: "freestyle" }).click();
  await card.locator('[data-ask="size_class"]').getByRole("textbox").fill("4 inch");
  const row = card.locator('[data-rank="stiffness"]');
  await row.focus();
  await page.keyboard.press("Alt+ArrowUp");
  await page.keyboard.press("Alt+ArrowUp");
  await expect(card.locator('[data-ask="priorities"] [data-rank]').first()).toHaveAttribute("data-rank", "stiffness");
  if (SHOTS) await card.screenshot({ path: `${SHOTS}/interview-dark.png` });
  await card.getByRole("button", { name: "Send answers" }).click();
  await expect(card).toHaveCount(0);
  const sent = (await fake.calls()).filter((c) => c.path === "/questions/q_iv1/answer");
  expect(sent).toHaveLength(1);
  expect(JSON.parse((sent[0].body as { text: string }).text)).toEqual({
    use: "freestyle", size_class: "4 inch", priorities: ["stiffness", "weight", "impact strength", "print time"],
  });
});

for (const theme of ["dark", "light"] as const) {
  test(`console_spec_strip_shows_the_spec_and_study_${theme}`, async ({ page, fake }) => {
    await fake.reset(withSpec({ spec: "fixture", studies: "fixture" }) as never);
    await open(page, theme);
    const strip = page.locator("[data-spec-panel]");
    await expect(strip).toBeVisible();
    await expect(strip.getByRole("button", { expanded: false })).toContainText("2 pass");
    await strip.getByRole("button", { expanded: false }).click();
    await expect(strip.locator("[data-spec-card]")).toBeVisible();
    await expect(strip.locator('[data-spec-card] [data-req="mass_budget"]')).toHaveAttribute("data-status", "pass");
    await expect(strip.locator("[data-scatter] [data-front]")).toBeVisible();
    if (SHOTS) await page.locator(".region-console").screenshot({ path: `${SHOTS}/console-spec-${theme}.png` });
  });

  test(`studies_page_design_study_preview_and_use_variant_${theme}`, async ({ page, fake }) => {
    await fake.reset(withSpec({ spec: "fixture", studies: "fixture" }) as never);
    await open(page, theme);
    await page.getByRole("tab", { name: "Studies" }).click();
    const sec = page.locator("[data-design-studies]");
    await sec.scrollIntoViewIfNeeded();
    await expect(sec.locator("[data-study-row]")).toHaveCount(1);
    const pts = sec.locator("[data-scatter] [data-point]");
    await expect(pts).toHaveCount(6);
    await expect(sec.locator('[data-point][data-kind="pareto"]')).toHaveCount(3);
    // hover shows the tooltip; a click previews the variant
    await sec.locator('[data-point="0"]').hover();
    await expect(sec.locator("[data-tip]")).toContainText("fails the spec");
    await sec.locator('[data-point="0"]').click();
    await expect(sec.locator("[data-variant]")).toHaveAttribute("data-variant", "0");
    // keyboard: the chart is reachable and Enter previews
    await sec.locator('[data-point][tabindex="0"]').focus();
    await page.keyboard.press("End");
    await page.keyboard.press("Enter");
    await expect(sec.locator("[data-variant]")).not.toHaveAttribute("data-variant", "0");
    await sec.locator(`[data-iter="${STUDIES[0].best_id}"]`).click();
    if (SHOTS) await sec.screenshot({ path: `${SHOTS}/studies-design-${theme}.png` });
    await sec.getByRole("button", { name: "Use this variant" }).click();
    await sec.getByRole("button", { name: "Apply and rebuild" }).click();
    await expect(sec.locator("[data-applied]")).toBeVisible();
    const applied = (await fake.calls()).filter((c) => c.path.endsWith("/apply"));
    expect(applied).toHaveLength(1);
    expect(applied[0].body).toEqual({ iteration: STUDIES[0].best_id });
  });
}

test("no_spec_no_strip", async ({ page, fake }) => {
  await fake.reset("base");
  await open(page);
  await expect(page.locator("[data-conversation]")).toBeVisible();
  await expect(page.locator("[data-spec-panel]")).toHaveCount(0);
});
