// The agent dock (Plan 9 D2 / D4, G1 layout): View · Simulations on top (one at a time), the Conversation under
// collapsible (with an unread count) and resizable; the composer's send button turns into Stop.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, onTestFinished, vi } from "vitest";
import type { CoreClient } from "../../api/client";
import type { BackgroundJob } from "../../api/jobs";
import { stepSchema } from "../../api/schemas";
import type { AgentRun, Step } from "../../api/types";
import { recordActivity, resetActivity } from "../../state/activity";
import { activeBuildId } from "../../state/builds";
import { liveJobs, onJobEvent, resetJobs } from "../../state/jobs";
import { bindClient, resetStore, runs, selectedRunId, steps } from "../../state/store";
import { resetCatalog } from "../dialogs/catalog";
import { ConsolePane } from "./ConsolePane";
import { resetOffListRuns } from "./derived";
import { DEFAULT_LAYOUT, dockLayout, LAYOUT_KEY, resetDockLayout, setDockLayout } from "./dockLayout";
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
const tab = (s: "view" | "sim") => document.querySelector(`[data-dock] [data-stream-tab=${s}]`) as HTMLElement;

beforeEach(() => { resetIds(70_000); resetConsoleView(); try { localStorage.removeItem(LAYOUT_KEY); } catch { /* none */ } resetDockLayout(); resetUnread();
  setDockLayout(DEFAULT_LAYOUT, false); });
afterEach(() => { cleanup(); resetStore(); resetCatalog(); resetConsoleView(); resetOffListRuns(); resetActivity(); dockLayout.value = DEFAULT_LAYOUT; });

