// The app-side fixes from the real-core QA of Plan 5 (scratchpad qa-engineering-findings*.md and
// qa-workspace-findings.md), one test per repro, against the fake core. Each test names its item.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";
import type { AgentRun } from "../../src/api/types";
import { expect, homeMenu, openManualStudySetup, test } from "./fixtures";

const BASE = JSON.parse(readFileSync(resolve(import.meta.dirname, "../fake-core/scenarios/base.json"), "utf8")) as Record<string, unknown> & { runs: AgentRun[] };
const RUN = BASE.runs[0].id;   // a running designer run
const FX = resolve(import.meta.dirname, "../../../core/tests/fixtures/ui");
const read = (n: string): unknown => JSON.parse(readFileSync(resolve(FX, n), "utf8"));
const STUDIES = read("studies.json") as { id: string; best_id: number; kept_variants: string[]; iterations: { n: number; metrics: { variant: string } }[] }[];

function run(id: string, over: Partial<AgentRun> = {}): AgentRun {
  return {
    id, parent_id: null, agent: "designer", provider: "fake", model: "fake-model", task: "crash now please",
    status: "done", branch: null, onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0,
    created_at: 1_790_000_000, updated_at: 1_790_000_000, owner_pid: null, external: false, session_ref: null, ...over,
  };
}

async function open(page: Page) {
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
}
const manager = (page: Page) => page.getByRole("dialog", { name: "Agent manager" });

test("C3_activity_is_seeded_on_load_so_the_pill_shows_a_working_run", async ({ page, fake }) => {
  await fake.reset("base");
  // the run's activity happened before this window connected
  await fake.emit({ type: "agent_activity", run_id: RUN, data: { run_id: RUN, state: "tool", detail: "listing parts", since: Date.now() / 1000 } });
  await open(page);
  await expect(page.locator(".activity-pill")).toContainText("1 working");
  // and again after a reload
  await page.reload();
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  await expect(page.locator(".activity-pill")).toContainText("1 working");
});

test("C2_a_pending_question_comes_back_after_a_reload", async ({ page, fake }) => {
  await fake.reset("base");
  await open(page);
  await fake.emit({ type: "question_asked", run_id: RUN, data: { question_id: "q_phone", question: "Which phone do you have?", options: ["iPhone 17", "Pixel 11"] } });
  await expect(page.locator("[data-card]").filter({ hasText: "Which phone do you have?" })).toBeVisible();
  await page.reload();
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  await expect(page.locator("[data-card]").filter({ hasText: "Which phone do you have?" })).toBeVisible();
  await expect(page.locator(".activity-pill")).toContainText("1 waiting");
});

test("C5_a_rename_in_the_manager_sticks", async ({ page, fake }) => {
  await fake.reset({ ...BASE, runs: [...BASE.runs, run("r_crash")] });
  await open(page);
  // Plan 9 D9: the left pane's History is gone (the BOM took its place); the manager lists runs
  await page.keyboard.press("Meta+Shift+KeyA");
  await manager(page).getByRole("button", { name: "Advanced ▸" }).click();
  await manager(page).getByRole("tab", { name: "All runs" }).click();   // the dense run tree (R10: the simple Agents view is the default)
  const row = manager(page).locator('[data-run-row="r_crash"]');
  await row.getByRole("button", { name: "Rename" }).click();
  await row.getByRole("textbox", { name: "Run title" }).fill("QA renamed run");
  await row.getByRole("textbox", { name: "Run title" }).press("Enter");
  await expect(row).toContainText("QA renamed run");
  await page.keyboard.press("Escape");
});

test("C6_a_deleted_run_leaves_the_manager_and_cannot_be_opened", async ({ page, fake }) => {
  await fake.reset({ ...BASE, runs: [...BASE.runs, run("r_zulu", { task: "hello zulu" }), run("r_old1", { task: "old one" }), run("r_old2", { task: "old two" })] });
  await open(page);
  await page.keyboard.press("Meta+Shift+KeyA");
  await manager(page).getByRole("button", { name: "Advanced ▸" }).click();
  await manager(page).getByRole("tab", { name: "All runs" }).click();   // the dense run tree
  const row = manager(page).locator('[data-run-row="r_zulu"]');
  await row.getByRole("button", { name: "Delete", exact: true }).click();
  await row.getByRole("button", { name: "Confirm delete" }).click();
  await expect(row).toHaveCount(0);
  // Clean up deletes the rest
  await manager(page).getByRole("button", { name: "Clean up…" }).click();
  await manager(page).getByRole("combobox", { name: "Age" }).selectOption("null");
  await manager(page).getByRole("button", { name: "Check" }).click();
  await manager(page).getByRole("button", { name: /^Delete \d+ runs?$/ }).click();
  await manager(page).getByRole("button", { name: /^Really delete/ }).click();
  await expect(manager(page).locator("[data-cleanup-outcome]")).toBeVisible();
  for (const id of ["r_zulu", "r_old1", "r_old2"]) await expect(manager(page).locator(`[data-run-row="${id}"]`)).toHaveCount(0);
  await page.keyboard.press("Escape");
  // the selection moved off the deleted run: the Conversation header never names it
  await expect(page.locator("[data-conversation-head]")).not.toContainText("hello zulu");
});

