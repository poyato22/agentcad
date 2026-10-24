import { act, cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { AgentRun, PartSummary, Step } from "../../api/types";
import { isAutoNumbered } from "../../lib/names";
import { actions, focusedStepId, inflight, parts, resetStore, runs, selectedPartId, selectedRunId, steps } from "../../state/store";
import allSteps from "../../../test/fixtures/core-ui/steps.json";
import allParts from "../../../test/fixtures/core-ui/parts.json";
import allRuns from "../../../test/fixtures/core-ui/runs.json";
import { History } from "./History";
import { LeftPane } from "./LeftPane";
import { PartList } from "./PartList";
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
afterEach(() => { cleanup(); document.head.replaceChildren(); });

describe("left pane", () => {
  it("part_label_and_mass", () => {
    load();
    render(<PartList />);
    expect(screen.getByText("Part · mount plate")).toBeTruthy();
    expect(screen.getByText("5.7 g")).toBeTruthy();
  });

  it("selecting_part_updates_store", () => {
    load();
    render(<PartList />);
    fireEvent.click(screen.getByText("Part · mount plate"));
    expect(selectedPartId.value).toBe("mount_plate");
    expect(screen.getByRole("button", { name: /mount plate/ }).getAttribute("aria-pressed")).toBe("true");
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
    expect(v.textContent).toBe("6.00 mm");
    expect(v.getAttribute("data-changed")).toBe("true");
    // jsdom cannot resolve var(); the computed rgb(141, 186, 255) is asserted in e2e/left.spec.ts
    expect(v.className).toContain("changed");
    expect(leftCss).toMatch(/\.var-value\.changed\s*\{[^}]*color:\s*var\(--blue2\)/);
    expect(tokensCss).toMatch(/--blue2:\s*#8dbaff/);
    expect(v.getAttribute("title")).toBe("was 5.00 · set by designer · thicken mount plate to");
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

  it("history_newest_first_and_click_focuses_step", () => {
    load();
    const older: AgentRun = { ...fixtureRuns[0], id: "older0000000", task: "drill the mounting holes", status: "done", created_at: fixtureRuns[0].created_at - 600, updated_at: fixtureRuns[0].created_at - 540 };
    runs.value = new Map([[older.id, older], [RUN, fixtureRuns[0]]]);
    render(<History />);
    const rows = screen.getAllByTestId("run-row");
    expect(rows.map((r) => r.getAttribute("data-run"))).toEqual([RUN, "older0000000"]);
    expect(rows[0].querySelector("[data-tone]")?.getAttribute("data-tone")).toBe("blue");
    expect(rows[1].querySelector("[data-tone]")?.getAttribute("data-tone")).toBe("green");
    fireEvent.click(rows[0].querySelector("button") as HTMLElement);
    const step = screen.getByText(/^v-step: thickness 5→6 · \+0\.96 g$/);
    fireEvent.click(step);
    expect(selectedRunId.value).toBe(RUN);
    expect(focusedStepId.value).toBe(7);
  });

  it("no_auto_numbered_labels", () => {
    load();
    selectedRunId.value = RUN;
    const { container } = render(<LeftPane />);
    fireEvent.click(container.querySelector("[data-testid=run-row] button") as HTMLElement);
    const rowTexts = Array.from(container.querySelectorAll("[data-label]")).map((e) => e.textContent ?? "");
    expect(rowTexts.length).toBeGreaterThanOrEqual(2);
    for (const t of rowTexts) {
      const purpose = t.split(" · ").slice(1).join(" · ") || t;
      expect(isAutoNumbered(purpose), t).toBe(false);
    }
    expect(actions).toBeDefined();
  });
});
