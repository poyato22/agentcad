// The agent dock (Plan 9 D2 / D4): three streams visible at once over the real core's steps, each
// collapsible (with an unread count) and resizable; the composer's send button turns into Stop.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../api/client";
import { stepSchema } from "../../api/schemas";
import type { AgentRun, Step } from "../../api/types";
import { recordActivity, resetActivity } from "../../state/activity";
import { bindClient, resetStore, runs, selectedRunId, steps } from "../../state/store";
import { resetCatalog } from "../dialogs/catalog";
import { ConsolePane } from "./ConsolePane";
import { resetOffListRuns } from "./derived";
import { DEFAULT_LAYOUT, dockLayout, LAYOUT_KEY, resetDockLayout } from "./dockLayout";
import { message, resetIds, run, step, toolCall } from "./testSteps";
import { resetUnread } from "./unread";
import { resetConsoleView } from "./view";

const FX = resolve(__dirname, "../../../../core/tests/fixtures/ui");
const APP_FX = resolve(__dirname, "../../../test/fixtures/core-ui");
const parse = (path: string): Step[] => (JSON.parse(readFileSync(path, "utf8")) as unknown[]).map((s) => stepSchema.parse(s));
const TEXT = parse(resolve(APP_FX, "steps.json"));
const RID = TEXT[0].run_id;
const STUDY = parse(resolve(FX, "study_agent_steps.json")).map((s) => ({ ...s, run_id: RID, id: s.id + 10_000 }));
const G1 = JSON.parse(readFileSync(resolve(APP_FX, "g1_cards.json"), "utf8")) as Record<string, Record<string, unknown>>;
const view = (id: number, ts: number, caption: string): Step => {
  const card = structuredClone(G1.view_presented) as { ref: { view_spec: { caption: string } } };
  card.ref.view_spec.caption = caption;
  return step(RID, "decision", { agent_announce: card }, ts, { id });
};

async function frame() { await act(async () => { await new Promise((r) => setTimeout(r, 40)); }); }

function mount(list: Step[], over: Partial<AgentRun> = {}) {
  const c = {
    agents: vi.fn().mockResolvedValue([]), models: vi.fn().mockResolvedValue({ default: "x/y", vendors: [] }),
    localStatus: vi.fn().mockRejectedValue(new Error("404")), getProfile: vi.fn().mockResolvedValue({}),
    cancel: vi.fn().mockResolvedValue({ ok: true }), renderImage: vi.fn().mockRejectedValue(new Error("none")),
    partThumb: vi.fn().mockRejectedValue(new Error("none")), message: vi.fn().mockResolvedValue({ ok: true }),
  };
  bindClient(c as unknown as CoreClient);
  const r = run(RID, { role: "lead", project_id: null, ...over });
  runs.value = new Map([[r.id, r]]);
  const m = new Map<string, Step[]>();
  m.set(RID, [...list].sort((a, b) => a.id - b.id));
  steps.value = m;
  selectedRunId.value = RID;
  return { c, ...render(<ConsolePane />) };
}

const section = (s: string) => document.querySelector(`[data-dock] [data-stream=${s}]`) as HTMLElement;

beforeEach(() => { resetIds(70_000); resetConsoleView(); try { localStorage.removeItem(LAYOUT_KEY); } catch { /* none */ } resetDockLayout(); resetUnread(); });
afterEach(() => { cleanup(); resetStore(); resetCatalog(); resetConsoleView(); resetOffListRuns(); resetActivity(); dockLayout.value = DEFAULT_LAYOUT; });

