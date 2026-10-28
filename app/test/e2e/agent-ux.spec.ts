// Plan 4 Task 5 against the fake core ("agent-ux" scenario): the conversation view is the
// default console, with the approval card and failed calls inline; the run switcher; the inline
// composer; the model picker's groups, badges, "Start Ollama" and a download with progress; Auto
// parallel agents; a run that queues for a slot. Approval safety itself is approvals.spec.ts.
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

const DES = "b7c79a57cbb4";
const AID = "a332e73aa8f2";
const RED = "rgb(255, 90, 74)";

const consoleRegion = (page: Page) => page.locator("[data-region=console]");
const thread = (page: Page) => page.getByRole("log", { name: "Conversation" });
const acts = (page: Page) => thread(page).locator("[data-activity] [data-activity-text]");
const switcher = (page: Page) => page.getByRole("button", { name: /^Run: / });

test.beforeEach(async ({ page, fake }) => {
  await fake.reset("agent-ux");
  await page.setViewportSize({ width: 1440, height: 900 });
});

async function open(page: Page) {
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
}

test("console_header_stays_one_line_at_1280x800", async ({ page }) => {
  // real-app QA: the run label wrapped onto a second line and "+ New" / "Full log" broke in two
  await page.setViewportSize({ width: 1280, height: 800 });
  await open(page);
  const row = consoleRegion(page).locator(".cv-top-row");
  const lineH = await row.locator("[data-run-label]").evaluate((el) => parseFloat(getComputedStyle(el).lineHeight) || 16);
  for (const name of ["+ New", "Full log"]) {
    const h = (await row.getByRole("button", { name, exact: true }).boundingBox())?.height ?? 0;
    expect(h, name).toBeLessThan(lineH * 2);
  }
  const label = await row.locator("[data-run-label]").evaluate((el) => ({ h: el.getBoundingClientRect().height, ws: getComputedStyle(el).whiteSpace }));
  expect(label.ws).toBe("nowrap");
  expect(label.h).toBeLessThan(lineH * 1.6);
  // and "Full log" is not under the pinned collapse button
  const full = await row.getByRole("button", { name: "Full log", exact: true }).boundingBox();
  const collapse = await page.getByRole("button", { name: "Collapse the agent console" }).boundingBox();
  expect(full && collapse && full.x + full.width <= collapse.x + 0.5).toBe(true);
});

test("conversation_is_the_default_with_activity_lines_and_the_approval_card_inline", async ({ page }) => {
  await open(page);
  await expect(consoleRegion(page).locator("[data-view=chat]")).toBeVisible();
  await expect(consoleRegion(page).locator("[data-filters]")).toHaveCount(0);
  await expect(switcher(page)).toContainText("designer · thicken mount plate");
  await expect(thread(page).locator("[data-msg=user]").first()).toContainText("thicken the mount plate to 6 mm");
  await expect(acts(page)).toHaveText([
    "created mount plate · 3.9 cm³",
    "changed mount plate · thickness 5 → 6 · +0.8 cm³",
    "measured mount plate · 4.6 cm³",
    "asking to merge mount plate",
  ]);
  await expect(thread(page).locator(`[data-approval="${AID}"]`)).toBeVisible();
  await expect(thread(page).locator(`[data-approval="${AID}"]`)).toContainText("▲ NEEDS YOU");
  // ▸ shows the raw tool input/output
  const line = thread(page).locator("[data-activity]", { hasText: "changed mount plate" });
  await line.getByRole("button", { name: /^Show details/ }).click();
  await expect(line.locator("[data-call-details]")).toContainText('"thickness": 6');
  await expect(line.locator("[data-diff]")).toContainText("+ thickness = 6.00 mm");
});

test("failed_calls_and_errors_are_red_inline_and_another_run_needing_you_is_one_click_away", async ({ page }) => {
  await open(page);
  await switcher(page).click();
  const menu = page.getByRole("listbox", { name: "Runs" });
  await expect(menu.getByRole("option")).toHaveCount(2);
  await menu.getByRole("option", { name: /checker · check hole spacing/ }).click();
  await expect(switcher(page)).toContainText("checker · check hole spacing");
  const fail = thread(page).locator("[data-activity][data-tone=fail]");
  await expect(fail).toContainText("changing mount plate failed · wall 0.4 mm is below the 0.8 mm minimum");
  await expect(fail).toContainText("use at least 0.8 mm");
  await expect(fail.locator("[data-activity-text]")).toHaveCSS("color", RED);
  const err = thread(page).locator("[data-error]");
  await expect(err).toContainText("transient · provider timed out");
  await expect(err.locator(".cv-fail").first()).toHaveCSS("color", RED);
  // the designer's approval is still one click away
  const needs = page.getByRole("button", { name: "designer · thicken mount plate to needs you" });
  await expect(needs).toHaveText("▲ 1 needs you");
  await needs.click();
  await expect(thread(page).locator(`[data-approval="${AID}"]`)).toBeVisible();
});