test("C4_a_hidden_built_in_agent_is_not_offered_in_new_run", async ({ page, fake }) => {
  await fake.reset("base");
  await open(page);
  await page.keyboard.press("Meta+Shift+KeyA");
  await manager(page).getByRole("button", { name: "Advanced ▸" }).click();
  await manager(page).getByRole("tab", { name: "Definitions" }).click();
  await manager(page).locator('[data-agent="designer"]').getByRole("button", { name: "Hide" }).click();
  await expect(manager(page).locator('[data-agent="designer"]')).toContainText("hidden from New run");
  await page.keyboard.press("Escape");
  await page.keyboard.press("Meta+KeyN");
  // R8: the agent list lives behind the "specific agent" override
  const dlg = page.getByRole("dialog", { name: "New run" });
  await dlg.getByRole("button", { name: "More options" }).click();
  await dlg.getByRole("menuitem", { name: "Use a specific agent…" }).click();
  const select = dlg.getByRole("combobox", { name: "Choose an agent" });
  await expect(select).toBeVisible();
  // the list loads from GET /agents: wait for it, then check it (the override's placeholder aside)
  await expect(select.locator("option")).not.toHaveCount(0);
  await expect.poll(() => select.locator("option").allTextContents().then((a) => a.filter((t) => t !== "Choose an agent"))).toEqual(["planner", "sim", "verifier"]);
  await page.keyboard.press("Escape");
});

test("C7_the_only_these_tools_picker_lists_every_registered_tool", async ({ page, fake }) => {
  await fake.reset("base");
  await open(page);
  await page.keyboard.press("Meta+Shift+KeyA");
  await manager(page).getByRole("button", { name: "Advanced ▸" }).click();
  await manager(page).getByRole("tab", { name: "Definitions" }).click();
  await manager(page).getByRole("button", { name: "New agent" }).click();
  await manager(page).getByRole("radio", { name: "Only these" }).check();
  const grid = manager(page).getByRole("group", { name: "Allowed tools" });
  // the real registry (tools.json, less the dump's fixture_merge): every plan's tools
  const real = (JSON.parse(readFileSync(resolve(import.meta.dirname, "../../../core/tests/fixtures/ui/tools.json"), "utf8")) as { name: string }[])
    .filter((t) => t.name !== "fixture_merge");
  expect(real.length).toBeGreaterThan(57);
  await expect(grid.getByRole("checkbox")).toHaveCount(real.length);
  for (const t of ["mirror_part_create", "spec_set", "study_run", "assembly_add_joint", "catalog_insert", "print_slice", "agent_spawn"]) {
    await expect(grid.getByRole("checkbox", { name: t, exact: true })).toBeVisible();
  }
});

test("C7_without_GET_tools_the_picker_falls_back_to_the_agents_tools", async ({ page, fake }) => {
  await fake.reset({ ...BASE, tools: null, agents: [{ name: "planner", description: "", model: "inherit", tools: ["mirror_measure", "spec_get"] }] });
  await open(page);
  await page.keyboard.press("Meta+Shift+KeyA");
  await manager(page).getByRole("button", { name: "Advanced ▸" }).click();
  await manager(page).getByRole("tab", { name: "Definitions" }).click();
  await manager(page).getByRole("button", { name: "New agent" }).click();
  await manager(page).getByRole("radio", { name: "Only these" }).check();
  await expect(manager(page).getByRole("group", { name: "Allowed tools" }).getByRole("checkbox")).toHaveCount(2);
});

test("C8_cancel_and_delete_waits_for_a_slow_stop", async ({ page, fake }) => {
  // the run takes 6.5 s to stop: longer than the old ~5 s retry window
  await fake.reset({ ...BASE, stop_delay_ms: 6500 });
  await open(page);
  await page.keyboard.press("Meta+Shift+KeyA");
  await manager(page).getByRole("button", { name: "Advanced ▸" }).click();
  await manager(page).getByRole("tab", { name: "All runs" }).click();   // the dense run tree
  const row = manager(page).locator(`[data-run-row="${RUN}"]`);
  await row.getByRole("button", { name: "Delete", exact: true }).click();
  await row.getByRole("button", { name: "Confirm delete" }).click();
  await expect(row.locator("[data-refusal=running]")).toBeVisible();
  await row.getByRole("button", { name: "Cancel and delete" }).click();
  await row.getByRole("button", { name: "Stop it and delete?" }).click();
  await expect(manager(page).getByRole("status")).toContainText("Cancelled and deleted", { timeout: 20_000 });
  await expect(row).toHaveCount(0);
  await expect(manager(page).locator("[role=alert]")).toHaveCount(0);
});