describe("agent dock: View · Simulations on top, the Conversation under it (G1)", () => {
  it("shows views, studies and the conversation, each in its own scroller; the switcher picks View or Simulations", async () => {
    mount([...TEXT, ...STUDY, view(90_001, 2e9, "the lid"), view(90_002, 2e9 + 1, "the lid, closer")]);
    await frame();
    expect(tab("view").getAttribute("aria-selected")).toBe("true");
    expect(tab("sim").getAttribute("aria-selected")).toBe("false");
    expect(section("view").hidden).toBe(false);
    expect(section("sim").hidden).toBe(true);                       // one stream at a time
    // View: the newest large, the earlier one in the filmstrip
    const v = section("view");
    expect(v.querySelectorAll("[data-testid=view-card]").length).toBe(1);
    expect(v.querySelector("[data-testid=view-card]")?.textContent).toContain("the lid, closer");
    const tiles = within(v.querySelector("[data-filmstrip]") as HTMLElement).getAllByRole("button");
    expect(tiles.map((t) => t.getAttribute("aria-current"))).toEqual([null, "true"]);
    fireEvent.click(tiles[0]);
    expect(v.querySelector("[data-testid=view-card]")?.textContent).toContain("the lid");
    expect(v.querySelector("[data-testid=view-card]")?.textContent).not.toContain("closer");
    // Simulations: one card per study, once its tab is clicked
    fireEvent.click(tab("sim"));
    await frame();
    expect(section("sim").hidden).toBe(false);
    expect(section("view").hidden).toBe(true);
    expect(document.querySelector("[data-dock] .dock-seg")?.getAttribute("data-seg")).toBe("sim");
    expect(JSON.parse(localStorage.getItem(LAYOUT_KEY) as string).active).toBe("sim");
    const sims = section("sim");
    expect(sims.querySelectorAll("[data-study-slot]").length).toBe(STUDY.filter((x) => x.kind === "tool_call").length);
    // Conversation: the words, no study or view card in it
    const conv = within(section("text")).getByRole("log", { name: "Conversation" });
    expect(conv.querySelector("[data-msg=user]")).toBeTruthy();
    expect(conv.querySelector("[data-study-slot], [data-testid=view-card]")).toBeNull();
    expect(section("sim").querySelector("[data-run-tag]")).toBeNull();
    expect(section("view").querySelector("[data-run-tag]")).toBeNull();
  });

  it("a sub-agent's studies and views show in the build's dock while the lead's thread is shown, naming that agent", async () => {
    const SUB = "sub0001";
    mount([message(RID, "user", "make the lid", 1)]);
    act(() => {
      const sub = run(SUB, { parent_id: RID, role: "sub", agent: "simulation", task: "check the lid" });
      runs.value = new Map([...runs.value, [SUB, sub]]);
      steps.value = new Map([...steps.value, [SUB, [
        ...STUDY.slice(0, 4).map((s) => ({ ...s, run_id: SUB })),
        { ...view(91_001, 2e9, "the lid from the sub-agent"), run_id: SUB },
      ]]]);
    });
    await frame();
    expect(selectedRunId.value).toBe(RID);
    expect(section("sim").querySelectorAll("[data-study-slot]").length).toBe(1);   // the sub-agent's one study
    expect(section("sim").querySelector(`[data-run="${SUB}"] [data-run-tag]`)?.textContent).toContain("simulation");
    expect(section("view").querySelector("[data-testid=view-card]")?.textContent).toContain("the lid from the sub-agent");
    expect(section("view").querySelector(`[data-view-main] [data-run-tag="${SUB}"]`)).toBeTruthy();
    expect(within(section("text")).getByRole("log", { name: "Conversation" }).querySelector("[data-study-slot]")).toBeNull();
  });

  it("an empty stream says so in one quiet line instead of a blank box", async () => {
    mount(TEXT);
    await frame();
    expect(section("view").querySelector("[data-stream-empty]")?.textContent).toContain("Views and renders");
  });

  it("View · Simulations on top, the divider, the Conversation under them with the composer (no hover-expand)", async () => {
    mount([...TEXT, ...STUDY]);
    await frame();
    const stack = document.querySelector("[data-dock] .dock-stack") as HTMLElement;
    expect([...stack.children].map((e) => e.getAttribute("data-stream-area") ?? e.getAttribute("data-stream") ?? (e.hasAttribute("data-split") ? "split" : "?")))
      .toEqual(["top", "split", "text"]);
    expect(document.querySelector("[data-dock-row], [data-expand], [data-shrunk]")).toBeNull();
    const top = stack.firstElementChild as HTMLElement;
    expect(top.style.flex).toContain(String(dockLayout.value.split));
    // hovering a tab or the stream changes nothing
    fireEvent.pointerEnter(tab("sim"));
    fireEvent.pointerEnter(section("view"));
    await frame();
    expect(tab("view").getAttribute("aria-selected")).toBe("true");
    expect(top.style.flex).toContain(String(dockLayout.value.split));
    expect(section("text").querySelector("[data-command-line]")).toBeTruthy();
    expect(document.querySelector("[data-design-focus], [aria-label='Design focus']")).toBeNull();
  });

  it("the other tab counts what arrives for it; showing it clears the count; a running count shows", async () => {
    mount([...TEXT, ...STUDY]);
    await frame();
    act(() => {
      steps.value = new Map([[RID, [...(steps.value.get(RID) ?? []), toolCall(RID, "n1", "sim_run", { type: "static" }, 3e9), toolCall(RID, "n2", "sim_run", { type: "modal" }, 3e9 + 1)]]]);
    });
    await frame();
    expect(tab("sim").querySelector("[data-unread]")?.textContent).toBe("2");
    expect(tab("sim").getAttribute("aria-label")).toBe("Simulations · 2 new · 2 running");
    expect(tab("view").querySelector("[data-unread]")).toBeNull();
    fireEvent.click(tab("sim"));
    await frame();
    expect(tab("sim").querySelector("[data-unread]")).toBeNull();
    expect(tab("sim").querySelector("[data-running]")?.getAttribute("data-running")).toBe("2");
  });

  it("either part closes to a slim bar (the top's keeps its tabs); the last open part stays open", async () => {
    mount([...TEXT, ...STUDY]);
    await frame();
    fireEvent.click(screen.getByRole("button", { name: "Hide View and Simulations" }));
    await frame();
    expect(document.querySelector("[data-dock] [data-stream=top]")?.hasAttribute("data-collapsed")).toBe(true);
    expect(document.querySelector("[data-split]")).toBeNull();
    expect(JSON.parse(localStorage.getItem(LAYOUT_KEY) as string).collapsed.top).toBe(true);
    const t = section("text").querySelector(".dock-t") as HTMLElement;
    expect(t.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(t);
    await frame();
    expect(section("text").tagName).toBe("SECTION");                // the last open part
    fireEvent.click(tab("sim"));                                    // a tab on the bar opens that stream
    await frame();
    expect(dockLayout.value).toMatchObject({ active: "sim", collapsed: { top: false } });
    fireEvent.click(section("text").querySelector(".dock-t") as HTMLElement);
    await frame();
    expect(section("text").tagName).toBe("BUTTON");
    expect(document.querySelector("[data-dock] .cmd, [data-dock] [data-command-line]")).toBeTruthy();   // the composer stays
  });

  it("the divider trades the heights and saves at the end", async () => {
    mount([...TEXT, ...STUDY]);
    await frame();
    const bar = screen.getByRole("separator", { name: /Resize View and Simulations/ });
    const before = dockLayout.value.split;
    fireEvent.keyDown(bar, { key: "ArrowDown" });
    expect(dockLayout.value.split).toBeGreaterThan(before);
    expect(JSON.parse(localStorage.getItem(LAYOUT_KEY) as string).split).toBe(dockLayout.value.split);
  });

  it("dragging the edge or the divider never selects text (P10 E10)", async () => {
    const w = window.innerWidth;
    Object.defineProperty(window, "innerWidth", { value: 2400, configurable: true });
    onTestFinished(() => { Object.defineProperty(window, "innerWidth", { value: w, configurable: true }); });
    mount(TEXT);
    await frame();
    const edge = screen.getByRole("separator", { name: "Resize the agent dock" });
    const down = new PointerEvent("pointerdown", { bubbles: true, cancelable: true, button: 0, clientX: 500, pointerId: 1 });
    edge.dispatchEvent(down);
    expect(down.defaultPrevented).toBe(true);
    expect(document.body.classList.contains("dock-resizing")).toBe(true);
    fireEvent.pointerMove(edge, { clientX: 440, pointerId: 1 });
    expect(dockLayout.value.width).toBe(620);                      // wider by 60
    fireEvent.pointerUp(edge, { pointerId: 1 });
    expect(document.body.classList.contains("dock-resizing")).toBe(false);
    expect(JSON.parse(localStorage.getItem(LAYOUT_KEY) as string).width).toBe(620);
    const bar = document.querySelector("[data-split]") as HTMLElement;
    const d2 = new PointerEvent("pointerdown", { bubbles: true, cancelable: true, button: 0, clientY: 300, pointerId: 2 });
    bar.dispatchEvent(d2);
    expect(d2.defaultPrevented).toBe(true);
    expect(document.body.classList.contains("dock-resizing")).toBe(true);
    fireEvent.pointerCancel(bar, { pointerId: 2 });
    expect(document.body.classList.contains("dock-resizing")).toBe(false);
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

// Plan 9 B3 fix round 2: an agent's background job (the real core's dump of a lead whose sim_run
// returned at once, GET /jobs while it ran) keeps the build busy and has its own Stop.
describe("agent dock: background jobs", () => {
  const P9 = resolve(FX, "p9");
  const BG = parse(resolve(P9, "streams_steps.json")).map((s) => ({ ...s, run_id: RID, id: s.id + 20_000 }));
  const [JOB] = JSON.parse(readFileSync(resolve(P9, "streams_jobs.json"), "utf8")) as BackgroundJob[];
  const simId = (BG.find((s) => s.kind === "tool_result" && s.payload.name === "sim_run")?.payload.output as { data: { sim_id: string } }).data.sim_id;
  const live = (): BackgroundJob => ({ ...JOB, job_id: simId, cancel_url: `/jobs/${simId}/cancel`, run_id: RID, build_id: "b1" });

  afterEach(() => { resetJobs(); activeBuildId.value = null; });

  it("the running study's card has a Stop that cancels that one job", async () => {
    const { c } = mount(BG);
    const cancelJob = vi.fn().mockResolvedValue({ job_id: simId, status: "cancelling" });
    Object.assign(c, { cancelJob });
    activeBuildId.value = "b1";
    liveJobs.value = [live()];
    await frame();
    const stop = section("sim").querySelector(`[data-job-stop="${simId}"]`) as HTMLButtonElement;
    expect(stop?.textContent).toBe("Stop");
    await act(async () => { fireEvent.click(stop); });
    expect(cancelJob).toHaveBeenCalledWith(simId);
    expect(c.cancel).not.toHaveBeenCalled();                        // the agent keeps working
    expect((section("sim").querySelector(`[data-job-stop="${simId}"]`) as HTMLButtonElement).disabled).toBe(true);
    // sim_finished: the job is gone, so is its Stop
    await act(async () => { onJobEvent({ type: "sim_finished", run_id: RID, ts: 0, data: { job_id: simId, status: "cancelled" } }); });
    expect(section("sim").querySelector("[data-job-stop]")).toBeNull();
  });

  it("an idle lead with a live job: the composer shows Stop, and Stop / Esc cancel the job", async () => {
    const { c } = mount(BG, { status: "done" });                    // its turn has ended
    await frame();
    expect(screen.queryByTestId("composer-stop")).toBeNull();      // nothing runs yet
    const cancelJob = vi.fn().mockResolvedValue({ job_id: simId, status: "cancelling" });
    Object.assign(c, { cancelJob });
    activeBuildId.value = "b1";
    liveJobs.value = [live()];
    await frame();
    await act(async () => { fireEvent.click(screen.getByTestId("composer-stop")); });
    expect(cancelJob).toHaveBeenCalledWith(simId);
    cancelJob.mockClear();
    await act(async () => { fireEvent.keyDown(screen.getByRole("textbox", { name: "Command line" }), { key: "Escape" }); });
    expect(cancelJob).toHaveBeenCalledWith(simId);
    // another build's job does not make this one busy
    resetJobs();
    liveJobs.value = [{ ...live(), build_id: "other" }];
    await frame();
    expect(screen.queryByTestId("composer-stop")).toBeNull();
    expect(screen.getByTestId("composer-send")).toBeTruthy();
  });
});