describe("agent dock: three streams at once", () => {
  it("shows views, studies and the conversation together, each in its own scroller", async () => {
    mount([...TEXT, ...STUDY, view(90_001, 2e9, "the lid"), view(90_002, 2e9 + 1, "the lid, closer")]);
    await frame();
    for (const [s, title] of [["view", "View"], ["sim", "Simulations"], ["text", "Conversation"]]) {
      const el = section(s);
      const t = el.querySelector(".dock-t") as HTMLElement;
      expect(t.textContent).toContain(title);
      expect(t.getAttribute("aria-expanded")).toBe("true");
      expect(el.querySelector(`[data-stream-body=${s}]`)).toBeTruthy();
    }
    // View: the newest large, the earlier one in the filmstrip
    const v = section("view");
    expect(v.querySelectorAll("[data-testid=view-card]").length).toBe(1);
    expect(v.querySelector("[data-testid=view-card]")?.textContent).toContain("the lid, closer");
    const tiles = within(v.querySelector("[data-filmstrip]") as HTMLElement).getAllByRole("button");
    expect(tiles.map((t) => t.getAttribute("aria-current"))).toEqual([null, "true"]);
    fireEvent.click(tiles[0]);
    expect(v.querySelector("[data-testid=view-card]")?.textContent).toContain("the lid");
    expect(v.querySelector("[data-testid=view-card]")?.textContent).not.toContain("closer");
    // Simulations: one card per study
    const sims = section("sim");
    expect(sims.querySelectorAll("[data-study-slot]").length).toBe(STUDY.filter((x) => x.kind === "tool_call").length);
    // Conversation: the words, no study or view card in it
    const conv = within(section("text")).getByRole("log", { name: "Conversation" });
    expect(conv.querySelector("[data-msg=user]")).toBeTruthy();
    expect(conv.querySelector("[data-study-slot], [data-testid=view-card]")).toBeNull();
  });

  it("an empty stream shrinks to one quiet line instead of a blank box", async () => {
    mount(TEXT);
    await frame();
    expect(section("view").querySelector("[data-stream-empty]")?.textContent).toContain("Views and renders");
    expect(section("view").hasAttribute("data-sized")).toBe(false);
    expect(document.querySelector("[data-divider=view]")).toBeNull();
  });

  it("a collapsed stream keeps its header and counts what arrives while it is closed; the choice is remembered", async () => {
    mount([...TEXT, ...STUDY]);
    await frame();
    const toggle = within(section("sim")).getByRole("button", { name: /^Simulations/ });
    fireEvent.click(toggle);
    await frame();
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(section("sim").querySelector("[data-stream-body]")).toBeNull();
    expect(JSON.parse(localStorage.getItem(LAYOUT_KEY) as string).collapsed.sim).toBe(true);
    // two more studies land
    act(() => {
      steps.value = new Map([[RID, [...(steps.value.get(RID) ?? []), toolCall(RID, "n1", "sim_run", { type: "static" }, 3e9), toolCall(RID, "n2", "sim_run", { type: "modal" }, 3e9 + 1)]]]);
    });
    await frame();
    expect(section("sim").querySelector("[data-unread]")?.textContent).toBe("2");
    fireEvent.click(toggle);
    await frame();
    expect(section("sim").querySelector("[data-unread]")).toBeNull();
    // the running count in the header (an agent working: blue, data-changing)
    expect(section("sim").querySelector("[data-running]")?.getAttribute("data-running")).toBe("2");
  });

  it("a divider drags the stream above it and saves at the end of the drag", async () => {
    mount([...TEXT, ...STUDY, view(90_001, 2e9, "the lid")]);
    await frame();
    const div = document.querySelector("[data-divider=view]") as HTMLElement;
    expect(section("view").style.height).toBe(`${DEFAULT_LAYOUT.view}px`);
    fireEvent.keyDown(div, { key: "ArrowDown" });
    expect(dockLayout.value.view).toBeGreaterThan(DEFAULT_LAYOUT.view);
    expect(JSON.parse(localStorage.getItem(LAYOUT_KEY) as string).view).toBe(dockLayout.value.view);
  });
});

describe("composer: send turns into Stop while the agents work (Plan 9 D2)", () => {
  it("Stop and Esc stop the busy lead; Send is back once its turn ends", async () => {
    const { c } = mount([message(RID, "user", "make the lid", 1)]);
    recordActivity({ type: "agent_activity", run_id: RID, ts: 0, data: { state: "thinking", detail: "", since: 1 } });
    await frame();
    const stop = screen.getByRole("button", { name: "Stop" });
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
    await act(async () => { fireEvent.click(stop); });
    expect(c.cancel).toHaveBeenCalledWith(RID);
    c.cancel.mockClear();
    const box = screen.getByRole("textbox", { name: "Command line" });
    await act(async () => { fireEvent.keyDown(box, { key: "Escape" }); });
    expect(c.cancel).toHaveBeenCalledWith(RID);
    recordActivity({ type: "agent_activity", run_id: RID, ts: 0, data: { state: "idle", detail: "", since: 2 } });
    await frame();
    c.cancel.mockClear();
    const send = screen.getByRole("button", { name: "Send" });
    expect(send.hasAttribute("disabled")).toBe(true);               // nothing typed yet
    await act(async () => { fireEvent.keyDown(box, { key: "Escape" }); });
    expect(c.cancel).not.toHaveBeenCalled();                        // Esc stops only a busy run
  });
});
