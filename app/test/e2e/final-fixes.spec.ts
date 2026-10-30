// Plan 5 final fix wave (app): the final review's repros (scratchpad fr/s1–s5) as e2e tests,
// against the fake core. Each test names the finding it covers. With MISES_SHOTS set, each one
// also saves its after-state in both themes at 1440×900.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Locator, Page } from "@playwright/test";
import { expect, test, type FakeCall, type FakeControl } from "./fixtures";

const QUAD = "q0quadframe0";
const BENCH = "b0benchrig00";
const SHOTS = process.env.MISES_SHOTS;
const BASE = JSON.parse(readFileSync(resolve(import.meta.dirname, "../fake-core/scenarios/base.json"), "utf8")) as { runs: { id: string }[] };

const tree = (page: Page) => page.getByTestId("part-tree");
const callsTo = async (fake: FakeControl, method: string, path: string | RegExp): Promise<FakeCall[]> =>
  (await fake.calls()).filter((c) => c.method === method && (typeof path === "string" ? c.path === path : path.test(c.path)));

/** The after-shot in both themes (data-theme flipped on <html> in the same state). */
async function shots(page: Page, name: string, el: Locator | null = null): Promise<void> {
  if (!SHOTS) return;
  for (const t of ["dark", "light"] as const) {
    await page.evaluate((th) => document.documentElement.setAttribute("data-theme", th), t);
    await page.waitForTimeout(150);
    const path = `${SHOTS}/${name}-${t}.png`;
    if (el) await el.screenshot({ path }); else await page.screenshot({ path });
  }
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
}

async function openBuilds(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page.getByRole("tab", { name: "Quad frame" })).toHaveAttribute("aria-selected", "true");
  await expect(tree(page).getByRole("button", { name: "Quad arm", exact: true })).toBeVisible();
}

async function openBase(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
}

test.afterEach(async ({ fake }) => { await fake.reset("base"); });

