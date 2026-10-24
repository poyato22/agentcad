import { expect, test } from "./fixtures";
import type { PartSummary, Step } from "../../src/api/types";
import { readFileSync } from "node:fs";

const RUN = "b7c79a57cbb4";

test.beforeEach(async ({ fake }) => { await fake.reset("left"); });

test("left_pane_shows_parts_variables_history", async ({ page }) => {
  await page.goto("/");
  const left = page.locator("[data-region=left]");
  await expect(left.getByText("Part · mount plate")).toBeVisible();
  await expect(left.getByText("4.8 g")).toBeVisible();
  const v = page.getByTestId("var-thickness");
  await expect(v).toHaveText("5.00 mm");
  await expect(v).not.toHaveAttribute("data-changed", "true");
  await expect(v).toHaveCSS("color", "rgb(236, 236, 236)");
  const rows = page.getByTestId("run-row");
  await expect(rows).toHaveCount(2);
  await expect(rows.first()).toContainText("designer · thicken mount plate to");
  await expect(rows.nth(1)).toContainText("designer · drill mounting holes");
});

test("vars_turn_blue_after_set_vars_event", async ({ page, fake }) => {
  await page.goto("/");
  const v = page.getByTestId("var-thickness");
  await expect(v).toHaveText("5.00 mm");
  const bundle = JSON.parse(readFileSync("test/fixtures/core-ui/parts.json", "utf8")) as PartSummary[];
  const diffOutput = {
    data: { part_id: "mount_plate", vars: { thickness: 6 }, delta: { mass_g: 0.96, volume_mm3: 772, bbox_size: [0, 0, 1] } },
    ok: true, summary: "mount_plate: thickness 6", handles: [], warnings: [], step_kind: "diff",
  };
  const steps: Partial<Step>[] = [
    { run_id: RUN, kind: "tool_call", payload: { call_id: "c2", name: "mirror_part_set_vars", input: { part_id: "mount_plate", vars: { thickness: 6 } } } },
    { run_id: RUN, kind: "diff", payload: { call_id: "c2", name: "mirror_part_set_vars", output: diffOutput } },
  ];
  await fake.emit({ type: "tool_call", run_id: RUN, data: { call_id: "c2", name: "mirror_part_set_vars", input: { part_id: "mount_plate", vars: { thickness: 6 } } } }, { steps, parts: bundle });
  await expect(v).toHaveText("6.00 mm");
  await expect(v).toHaveAttribute("data-changed", "true");
  await expect(v).toHaveCSS("color", "rgb(141, 186, 255)");
  await expect(v).toHaveAttribute("title", "was 5.00 · set by designer · thicken mount plate to");
});

test("history_row_expands", async ({ page }) => {
  await page.goto("/");
  const first = page.getByTestId("run-row").first().getByRole("button").first();
  await first.click();
  await expect(first).toHaveAttribute("aria-expanded", "true");
});