// ---- Studies page (B1)

const PARTS = read("study_parts.json");
const GLB = { beam: resolve(FX, "study_part_beam.glb"), cube: resolve(FX, "study_part_cube.glb") };
const studiesScenario = () => ({ ...BASE, parts: PARTS, glb: GLB, runs: [], steps: [], approvals: [], sims: { studies: true, step_ms: 60 } });

async function openStudies(page: Page, part = "beam") {
  await openManualStudySetup(page);
  await open(page);
  await page.getByRole("tab", { name: "Studies" }).click();
  await expect(page.getByTestId("studies")).toBeVisible();
  await page.locator("#st-part").selectOption(part);
}

test("B1_2_the_viewer_switches_to_the_result_when_the_users_study_finishes", async ({ page, fake }) => {
  await fake.reset(studiesScenario() as never);
  await openStudies(page);
  await page.getByTestId("type-modal").click();
  await expect(page.getByTestId("view-setup")).toHaveAttribute("aria-selected", "true");
  await page.getByTestId("run-study").click();
  await expect(page.getByTestId("study-result")).toHaveAttribute("data-type", "modal", { timeout: 15_000 });
  await expect(page.getByTestId("view-result")).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("view-setup")).toHaveAttribute("aria-selected", "false");
});

test("B1_3_a_two_case_static_result_names_the_governing_case_and_lists_each", async ({ page, fake }) => {
  await fake.reset(studiesScenario() as never);
  await openStudies(page);
  await page.getByTestId("type-static").click();
  await page.getByRole("button", { name: "Add load case" }).click();
  await page.getByTestId("run-study").click();
  const cases = page.getByTestId("result-cases");
  await expect(cases).toBeVisible({ timeout: 15_000 });
  await expect(cases.locator("[data-case]")).toHaveCount(2);
  await expect(page.getByTestId("governing-case")).toContainText("Governing case:");
  const gov = await cases.getAttribute("data-governing");
  expect(gov).toBeTruthy();
  await expect(cases.locator(`[data-case="${gov}"]`)).toHaveAttribute("data-governing", "");
  // pick the other case: its own figures, and a note that the contour is the governing case's
  const other = cases.locator("[data-case]:not([data-governing])");
  await other.getByRole("button").click();
  await expect(other).toHaveAttribute("data-selected", "");
  await expect(page.getByTestId("case-note")).toContainText("governing case's contour");
});

test("B1_8_enter_in_the_legend_range_applies_it", async ({ page, fake }) => {
  await fake.reset(studiesScenario() as never);
  await openStudies(page);
  await page.getByTestId("type-static").click();
  await page.getByTestId("run-study").click();
  await expect(page.getByTestId("study-result")).toHaveAttribute("data-status", "done", { timeout: 15_000 });
  await expect(page.getByRole("button", { name: "full range" })).toHaveCount(0);
  await page.getByTestId("range-hi").fill("1");
  await page.getByTestId("range-hi").press("Enter");
  await expect(page.getByTestId("range-hi")).toBeFocused();           // applied without leaving the field
  await expect(page.getByRole("button", { name: "full range" })).toBeVisible();
});

test("B1_10_a_cancelled_study_reads_cancelled_not_fail_and_offers_no_agent_fix", async ({ page, fake }) => {
  await fake.reset({ ...studiesScenario(), sims: { studies: true, step_ms: 60, outcome: "hang" } } as never);
  await openStudies(page, "cube");
  await page.getByTestId("type-wind").click();
  await page.getByTestId("run-study").click();
  await expect(page.getByTestId("study-progress")).toHaveAttribute("data-phase", "solve");
  await page.getByTestId("cancel-study").click();
  const result = page.getByTestId("study-result");
  await expect(result).toHaveAttribute("data-status", "cancelled");
  await expect(result.getByTestId("verdict")).toHaveText("cancelled");
  await expect(result.getByTestId("ask-agent")).toHaveCount(0);
});

// ---- design study (B2)