test("I-2: a run started right after a tab switch is filed under the new build", async ({ page, fake }) => {
  await fake.reset("builds");
  await openBuilds(page);
  await page.getByRole("button", { name: "All builds and the Trash" }).click();
  await page.getByRole("menuitemradio", { name: /Bench rig/ }).click();
  await expect(page.getByRole("tab", { name: "Bench rig" })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Meta+n");
  const dialog = page.getByRole("dialog", { name: "New run" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Task").fill("a bracket for the bench");
  await dialog.getByRole("button", { name: "Start" }).click();
  await expect(dialog).toBeHidden();
  const post = (await callsTo(fake, "POST", "/runs")).at(-1);
  expect((post?.body as { project_id?: string }).project_id).toBe(BENCH);
});

test("I-3: Replay intro closes Profile and plays above every panel", async ({ page }) => {
  await openBase(page);
  await page.getByRole("button", { name: /^Profile/ }).first().click();
  const panel = page.locator(".profile-pane, [data-profile-pane], [role=dialog][aria-label*=Profile]").first();
  await page.getByRole("button", { name: "System", exact: true }).click();
  await page.getByRole("button", { name: "Replay intro" }).click();
  const intro = page.locator(".intro");
  await expect(intro).toBeVisible();
  await expect(panel).toBeHidden();
  // the intro is what's on top at the centre of the window and at the right, where Profile was
  for (const [x, y] of [[720, 450], [1300, 450]]) {
    const top = await page.evaluate(([px, py]) => !!document.elementFromPoint(px, py)?.closest(".intro"), [x, y]);
    expect(top).toBe(true);
  }
  await page.waitForTimeout(1500);
  await shots(page, "i3-replay-intro-above-panels");
  await page.keyboard.press("Escape");
  await expect(intro).toBeHidden();
});

test("I-4: Use this variant sends base_version; a change meanwhile is a 409 with Reload / Keep", async ({ page, fake }) => {
  await fake.reset({ ...BASE, spec: { spec: "fixture", studies: "fixture" } });
  await openBase(page);
  await page.getByRole("tab", { name: "Studies" }).click();
  const sec = page.locator("[data-design-studies]");
  await sec.scrollIntoViewIfNeeded();
  await sec.getByRole("button", { name: "Use this variant" }).click();
  const confirm = sec.getByRole("group", { name: "Confirm variant" });
  await expect(confirm).toBeVisible();
  await fake.agentEdit({ part_id: "mount_plate", vars: { thickness: 9 }, summary: "set thickness=9" });
  await confirm.getByRole("button", { name: "Apply and rebuild" }).click();
  await expect(sec.locator("[data-apply-error][data-conflict]")).toContainText("The part changed since you opened this");
  const first = (await callsTo(fake, "POST", /\/studies\/[^/]+\/apply$/))[0];
  expect(typeof (first.body as { base_version?: unknown }).base_version).toBe("number");
  await shots(page, "i4-variant-409-reload-keep", sec);
  await confirm.getByRole("button", { name: "Reload" }).click();
  await expect(sec.locator("[data-apply-error]")).toHaveCount(0);
  await expect(confirm).toContainText("9");
  await confirm.getByRole("button", { name: "Apply and rebuild" }).click();
  await expect(sec.locator("[data-applied]")).toBeVisible();
  const applies = await callsTo(fake, "POST", /\/studies\/[^/]+\/apply$/);
  expect((applies.at(-1)?.body as { base_version: number }).base_version).toBe((first.body as { base_version: number }).base_version + 1);
});

test("I-5: interview — chip vs text, multi + text, keyboard ranking, counter and Sending…", async ({ page, fake }) => {
  await fake.reset("base");
  await openBase(page);
  await fake.emit({
    type: "question_asked", run_id: BASE.runs[0].id, data: {
      question_id: "q_iv1", question: "A few questions first:\n1. What will this drone do?", options: [], questions: [
        { id: "use", prompt: "What will this drone do?", options: ["freestyle", "racing", "cinematic"], multi: false, rank: false, allow_text: true },
        { id: "extras", prompt: "Which extras?", options: ["GPS", "HD camera", "LEDs"], multi: true, rank: false, allow_text: true },
        { id: "priorities", prompt: "Rank what matters most", options: ["weight", "impact strength", "stiffness"], multi: false, rank: true, allow_text: true },
      ],
    },
  });
  const card = page.locator("[data-interview]");
  await expect(card).toBeVisible();
  await expect(card.locator("[data-count]")).toHaveText("1/3 answered");
  await expect(card.locator("[data-open]")).toHaveText("Answer 1, 2 to send.");
  await page.waitForTimeout(450);
  // single: typed, then a chip — the chip wins and the text clears (what's shown is what's sent)
  const useBox = card.locator('[data-q="use"]').getByRole("textbox");
  await useBox.fill("mapping");
  await card.getByRole("button", { name: "racing" }).click();
  await expect(useBox).toHaveValue("");
  await expect(card.getByRole("button", { name: "racing" })).toHaveAttribute("aria-pressed", "true");
  // multi: a chip plus typed text, both kept
  await card.getByRole("button", { name: "GPS" }).click();
  await card.locator('[data-q="extras"]').getByRole("textbox").fill("lidar");
  await expect(card.getByRole("button", { name: "GPS" })).toHaveAttribute("aria-pressed", "true");
  // rank: Alt+↑ twice on a focused row moves it to the top, focus stays on it
  const row = card.locator('[data-rank="stiffness"]');
  await row.focus();
  await page.keyboard.press("Alt+ArrowUp");
  await page.keyboard.press("Alt+ArrowUp");
  await expect(card.locator("[data-rank]")).toHaveText([/stiffness/, /weight/, /impact strength/]);
  await expect(card.locator('[data-rank="stiffness"]')).toBeFocused();
  await expect(card.locator("[data-count]")).toHaveText("3/3 answered");
  await shots(page, "i5-interview-card", card);
  await card.getByRole("button", { name: "Send answers" }).click();
  await expect(card).toHaveCount(0);
  const sent = (await fake.calls()).filter((c) => c.path === "/questions/q_iv1/answer");
  expect(sent).toHaveLength(1);
  expect(JSON.parse((sent[0].body as { text: string }).text)).toEqual({
    use: "racing", extras: ["GPS", "lidar"], priorities: ["stiffness", "weight", "impact strength"],
  });
});

test("M-1: in Code view the Part | Assembly switch is on top and usable", async ({ page }) => {
  await page.goto("/");
  await page.locator("[data-region=app][data-conn=open]").waitFor();
  await page.getByRole("tab", { name: "Code" }).click();
  await page.getByTestId("source-editor").waitFor();
  const ms = page.getByTestId("mode-switch");
  const bb = await ms.boundingBox();
  expect(bb).not.toBeNull();
  const onTop = await page.evaluate(([x, y]) => document.elementFromPoint(x, y)?.closest("[data-testid]")?.getAttribute("data-testid"), [bb!.x + bb!.width / 2, bb!.y + bb!.height / 2]);
  expect(onTop).toBe("mode-switch");
  const tabs = await page.locator(".vp-tabs").boundingBox();
  expect(Math.abs((tabs?.y ?? 0) - bb!.y)).toBeLessThan(2);           // one row with Model | Code
  expect(bb!.x).toBeGreaterThan((tabs?.x ?? 0) + (tabs?.width ?? 0));  // after it, not over it
  await shots(page, "m1-code-view-mode-switch", page.locator(".vp").first());
});

test("M-3: the Into build picker follows a build-tab switch after a pick", async ({ page, fake }) => {
  await fake.reset("builds");
  await openBuilds(page);
  const picker = async () => {
    await page.getByRole("tab", { name: "Parts" }).click();
    await page.getByRole("button", { name: "Motors" }).click();
    const card = page.locator('[data-item="motor_2207_class"]');
    if (await page.locator('[data-detail="motor_2207_class"]').count() === 0) await card.click();
    return page.locator('[data-detail="motor_2207_class"]').getByRole("combobox", { name: "Into build" });
  };
  const sel = await picker();
  await expect(sel).toHaveValue(QUAD);
  await sel.selectOption(BENCH);                  // picked Bench rig here
  await page.getByRole("tab", { name: "Design" }).click();
  await page.getByRole("button", { name: "All builds and the Trash" }).click();
  await page.getByRole("menuitemradio", { name: /Bench rig/ }).click();
  await page.getByRole("tab", { name: "Quad frame" }).click();   // then switched tabs back to Quad
  await expect(page.getByRole("tab", { name: "Quad frame" })).toHaveAttribute("aria-selected", "true");
  await expect(await picker()).toHaveValue(QUAD);  // the picker follows the tab, not the old pick
  await shots(page, "m3-into-build-follows-tab", page.locator('[data-detail="motor_2207_class"]'));
});

test("M-9: the Studies type picker moves with the arrow keys", async ({ page }) => {
  await page.goto("/");
  await page.locator("[data-region=app][data-conn=open]").waitFor();
  await page.getByRole("tab", { name: "Studies" }).click();
  const first = page.locator(".st-types [role=tab]").first();
  await first.click();
  await page.keyboard.press("ArrowRight");
  const second = page.locator(".st-types [role=tab]").nth(1);
  await expect(second).toHaveAttribute("aria-selected", "true");
  await expect(second).toBeFocused();
  await page.keyboard.press("ArrowLeft");
  await expect(first).toBeFocused();
  await expect(first).toHaveAttribute("aria-selected", "true");
  await shots(page, "m9-studies-type-tabs", page.locator(".st-types"));
});