test("details_toggle_shows_the_dense_log_and_is_remembered", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "Full log", exact: true }).click();
  await expect(consoleRegion(page).locator("[data-roster]")).toBeVisible();
  await expect(consoleRegion(page).locator("[data-filters]")).toBeVisible();
  await expect(page.locator(`[data-roster] [data-run="${DES}"]`)).toBeVisible();
  await page.reload();
  await expect(consoleRegion(page).locator("[data-roster]")).toBeVisible();
  await page.getByRole("button", { name: "Conversation" }).click();
  await expect(thread(page)).toBeVisible();
});

test("inline_composer_starts_a_run_with_profile_defaults", async ({ page, fake }) => {
  await open(page);
  await page.getByRole("button", { name: "New" }).click();
  const form = page.getByRole("form", { name: "New run" });
  await expect(form).toBeVisible();
  await expect(page.getByRole("dialog", { name: "New run" })).toHaveCount(0);
  await expect(form.getByLabel("Agent")).toHaveValue("designer");
  await expect(form.getByRole("button", { name: /^Model: Claude Code · opus/ })).toBeVisible();
  await form.getByLabel("Task").fill("stiffen the upright");
  await form.getByLabel("Task").press("Enter");
  await expect(form).toHaveCount(0);
  const posts = (await fake.calls()).filter((c) => c.method === "POST" && c.path === "/runs");
  expect(posts.map((c) => c.body)).toEqual([{ agent: "designer", prompt: "stiffen the upright", model_ref: "claude-code/opus", edit_scope: ["*"], read_scope: ["*"] }]);
  await expect(switcher(page)).toContainText("designer · stiffen upright");
  await expect(acts(page)).toHaveText(["measured mount plate · 8.0 cm³"]);
  await expect(thread(page).locator("[data-msg=agent]")).toContainText("Measuring the plate first.");
});

test("enter_in_the_picker's_other_model_box_never_starts_a_run", async ({ page, fake }) => {
  await open(page);
  await page.getByRole("button", { name: "New" }).click();
  const form = page.getByRole("form", { name: "New run" });
  await expect(form.getByRole("button", { name: /^Model: Claude Code · opus/ })).toBeVisible();
  await form.getByLabel("Task").fill("stiffen the upright");
  await form.getByRole("button", { name: /^Model:/ }).click();
  const other = form.getByLabel("Other model");
  await other.fill("gemini");                                   // a partial ref
  await other.press("Enter");
  await other.fill("gemini/gemini-3-pro");                      // a full ref: Enter picks it, still no run
  await other.press("Enter");
  await expect(form.getByRole("button", { name: /^Model: gemini · gemini-3-pro/ })).toBeVisible();
  await page.waitForTimeout(300);
  expect((await fake.calls()).filter((c) => c.method === "POST" && c.path === "/runs")).toHaveLength(0);
  await expect(form).toBeVisible();
});

test("scrolled_up_the_thread_counts_new_rows_and_a_new_card_comes_into_view", async ({ page, fake }) => {
  await open(page);
  await expect(acts(page)).toHaveCount(4);
  const t0 = 1792869500;
  await fake.emit({ type: "note", run_id: DES, data: { text: "" } }, {
    steps: Array.from({ length: 40 }, (_, i) => ({ run_id: DES, kind: "message" as const, ts: t0 + i, payload: { role: "assistant", text: `progress line ${i}`, stop_reason: "end_turn" } })),
  });
  await expect(thread(page).getByText("progress line 39")).toBeInViewport();
  await thread(page).hover();
  await page.mouse.wheel(0, -3000);
  await expect(thread(page).getByText("progress line 39")).not.toBeInViewport();
  await fake.emit({ type: "note", run_id: DES, data: { text: "" } }, {
    steps: Array.from({ length: 3 }, (_, i) => ({ run_id: DES, kind: "message" as const, ts: t0 + 100 + i, payload: { role: "assistant", text: `late line ${i}`, stop_reason: "end_turn" } })),
  });
  await expect(page.getByRole("button", { name: "3 new ↓" })).toBeVisible();
  await expect(thread(page).getByText("late line 2")).not.toBeInViewport();     // no jump
  await page.getByRole("button", { name: "3 new ↓" }).click();
  await expect(thread(page).getByText("late line 2")).toBeInViewport();
  await expect(page.getByRole("button", { name: /new ↓$/ })).toHaveCount(0);
  // scrolled up again: a question for this run is still brought into view
  await thread(page).hover();
  await page.mouse.wheel(0, -3000);
  await expect(thread(page).getByText("late line 2")).not.toBeInViewport();
  await fake.emit({ type: "question_asked", run_id: DES, data: { question_id: "q_now", question: "merge now?", options: ["yes", "no"] } });
  await expect(page.locator('[data-question="q_now"]')).toBeInViewport();
});

