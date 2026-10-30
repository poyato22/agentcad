// The final review's app fixes (final-678-app.md) that have no better home: the layout skeleton's
// row (I4b) and the "All parts" toggle's place (I3).
import type { PartSummary } from "../../src/api/types";
import { expect, test } from "./fixtures";
import { CORE_URL } from "./ports";

test.beforeEach(async ({ fake, page }) => {
  await fake.reset("base");
  await page.setViewportSize({ width: 1440, height: 900 });
});

test("the skeleton row shows its parameters and datums, never a GLB error (I4b)", async ({ page, fake }) => {
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  const parts = await (await fetch(`${CORE_URL}/parts`, { headers: { Authorization: `Bearer ${fake.token}` } })).json() as PartSummary[];
  const skel: PartSummary = { ...parts[0], part_id: "skeleton_b1", label: "Skeleton", kind: "skeleton", metrics: null, version: 2 };
  const run = { id: "sk01", parent_id: null, agent: "engineer", provider: "fake", model: "m", task: "lay out the arm", status: "done" as const,
    branch: null, onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0, created_at: Date.now() / 1000, updated_at: Date.now() / 1000 };
  await fake.emit({ type: "tool_result", run_id: "sk01", data: { call_id: "c1", name: "skeleton_set", ok: true } }, {
    run,
    parts: [...parts, skel],
    steps: [{ kind: "tool_result", payload: { name: "skeleton_set", call_id: "c1", output: { ok: true, summary: "skeleton v2", data: {
      project_id: "b1", skeleton_id: "skeleton_b1", version: 2,
      skeleton: { params: { arm_length: 120, wall: 2.5 }, datums: { base_plane: { kind: "plane", origin: [0, 0, 0], normal: [0, 0, 1] } },
        envelope: { min: [0, 0, 0], max: [150, 40, 30] }, curves: {} } } } } }],
  });
  await fake.emit({ type: "part_changed", run_id: "sk01", data: { part_id: "skeleton_b1", version: 2, by: "agent" } });
  const row = page.locator('[data-part="skeleton_b1"]');
  await expect(row).toBeVisible();
  // no eye or swatch on a row with no model
  await expect(row.getByRole("button", { name: /in the viewer/ })).toHaveCount(0);
  await row.locator(".tree-name").click();
  const panel = page.getByTestId("skeleton-panel");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("arm_length");
  await expect(panel).toContainText("120");
  await expect(panel).toContainText("base_plane");
  await expect(panel).toContainText("150 × 40 × 30 mm");
  await expect(page.getByText(/GLB failed/)).toHaveCount(0);
  const glb = (await fake.calls()).filter((c) => c.path === "/parts/skeleton_b1/model.glb");
  expect(glb).toHaveLength(0);
});

test("All parts sits in the viewer's tool row, not over a panel, and Assembly view doesn't offer it (I3)", async ({ page }) => {
  await page.goto("/");
  const toggle = page.getByRole("button", { name: "All parts" });
  const view = page.getByRole("button", { name: "View options" });
  await expect(toggle).toBeVisible();
  const t = (await toggle.boundingBox())!;
  const v = (await view.boundingBox())!;
  expect(Math.abs((t.y + t.height / 2) - (v.y + v.height / 2))).toBeLessThan(2);   // one row
  expect(t.x + t.width).toBeLessThanOrEqual(v.x);                                    // left of View ▾
  await page.getByRole("radio", { name: "Assembly" }).click();
  await expect(page.getByRole("button", { name: "All parts" })).toHaveCount(0);
});
