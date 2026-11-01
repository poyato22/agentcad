// Plan 11 F6: rocket cards in the Simulations stream, over the real core's steps
// (core scripts/dump_rocket_fixtures.py: rocket_analyze on F52C and rocket_flight on F52C + G40W
// against a 750 ft / 36-39 s target with tune_mass, through the real Gate).
import { act, cleanup, fireEvent, render, within } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../../api/client";
import { stepSchema } from "../../../api/schemas";
import type { Step } from "../../../api/types";
import { resetActivity } from "../../../state/activity";
import { bindClient, resetStore, runs, selectedRunId, steps } from "../../../state/store";
import { resetCatalog } from "../../dialogs/catalog";
import { ConsolePane } from "../ConsolePane";
import { resetOffListRuns } from "../derived";
import { DEFAULT_LAYOUT, dockLayout, LAYOUT_KEY, resetDockLayout, setDockLayout } from "../dockLayout";
import type { LogItem } from "../logModel";
import { toolStream } from "../streams";
import { resetIds, run, toolCall } from "../testSteps";
import { resetUnread } from "../unread";
import { resetConsoleView } from "../view";
import { fmtMetric, isRocketCallName, rocketCallView } from "./model";

const FX = resolve(import.meta.dirname, "../../../../../core/tests/fixtures/ui/p11/rocket_steps.json");
const STEPS: Step[] = (JSON.parse(readFileSync(FX, "utf8")) as unknown[]).map((s) => stepSchema.parse(s));
const RID = STEPS[0].run_id;
type Call = Extract<LogItem, { kind: "call" }>;

function call(name: string): Call {
  const c = STEPS.find((s) => s.kind === "tool_call" && s.payload.name === name) as Step;
  const r = STEPS.find((s) => s.kind !== "tool_call" && s.payload.call_id === c.payload.call_id) as Step;
  const out = r.payload.output as { summary: string };
  return { kind: "call", callId: c.payload.call_id as string, name, input: c.payload.input as Record<string, unknown>, result: r,
    ok: true, summary: out.summary, startedTs: c.ts, endedTs: r.ts, hint: null, attached: [] } as Call;
}

describe("rocket card model (real core payloads)", () => {
  it("reads rocket_analyze: verdict, margin metrics, the burn chart with its 1-2 cal band, terms and flutter", () => {
    const v = rocketCallView(call("rocket_analyze"));
    expect(v).not.toBeNull();
    if (!v) return;
    expect(v.tool).toBe("rocket_analyze");
    expect(v.status).toBe("pass");
    const margin = v.metrics.find((m) => m.label === "margin at launch");
    expect(margin?.status).toBe("pass");
    expect(Number(margin?.value)).toBeGreaterThan(1.7);
    expect(fmtMetric(margin!)).toMatch(/cal$/);
    expect(v.charts[0].id).toBe("margin");
    expect(v.charts[0].bands?.[0]).toMatchObject({ axis: "y", from: 1, to: 2 });
    expect(v.terms.map((t) => t.term)).toEqual(["nose (ogive)", "fins (3)"]);
    expect(v.flutter[0].status).toBe("pass");
    expect(v.accuracy.length).toBeGreaterThan(0);
  });

  it("reads rocket_flight: one row per motor, the tune note, three charts", () => {
    const v = rocketCallView(call("rocket_flight"));
    if (!v) throw new Error("no view");
    expect(v.flights.map((f) => f.motor)).toEqual(["F52C", "G40W"]);
    expect(v.flights[0].apogee_m).toBeGreaterThan(300);
    expect(v.flights[0].tune?.reachable).toBe(true);
    expect(v.charts.map((c) => c.id)).toEqual(["altitude", "velocity", "acceleration"]);
    expect(v.charts[0].series?.map((s) => s.name)).toEqual(["F52C", "G40W"]);
  });

  it("a running call names its motors; other tools are not rocket cards", () => {
    const c = { ...call("rocket_flight"), result: null, ok: null, summary: null };
    const v = rocketCallView(c);
    expect(v?.running).toBe(true);
    expect(v?.motors).toEqual(["F52C", "G40W"]);
    expect(isRocketCallName("mcp__mises__rocket_analyze")).toBe(true);
    expect(isRocketCallName("rocket_motor")).toBe(false);
    expect(toolStream("mcp__mises__rocket_flight")).toBe("sim");
    expect(toolStream("rocket_motor")).toBe("text");
  });
});

