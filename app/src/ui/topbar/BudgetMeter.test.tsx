import { act, cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import type { OnshapeBudget } from "../../api/directory";
import type { CoreStatusInfo, MisesEvent } from "../../api/types";
import { applyBudget, parseBudgetEvent, resetOnshapeBudget } from "../../state/onshapeBudget";
import { coreStatus, resetStore, selectedRunId } from "../../state/store";
import { activePage, setPage } from "../pages/router";
import { BudgetMeter, fmtResetDate } from "./BudgetMeter";
import { onshapeTone } from "./StatusLights";

const FX = resolve(import.meta.dirname, "../../../test/fixtures/core-ui");
const BUDGET = JSON.parse(readFileSync(resolve(FX, "onshape_budget.json"), "utf8")) as OnshapeBudget;
const status = (onshape: CoreStatusInfo["onshape"] = "connected"): CoreStatusInfo => ({
  core_version: "0", onshape, onshape_calls_year: 5, onshape_cap_year: 2500, runs_running: 0, approvals_pending: 0,
});
const ev = (data: Partial<OnshapeBudget>, runId: string | null = null): MisesEvent =>
  ({ type: "onshape_budget", run_id: runId, data: { ...BUDGET, ...data, run_id: runId }, ts: 1 });
const feed = (e: MisesEvent) => act(() => { const g = parseBudgetEvent(e); if (g) applyBudget(g.budget, g.runId); });

const meter = () => document.querySelector(".budget") as HTMLElement;
const used = () => document.querySelector("[data-budget-used]")?.textContent;

afterEach(() => { cleanup(); resetOnshapeBudget(); resetStore(); setPage("design"); });

describe("BudgetMeter", () => {
  it("shows used / limit for the year and the session delta", () => {
    coreStatus.value = status();
    applyBudget(BUDGET, null);
    render(<BudgetMeter />);
    expect(used()).toBe("5");
    expect(meter().textContent).toContain("/2,500");
    expect(document.querySelector("[data-budget-delta]")?.textContent).toBe("+3");
    expect(meter().dataset.level).toBe("ok");
  });

  it("updates live from onshape_budget events, with a 'just moved' flash", async () => {
    coreStatus.value = status();
    applyBudget(BUDGET, null);
    render(<BudgetMeter />);
    feed(ev({ used_year: 6, used_run: 4 }));
    expect(used()).toBe("6");
    expect(document.querySelector("[data-budget-delta]")?.textContent).toBe("+4");
    expect(document.querySelector("[data-budget-used]")?.classList.contains("budget-moved")).toBe(true);
    // an agent's call moves the year count too (its own run count is kept apart)
    feed(ev({ used_year: 7, used_run: 1 }, "run-a"));
    expect(used()).toBe("7");
    expect(document.querySelector("[data-budget-delta]")?.textContent).toBe("+4");
    act(() => { selectedRunId.value = "run-a"; });
    expect(document.querySelector("[data-budget-delta]")?.textContent).toBe("+1");
  });

  it("turns amber at warn_at and red at the limit", () => {
    coreStatus.value = status();
    applyBudget({ ...BUDGET, used_year: 1999 }, null);
    render(<BudgetMeter />);
    expect(meter().dataset.level).toBe("ok");
    feed(ev({ used_year: 2000 }));
    expect(meter().dataset.level).toBe("warn");
    feed(ev({ used_year: 2500 }));
    expect(meter().dataset.level).toBe("limit");
    expect(screen.getByRole("group").getAttribute("aria-label")).toMatch(/Limit reached/);
  });

  it("shows the reset date and what is left; its button opens the Onshape page (U2b: it lives in the Status card)", () => {
    coreStatus.value = status();
    applyBudget(BUDGET, null);
    let opened = 0;
    render(<BudgetMeter onOpen={() => { opened++; }} />);
    expect(meter().querySelector("[data-budget-reset]")?.textContent).toBe(fmtResetDate(BUDGET.reset_at));
    expect(fmtResetDate(BUDGET.reset_at)).toMatch(/^1 Jan \d{4}$/);
    expect(meter().textContent).toContain("Left2,495");
    fireEvent.click(screen.getByRole("button", { name: "Open the Onshape page" }));
    expect(activePage.value).toBe("onshape");
    expect(opened).toBe(1);
  });

  it("shows idle when Onshape isn't set up (the connect card points at it, A-7), hidden while nothing is known", () => {
    coreStatus.value = status("not_configured");
    applyBudget({ ...BUDGET, used_year: 0 }, null);
    render(<BudgetMeter />);
    expect(meter().dataset.state).toBe("idle");
    expect(meter().querySelector("[data-budget-delta]")).toBeNull();
    expect(screen.getByRole("group").getAttribute("aria-label")).toContain("Onshape budget (not connected): 0 of 2,500 calls used this year");
    expect(screen.getByRole("group").getAttribute("aria-label")).toContain("add your Onshape keys to connect it.");
    expect(onshapeTone(status("not_configured")).title).toBe("Onshape not set up: add your Onshape keys to connect it.");
    cleanup();
    resetOnshapeBudget();
    coreStatus.value = status();
    render(<BudgetMeter />);
    expect(meter()).toBeNull();
  });

  it("the ONSHAPE light keeps its tone from the live figures but leaves the count to the meter", () => {
    expect(onshapeTone(status()).text).toBe("connected · 5/2,500");
    expect(onshapeTone(status(), { ...BUDGET, used_year: 2100 })).toEqual({
      tone: "amber", text: "connected", title: "Onshape connected: 2,100 of 2,500 calls used this year" });
  });

  it("with no run selected and no browsing yet, the delta is 0, never another run's count", () => {
    coreStatus.value = status();
    applyBudget({ ...BUDGET, used_run: 9 }, "agent-run");
    render(<BudgetMeter />);
    expect(document.querySelector("[data-budget-delta]")?.textContent).toBe("+0");
  });
});
