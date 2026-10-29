import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { StreamState } from "../api/events";
import type { OnshapeBudget } from "../api/directory";
import type { MisesEvent } from "../api/types";
import {
  applyBudget, budgetLevel, onshapeBudget, onshapeBudgetTick, onshapeRunUsed, parseBudgetEvent, resetOnshapeBudget, startOnshapeBudget,
} from "./onshapeBudget";
import { resetStore, selectedRunId } from "./store";

const FX = resolve(import.meta.dirname, "../../test/fixtures/core-ui");
/** The real core's `onshape_budget` events (browsing, then an import on the app's ui run). */
const EVENTS = readFileSync(resolve(FX, "onshape_events.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l) as MisesEvent);
const BUDGET = JSON.parse(readFileSync(resolve(FX, "onshape_budget.json"), "utf8")) as OnshapeBudget;

const snap = (over: Partial<OnshapeBudget> = {}): OnshapeBudget => ({ ...BUDGET, ...over });
const flush = () => new Promise((r) => setTimeout(r, 0));

afterEach(() => { resetOnshapeBudget(); resetStore(); });

describe("budgetLevel", () => {
  it("is ok below warn_at, warn from warn_at, limit at the yearly limit", () => {
    expect(budgetLevel({ used_year: 1999, warn_at: 2000, limit_year: 2500 })).toBe("ok");
    expect(budgetLevel({ used_year: 2000, warn_at: 2000, limit_year: 2500 })).toBe("warn");
    expect(budgetLevel({ used_year: 2499, warn_at: 2000, limit_year: 2500 })).toBe("warn");
    expect(budgetLevel({ used_year: 2500, warn_at: 2000, limit_year: 2500 })).toBe("limit");
    expect(budgetLevel({ used_year: 0, warn_at: 0, limit_year: 0 })).toBe("limit");
  });
});

describe("live budget", () => {
  it("parses the real core's onshape_budget events and ignores others", () => {
    const got = parseBudgetEvent(EVENTS[0]);
    expect(got?.budget.used_year).toBe(1);
    expect(got?.runId).toBeNull();
    expect(parseBudgetEvent({ type: "run_started", run_id: null, data: {}, ts: 1 })).toBeNull();
    expect(parseBudgetEvent({ type: "onshape_budget", run_id: null, data: { used_year: "x" }, ts: 1 })).toBeNull();
  });

  it("every event moves the year count, keeps per-run counts and bumps the tick", () => {
    const listeners: ((e: MisesEvent) => void)[] = [];
    const stop = startOnshapeBudget({
      subscribe: (cb) => { listeners.push(cb); return () => {}; }, subscribeState: () => () => {},
      fetch: vi.fn(async () => snap()),
    });
    for (const ev of EVENTS) listeners[0](ev);
    const last = EVENTS[EVENTS.length - 1];
    expect(onshapeBudget.value?.used_year).toBe((last.data as { used_year: number }).used_year);
    expect(onshapeRunUsed.value.get("")).toBe(3);                    // the app's own browsing
    expect(onshapeRunUsed.value.get(last.run_id as string)).toBe(2); // the import on the ui run
    expect(onshapeBudgetTick.value).toBe(EVENTS.length - 1);         // the first one only set it
    stop();
  });

  it("an older snapshot never moves the year count back (same year), a new year does", () => {
    applyBudget(snap({ used_year: 10, used_run: 2 }), null);
    applyBudget(snap({ used_year: 8, used_run: 1 }), null);
    expect(onshapeBudget.value?.used_year).toBe(10);
    applyBudget(snap({ used_year: 0, reset_at: BUDGET.reset_at + 365 * 86400 }), null);
    expect(onshapeBudget.value?.used_year).toBe(0);
  });

  it("reads GET /onshape/budget on every (re)open and for a newly selected run", async () => {
    let state: ((s: StreamState) => void) | null = null;
    const fetch = vi.fn(async (runId: string | null) => snap({ used_run: runId ? 7 : 3 }));
    const stop = startOnshapeBudget({ subscribe: () => () => {}, subscribeState: (cb) => { state = cb; return () => {}; }, fetch });
    state!("open");
    await flush();
    expect(fetch).toHaveBeenCalledWith(null);
    expect(onshapeRunUsed.value.get("")).toBe(3);
    selectedRunId.value = "r1";
    await flush();
    expect(fetch).toHaveBeenCalledWith("r1");
    expect(onshapeRunUsed.value.get("r1")).toBe(7);
    state!("reconnecting");
    state!("open");
    await flush();
    expect(fetch.mock.calls.filter(([r]) => r === null)).toHaveLength(2);
    stop();
  });
});