test("model_picker_groups_badges_start_ollama_and_download_with_progress", async ({ page, fake }) => {
  await open(page);
  await page.getByRole("button", { name: "New" }).click();
  const form = page.getByRole("form", { name: "New run" });
  await form.getByRole("button", { name: /^Model:/ }).click();
  const panel = form.getByRole("region", { name: "Choose a model" });
  await expect(panel.getByRole("group")).toHaveText([/^Recommended/, /^Your Claude Code/, /^On this Mac \(Ollama\)/, /^Free \(OpenRouter\)/, /^API keys/]);
  const rec = panel.getByRole("group", { name: "Recommended" });
  await expect(rec.locator("[data-ref='claude-code/opus'] [data-badge=best]")).toHaveText("best");
  await expect(rec).toContainText("uses your Claude Code plan");
  const free = panel.getByRole("group", { name: "Free (OpenRouter)" });
  await expect(free.locator("[data-badge=free]")).toHaveCount(2);
  await expect(free).toContainText("needs a free OpenRouter key — add it in Profile");
  // Ollama is installed but stopped: start it, then download the recommended model that fits
  const local = panel.getByRole("group", { name: "On this Mac (Ollama)" });
  await expect(local).toContainText("Ollama not running.");
  await expect(local.locator("[data-ref='ollama/gpt-oss:20b']")).toBeDisabled();
  await local.getByRole("button", { name: "Start Ollama" }).click();
  await expect(local.getByRole("button", { name: "Start Ollama" })).toHaveCount(0);
  await expect(local.locator("[data-ref='ollama/gpt-oss:20b']")).toBeEnabled();
  await expect(local.locator("[data-download='qwen3.6:27b']")).toHaveCount(0);       // doesn't fit this Mac
  const dl = local.getByRole("button", { name: "Download qwen3-coder:30b (19 GB)" });
  await expect(dl).toHaveText("Download (19 GB)");
  await dl.click();
  await expect(local.locator("[data-pull='qwen3-coder:30b']")).toContainText(/\d+% · [\d.]+ of 19\.0 GB/);
  await expect(local.locator("[data-ref='ollama/qwen3-coder:30b']")).toContainText("qwen3-coder:30b · 19 GB");
  await local.locator("[data-ref='ollama/qwen3-coder:30b']").click();
  await expect(form.getByRole("button", { name: /^Model: Ollama · qwen3-coder:30b/ })).toBeVisible();
  const calls = await fake.calls();
  expect(calls.filter((c) => c.path === "/local/start")).toHaveLength(1);
  expect(calls.filter((c) => c.path === "/local/pull").map((c) => c.body)).toEqual([{ model: "qwen3-coder:30b" }]);
});

test("parallel_agents_are_auto_with_the_reason_in_profile", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "Profile" }).click();
  const panel = page.getByRole("dialog", { name: "Profile" });
  await panel.getByRole("button", { name: "Agents & budgets" }).click();
  await expect(panel.getByRole("radio", { name: "Auto (3 on this Mac)" })).toBeChecked();
  await expect(panel).toContainText("M5 Pro · 24 GB · cloud model → 3");
  await expect(panel.getByLabel("Max parallel agents")).toHaveCount(0);
});

test("a_run_beyond_the_parallel_limit_waits_for_a_slot", async ({ page, fake }) => {
  const sc = JSON.parse(JSON.stringify((await import("../fake-core/scenarios/agent-ux.json", { with: { type: "json" } })).default)) as Record<string, unknown> & { status: Record<string, unknown> };
  sc.status.parallel = { limit: 1, auto: true, reason: "M5 Pro · 24 GB · local model → 1" };
  await fake.reset(sc);
  await open(page);
  await page.getByRole("button", { name: "New" }).click();
  const form = page.getByRole("form", { name: "New run" });
  await expect(form).toContainText("1 running · queues for a free slot (Auto: 1)");
  await form.getByLabel("Task").fill("add a gusset");
  await form.getByRole("button", { name: "Start" }).click();
  await expect(switcher(page)).toContainText("designer · add gusset");
  await expect(thread(page)).toContainText("waiting for a free agent slot · Auto: 1 on this Mac");
  await expect(thread(page).locator("[data-msg=user]")).toContainText("add a gusset");
});

test("blue_only_where_allowed_in_the_conversation_view", async ({ page }) => {
  await open(page);
  await expect(thread(page).locator(`[data-approval="${AID}"]`)).toBeVisible();
  await page.mouse.move(700, 450);
  const bad = await page.evaluate(() => {
    const ALLOWED = "[data-selected], [data-changing], [data-card], .active-tab, .cmd-prompt, .needs-you";
    const BLUE = ["rgb(61, 139, 255)", "rgb(141, 186, 255)", "rgba(61, 139, 255, 0.1)"];
    const out: string[] = [];
    for (const el of document.querySelectorAll("[data-region=console] *")) {
      const cs = getComputedStyle(el);
      const blue = [cs.color, cs.borderTopColor, cs.borderRightColor, cs.borderBottomColor, cs.borderLeftColor, cs.backgroundColor].filter((c) => BLUE.includes(c));
      if (blue.length && !el.closest(ALLOWED)) out.push(`${el.tagName.toLowerCase()}.${[...el.classList].join(".")} "${(el.textContent ?? "").trim().slice(0, 40)}"`);
    }
    return out;
  });
  expect(bad).toEqual([]);
});