async function frame() { await act(async () => { await new Promise((r) => setTimeout(r, 40)); }); }
const section = (s: string) => document.querySelector(`[data-dock] [data-stream=${s}]`) as HTMLElement;

describe("rocket cards in the dock", () => {
  beforeEach(() => {
    resetIds(80_000); resetConsoleView(); try { localStorage.removeItem(LAYOUT_KEY); } catch { /* none */ }
    resetDockLayout(); resetUnread();
    setDockLayout({ ...DEFAULT_LAYOUT, active: "sim" }, false);         // G1: the Simulations tab shown
  });
  afterEach(() => { cleanup(); resetStore(); resetCatalog(); resetConsoleView(); resetOffListRuns(); resetActivity(); dockLayout.value = DEFAULT_LAYOUT; });

  function mount(list: Step[]) {
    const c = {
      agents: vi.fn().mockResolvedValue([]), models: vi.fn().mockResolvedValue({ default: "x/y", vendors: [] }),
      localStatus: vi.fn().mockRejectedValue(new Error("404")), getProfile: vi.fn().mockResolvedValue({}),
      cancel: vi.fn().mockResolvedValue({ ok: true }), renderImage: vi.fn().mockRejectedValue(new Error("none")),
      partThumb: vi.fn().mockRejectedValue(new Error("none")), message: vi.fn().mockResolvedValue({ ok: true }),
    };
    bindClient(c as unknown as CoreClient);
    const r = run(RID, { role: "lead", project_id: null });
    runs.value = new Map([[r.id, r]]);
    steps.value = new Map([[RID, list]]);
    selectedRunId.value = RID;
    return render(<ConsolePane />);
  }

  it("shows both results as cards in Simulations, none in the Conversation", async () => {
    mount(STEPS);
    await frame();
    const sims = section("sim");
    const cards = sims.querySelectorAll("[data-testid=rocket-card]");
    expect([...cards].map((c) => c.getAttribute("data-rocket-card")).sort()).toEqual(["rocket_analyze", "rocket_flight"]);
    expect(section("text").querySelector("[data-testid=rocket-card]")).toBeNull();
    const flight = sims.querySelector("[data-rocket-card=rocket_flight]") as HTMLElement;
    const rows = within(flight).getByTestId("rocket-flights").querySelectorAll("tbody tr");
    expect([...rows].map((r) => r.getAttribute("data-motor"))).toEqual(["F52C", "G40W"]);
    expect(flight.querySelectorAll("[data-testid=rocket-chart]").length).toBe(3);
    const tunes = within(flight).getAllByTestId("rocket-tune").map((t) => t.textContent ?? "");
    expect(tunes[0]).toMatch(/^F52C: \+[\d.]+ g ballast hits the target \(margin [\d.]+ cal\)/);
    expect(tunes.some((t) => /chute lands it in the time window/.test(t))).toBe(true);
    // the analyze card opens its Barrowman terms and flutter on demand
    const an = sims.querySelector("[data-rocket-card=rocket_analyze]") as HTMLElement;
    expect(an.querySelector("[data-testid=rocket-terms]")).toBeNull();
    fireEvent.click(within(an).getByRole("button", { name: /Barrowman terms/ }));
    expect(an.querySelector("[data-testid=rocket-terms]")?.textContent).toContain("nose (ogive)");
    expect(an.querySelector("[data-testid=rocket-flutter] [data-status=pass]")).toBeTruthy();
    expect(an.querySelector("[data-label='margin at launch']")?.getAttribute("data-status")).toBe("pass");
  });

  it("a running rocket_flight is a running card in Simulations", async () => {
    const c = toolCall(RID, "rk9", "mcp__mises__rocket_flight", { motors: ["G40W"] }, 3e9);
    mount([c]);
    await frame();
    const card = section("sim").querySelector("[data-testid=rocket-card]") as HTMLElement;
    expect(card.getAttribute("data-status")).toBe("running");
    expect(card.textContent).toContain("Flying on G40W");
  });
});