test("B2_3_open_in_studies_brings_the_design_study_into_view", async ({ page, fake }) => {
  await fake.reset(withSpec({ spec: "fixture", studies: "fixture" }) as never);
  await page.setViewportSize({ width: 1280, height: 800 });
  await open(page);
  const strip = page.locator("[data-spec-panel]");
  await strip.getByRole("button", { expanded: false }).click();
  await strip.getByRole("button", { name: "Open in Studies" }).click();
  await expect(page.getByTestId("studies")).toBeVisible();
  await expect(page.locator("[data-design-studies]")).toBeInViewport();
});

const withSpec = (spec: Record<string, unknown>) => ({ ...BASE, spec });

test("B2_1_a_study_that_finishes_without_an_event_turns_done_and_enables_use_this_variant", async ({ page, fake }) => {
  await fake.reset(withSpec({ spec: "fixture", studies: "fixture", running: true, finish_after_ms: 2500 }) as never);
  await open(page);
  await page.getByRole("tab", { name: "Studies" }).click();
  const sec = page.locator("[data-design-studies]");
  await sec.scrollIntoViewIfNeeded();
  await sec.locator(`[data-iter="${STUDIES[0].best_id}"]`).click();
  const use = sec.getByRole("button", { name: /Use this variant|Use anyway/ });
  await expect(use).toBeDisabled();
  // no event, no reload: the panel polls the running study until it is final
  await expect(use).toBeEnabled({ timeout: 10_000 });
});

test("B2_2_view_result_is_offered_only_for_kept_variants", async ({ page, fake }) => {
  await fake.reset(withSpec({ spec: "fixture", studies: "fixture" }) as never);
  await open(page);
  await page.getByRole("tab", { name: "Studies" }).click();
  const sec = page.locator("[data-design-studies]");
  await sec.scrollIntoViewIfNeeded();
  const study = STUDIES[0];
  const notKept = study.iterations.find((i) => !study.kept_variants.includes(i.metrics.variant))!;
  const kept = study.iterations.find((i) => study.kept_variants.includes(i.metrics.variant))!;
  await sec.locator(`[data-iter="${notKept.n}"]`).click();
  await expect(sec.locator("[data-variant]")).toHaveAttribute("data-variant", String(notKept.n));
  await expect(sec.getByRole("button", { name: "View result" })).toHaveCount(0);
  await sec.locator(`[data-iter="${kept.n}"]`).click();
  await expect(sec.locator("[data-variant]")).toHaveAttribute("data-variant", String(kept.n));
  await expect(sec.getByRole("button", { name: "View result" })).toBeVisible();
});

// ---- Onshape (A-7)

test("A7_the_budget_meter_shows_idle_when_onshape_is_not_connected", async ({ page, fake }) => {
  await fake.reset("base");   // onshape: not_configured
  await open(page);
  await page.keyboard.press("Meta+Digit5");   // Onshape (Plan 7 U2b renumbered the tabs)
  await page.getByRole("button", { name: /^Status/ }).click();   // the meter lives in the Status card
  const meter = page.locator(".budget");
  await expect(meter).toBeVisible();
  await expect(meter).toHaveAttribute("data-state", "idle");
  await expect(meter).toHaveAttribute("aria-label", /not connected.*0 of 2,500/);
  await expect(page.locator('[data-light="onshape"]')).toHaveAttribute("title", /Onshape not set up/);
});

// ---- builds (Empty Trash 409)

const BUILDS = JSON.parse(readFileSync(resolve(import.meta.dirname, "../fake-core/scenarios/builds.json"), "utf8")) as Record<string, unknown> & { builds: { builds: { id: string; name: string; trashed_at?: number | null }[]; open: string[]; active: string } };

test("empty_trash_refused_names_the_runs_that_block_it", async ({ page, fake }) => {
  const sc = structuredClone(BUILDS);
  sc.builds.builds = [...sc.builds.builds, { id: "t0oldbuild00", name: "Old build", trashed_at: 1_790_000_000 }];
  const runs = [...((sc.runs as AgentRun[] | undefined) ?? []), run("r_trash", { status: "running", task: "still sanding", project_id: "t0oldbuild00" })];
  await fake.reset({ ...sc, runs });
  await open(page);
  // D30: the Trash lives on Home (no builds menu any more)
  await page.getByRole("button", { name: "Home" }).click();
  const home = page.locator('[data-testid="home"]');
  await homeMenu(page, "Trash");
  await home.getByRole("button", { name: "Empty trash…" }).click();
  await home.getByRole("button", { name: "Empty trash", exact: true }).click();
  await expect(home.getByRole("alert")).toContainText("The Trash wasn't emptied: 1 run in a trashed build is still working (designer · still sanding)");
  // nothing was purged
  await expect(home.locator("[data-build]", { hasText: "Old build" })).toBeVisible();
});
