import { act, cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { AgentRun, PartSummary, Step } from "../../api/types";
import { isAutoNumbered } from "../../lib/names";
import { actions, inflight, parts, resetStore, runs, selectedPartId, selectedRunId, steps } from "../../state/store";
import allSteps from "../../../test/fixtures/core-ui/steps.json";
import allParts from "../../../test/fixtures/core-ui/parts.json";
import allRuns from "../../../test/fixtures/core-ui/runs.json";
import { openPartId } from "../assembly/focus";
import { LeftPane } from "./LeftPane";
import { assemblyOf } from "./treeActions";
import { PartTree as PartList } from "./PartTree";
import { Variables } from "./Variables";

const tokensCss = readFileSync("src/styles/tokens.css", "utf8");
const leftCss = readFileSync("src/ui/left/left.css", "utf8");
const fixtureSteps = allSteps as unknown as Step[];
const fixtureParts = allParts as unknown as PartSummary[];
const fixtureRuns = allRuns as unknown as AgentRun[];
const RUN = fixtureRuns[0].id;

function load(): void {
  parts.value = fixtureParts;
  runs.value = new Map(fixtureRuns.map((r) => [r.id, r]));
  steps.value = new Map([[RUN, fixtureSteps.filter((s) => s.run_id === RUN)]]);
}

beforeEach(() => {
  resetStore();
  const style = document.createElement("style");
  style.textContent = tokensCss + leftCss;
  document.head.appendChild(style);
});
afterEach(() => { cleanup(); document.head.replaceChildren(); openPartId.value = null; });

describe("left pane", () => {
  it("part_label_and_mass", () => {
    load();
    render(<PartList />);
    expect(screen.getByText("mount plate")).toBeTruthy();
    expect(screen.getByText("5.7 g")).toBeTruthy();
  });

  it("first_part_fallback_is_shown_but_not_selected", () => {
    load();
    expect(selectedPartId.value).toBeNull();
    render(<div><PartList /><Variables /></div>);
    const row = screen.getByRole("button", { name: "mount plate" });
    expect(row.getAttribute("aria-pressed")).toBe("false");
    expect(row.className).not.toContain("sel"); // no blue without a selection
    expect(screen.getByTestId("var-thickness")).toBeTruthy(); // its variables still show
  });

  it("variables_show_the_current_value_not_the_runs", () => {
    load();
    selectedRunId.value = RUN;
    // the part was changed again after this run's diff (newer mtime): show the current value
    parts.value = fixtureParts.map((p) => ({ ...p, vars: { ...p.vars, thickness: 7 }, mtime: 9e9 }));
    render(<Variables />);
    const v = screen.getByTestId("var-thickness");
    expect((v as HTMLInputElement).value).toBe("7");
    expect(v.getAttribute("data-changed")).toBe("true");
  });

  it("variables_show_the_runs_value_while_the_part_list_lags", () => {
    load();
    selectedRunId.value = RUN;
    // the diff step landed; the /parts refetch hasn't yet (older mtime, old value)
    parts.value = fixtureParts.map((p) => ({ ...p, vars: { ...p.vars, thickness: 5 }, mtime: 1 }));
    render(<Variables />);
    expect((screen.getByTestId("var-thickness") as HTMLInputElement).value).toBe("6");
  });

  it("left_pane_follows_the_run_the_console_shows", () => {
    load();
    // newer but finished run vs the (older) running fixture run: the console shows the running one
    const newer: AgentRun = { ...fixtureRuns[0], id: "newer0000000", task: "drill the holes", status: "done", created_at: fixtureRuns[0].created_at + 600, updated_at: fixtureRuns[0].created_at + 660 };
    runs.value = new Map([[RUN, fixtureRuns[0]], [newer.id, newer]]);
    render(<Variables />);
    expect(screen.getByTestId("var-thickness").getAttribute("data-changed")).toBe("true");
  });

  it("selecting_part_updates_store", () => {
    load();
    render(<PartList />);
    fireEvent.click(screen.getByText("mount plate"));
    expect(selectedPartId.value).toBe("mount_plate");
    expect(screen.getByRole("button", { name: "mount plate" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("changing_tag_while_inflight", () => {
    load();
    render(<PartList />);
    expect(screen.queryByText("changing")).toBeNull();
    act(() => {
      inflight.value = new Map([["c9", { run_id: RUN, name: "mirror_part_set_vars", input: { part_id: "mount_plate", vars: { thickness: 7 } } }]]);
    });
    expect(screen.getByText("changing")).toBeTruthy();
    act(() => {
      inflight.value = new Map([["c9", { run_id: RUN, name: "onshape_read", input: { part_id: "mount_plate" } }]]);
    });
    expect(screen.queryByText("changing")).toBeNull();
  });

  it("changed_value_rendered_blue", () => {
    load();
    selectedRunId.value = RUN;
    render(<Variables />);
    const v = screen.getByTestId("var-thickness");
    expect((v as HTMLInputElement).value).toBe("6");
    expect(v.getAttribute("data-changed")).toBe("true");
    // jsdom cannot resolve var(); the computed rgb(141, 186, 255) is asserted in e2e/left.spec.ts
    expect(v.className).toContain("changed");
    expect(leftCss).toMatch(/\.var-value\.changed\s*\{[^}]*color:\s*var\(--blue2\)/);
    expect(tokensCss).toMatch(/--blue2:\s*#8dbaff/);
    expect(v.getAttribute("title")).toBe("was 5.00 · set by designer · thicken mount plate to 6 mm");
  });

  it("unchanged_value_ink", () => {
    load();
    selectedRunId.value = null;
    runs.value = new Map();
    render(<Variables />);
    const v = screen.getByTestId("var-thickness");
    expect(v.getAttribute("data-changed")).toBeNull();
    expect(v.className).not.toContain("changed");
    expect(leftCss).toMatch(/\.var-value\s*\{[^}]*color:\s*var\(--ink\)/);
  });

  it("no_auto_numbered_labels", () => {
    load();
    selectedRunId.value = RUN;
    const { container } = render(<LeftPane />);
    const rowTexts = Array.from(container.querySelectorAll("[data-label]")).map((e) => e.textContent ?? "");
    expect(rowTexts.length).toBeGreaterThanOrEqual(1);
    for (const t of rowTexts) {
      const purpose = t.split(" · ").slice(1).join(" · ") || t;
      expect(isAutoNumbered(purpose), t).toBe(false);
    }
    expect(actions).toBeDefined();
  });

  it("has_parts_with_a_total_and_the_bom_no_history_no_variables", () => {
    // Plan 9 D9: Parts (mass per row, the total under it) and the BOM summary replace History
    load();
    const { container } = render(<LeftPane />);
    const titles = Array.from(container.querySelectorAll(".left-sec-title")).map((e) => e.textContent);
    expect(titles).toEqual(["Parts", "BOM"]);
    expect(screen.getByTestId("parts-total").textContent).toContain("5.7 g");
    expect(screen.queryByText("History")).toBeNull();
    expect(screen.queryByText("Parts list")).toBeNull();
    expect(container.querySelector(".left-vars, .vars")).toBeNull();
  });

  it("the_total_is_the_assemblys_once_it_loads (FW-app M7: one total, not two)", () => {
    load();
    // two instances of the 5.7 g plate: the build weighs what its assembly does
    assemblyOf.value = { instances: [{ id: "i1", part_id: "mount_plate" }, { id: "i2", part_id: "mount_plate" }], total_mass_g: 11.4, mass_unknown: [] };
    render(<LeftPane />);
    const t = screen.getByTestId("parts-total");
    expect(t.textContent).toContain("11.4 g");
    expect(t.getAttribute("title")).toMatch(/every instance/);
    assemblyOf.value = null;
  });

  it("a_plain_click_opens_the_part_and_a_modifier_click_only_selects", () => {
    load();
    render(<PartList />);
    fireEvent.click(screen.getByText("mount plate"), { metaKey: true });
    expect(openPartId.value).toBeNull();
    fireEvent.click(screen.getByText("mount plate"));
    expect(openPartId.value).toBe("mount_plate");
    // Plan 10 E7: the same row again closes it (back to the assembly)
    fireEvent.click(screen.getByText("mount plate"));
    expect(openPartId.value).toBeNull();
  });
});
