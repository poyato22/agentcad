// Inline study cards from the real core's agent run (core scripts/dump_study_fixtures.py): one
// sim_run per study type and a design_review, rendered in the conversation thread; live progress
// from sim_progress; the immersive view opens from a card and from a chart point; Esc and
// ⌘[ / ⌘] work; the review card's rows open their studies.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../../api/client";
import { stepSchema } from "../../../api/schemas";
import { studyChartsSchema, studyRecordSchema, STUDY_TYPES } from "../../../api/studies";
import type { AgentRun, MisesEvent, Step } from "../../../api/types";
import { bindClient, resetStore, runs, selectedRunId, steps } from "../../../state/store";
import { studyData } from "../../pages/studies/data";
import { ImmersiveRoot } from "../../pages/studies/immersive/Immersive";
import { closeImmersive, immersive } from "../../pages/studies/immersive/state";
import { applyProgressEvent, resetLiveProgress } from "../../pages/studies/live";
import { resetThumbnails } from "../../pages/studies/scene/thumbnails";
import { Conversation } from "../Conversation";
import { resetConsoleView } from "../view";

const DIR = resolve(import.meta.dirname, "../../../../../core/tests/fixtures/ui");
const read = (n: string): unknown => JSON.parse(readFileSync(resolve(DIR, n), "utf8"));
const STEPS: Step[] = (read("study_agent_steps.json") as unknown[]).map((s) => stepSchema.parse(s));
const RUN = read("study_agent_run.json") as AgentRun;
const RID = STEPS[0].run_id;
const RECORDS = new Map(STUDY_TYPES.map((t) => { const r = studyRecordSchema.parse(read(`study_${t}.json`)); return [r.sim_id, r]; }));
const CHARTS = new Map(STUDY_TYPES.map((t) => { const c = studyChartsSchema.parse(read(`study_${t}_charts.json`)); return [c.sim_id ?? "", c]; }));
const EVENTS = readFileSync(resolve(DIR, "study_events.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l) as MisesEvent);

function client() {
  return {
    getStudy: vi.fn(async (id: string) => {
      const r = RECORDS.get(id);
      if (r) return r;
      // the design review's own studies: same shape as the static / modal ones
      const step = STEPS.find((s) => (s.payload.output as { data?: { sim_id?: string } } | undefined)?.data?.sim_id === id);
      const t = (step?.payload.output as { data: { type: string } }).data.type;
      return { ...[...RECORDS.values()].find((x) => x.type === t)!, sim_id: id };
    }),
    studyCharts: vi.fn(async (id: string) => CHARTS.get(id) ?? [...CHARTS.values()][0]),
    simGlb: vi.fn(async () => "not-modified"),
    partGlb: vi.fn(async () => "not-modified"),
    studySection: vi.fn(async () => { throw new Error("no fields"); }),
  };
}

function setThread(list: Step[], run: AgentRun = RUN) {
  runs.value = new Map([[run.id, run]]);
  steps.value = new Map([[run.id, list]]);
  selectedRunId.value = run.id;
}
async function settle() { await act(async () => { await new Promise((r) => setTimeout(r, 30)); }); }

let c: ReturnType<typeof client>;
beforeEach(() => {
  resetStore(); resetConsoleView(); resetLiveProgress(); resetThumbnails(); studyData.clear();
  c = client();
  bindClient(c as unknown as CoreClient);
});
afterEach(() => { cleanup(); immersive.value = null; bindClient(null); });

describe("inline study cards", () => {
  it("every study type renders a card from the real agent step, none folded away", async () => {
    setThread(STEPS);
    render(<Conversation />);
    await settle();
    const cards = screen.getAllByTestId("study-card");
    // six sim_run calls + the two the design review ran
    expect(cards).toHaveLength(8);
    expect(new Set(cards.map((x) => x.dataset.type))).toEqual(new Set(STUDY_TYPES));
    const wind = cards.find((x) => x.dataset.type === "wind")!;
    expect(within(wind).getByTestId("fidelity-note").textContent).toBe("low fidelity (preview) · ±40% (validated)");
    expect(within(wind).getByTestId("card-note").textContent).toMatch(/skin-friction estimate/);
    expect(wind.textContent).toContain("drag of the cube at 5 m/s");
    expect(wind.textContent).toContain("sim:"); // which agent ran it
    expect(within(wind).getAllByTestId("check")[0].dataset.status).toBe("pass");
    expect(wind.textContent).toContain("GUIDE ONLY");
    const stat = cards.find((x) => x.dataset.type === "static")!;
    expect(within(stat).getAllByTestId("metric").map((m) => m.dataset.key)).toContain("safety_factor");
    await waitFor(() => expect(within(stat).getByTestId("card-chart").dataset.chart).toBe("von_mises_histogram"));
    await waitFor(() => expect(within(wind).getByTestId("card-chart").dataset.chart).toBe("cd_vs_yaw"));
    const modal = cards.find((x) => x.dataset.type === "modal" && x.textContent?.includes("12k rpm"))!;
    await waitFor(() => expect(within(modal).getByTestId("card-chart").dataset.chart).toBe("modal_frequencies"));
  });

  it("a running call shows a live phase bar from sim_progress", async () => {
    const callIdx = STEPS.findIndex((s) => s.kind === "tool_call" && s.payload.call_id === "s6");
    setThread(STEPS.slice(0, callIdx + 1), { ...RUN, status: "running" });
    render(<Conversation />);
    await settle();
    const card = screen.getAllByTestId("study-card").find((x) => x.dataset.type === "wind")!;
    expect(card.dataset.status).toBe("running");
    const call = STEPS[callIdx];
    const prog = EVENTS.filter((e) => e.type === "sim_progress" && (e.data as { type?: string }).type === "wind" && e.run_id === RID);
    await act(async () => { applyProgressEvent({ ...prog[0], ts: call.ts + 0.1 }); });
    await act(async () => { applyProgressEvent({ ...prog.find((e) => (e.data as { phase: string }).phase === "solve")!, ts: call.ts + 0.5 }); });
    const bar = within(card).getByTestId("phase-bar");
    expect(bar.dataset.phase).toBe("solve");
    expect(bar.getAttribute("aria-valuetext")).toMatch(/solving · lattice step/);
  });

  it("the immersive view opens from a card, steps with ⌘[ / ⌘], and Esc closes it", async () => {
    setThread(STEPS);
    render(<><Conversation /><ImmersiveRoot /></>);
    await settle();
    const review = screen.getByTestId("review-card");
    expect(within(review).getAllByTestId("review-row").map((r) => r.dataset.status)).toEqual(["pass", "pass", "pass"]);
    fireEvent.click(within(review).getByTestId("open-immersive"));
    await settle();
    const imm = screen.getByTestId("immersive");
    expect(imm.getAttribute("aria-modal")).toBe("true");
    const first = imm.dataset.sim;
    expect(within(imm).getByTestId("imm-pos").textContent).toBe("1 / 2");
    expect(within(imm).getByTestId("imm-why").textContent).toMatch(/^Whysim/); // the agent that ran it, and its reason
    fireEvent.keyDown(window, { key: "]", metaKey: true });
    await settle();
    expect(screen.getByTestId("immersive").dataset.sim).not.toBe(first);
    fireEvent.keyDown(window, { key: "[", metaKey: true });
    await settle();
    expect(screen.getByTestId("immersive").dataset.sim).toBe(first);
    fireEvent.keyDown(window, { key: "Escape" });
    await settle();
    expect(screen.queryByTestId("immersive")).toBeNull();
  });

  it("each review row links to its study; a card and a chart point open the immersive view", async () => {
    setThread(STEPS);
    render(<><Conversation /><ImmersiveRoot /></>);
    await settle();
    const rows = within(screen.getByTestId("review-card")).getAllByTestId("review-row").filter((r) => r.dataset.sim);
    for (const row of rows) {
      fireEvent.click(within(row).getByRole("button"));
      await settle();
      expect(screen.getByTestId("immersive").dataset.sim).toBe(row.dataset.sim);
      closeImmersive();
      await settle();
    }
    const wind = screen.getAllByTestId("study-card").find((x) => x.dataset.type === "wind")!;
    fireEvent.click(within(wind).getByTestId("open-immersive"));
    await settle();
    const imm = screen.getByTestId("immersive");
    expect(imm.dataset.sim).toBe(RECORDS.values().find((r) => r.type === "wind")!.sim_id);
    await waitFor(() => expect(within(imm).getByTestId("imm-metrics").textContent).toMatch(/Cd/));
    expect(within(imm).getByTestId("fidelity-note").textContent).toBe("low fidelity (preview) · ±40% (validated)");
    closeImmersive();
    await settle();
    await waitFor(() => within(wind).getByTestId("study-chart"));
    fireEvent.dblClick(within(wind).getByTestId("study-chart"));
    await settle();
    expect(screen.getByTestId("immersive").dataset.sim).toBe(imm.dataset.sim);
  });

  it("the immersive view of a running study follows its progress, then shows the result", async () => {
    const wind = [...RECORDS.values()].find((r) => r.type === "wind")!;
    c.getStudy.mockImplementationOnce(async () => ({ ...wind, status: "running", phase: "mesh", pct: 2, card: { ...wind.card!, status: "running", verdict: null } }));
    setThread([]);
    render(<ImmersiveRoot />);
    immersive.value = { simId: wind.sim_id, ids: [wind.sim_id] };
    await settle();
    expect(screen.getByTestId("immersive").dataset.status).toBe("running");
    await act(async () => { applyProgressEvent({ type: "sim_progress", run_id: wind.run_id, ts: Date.now() / 1000, data: { sim_id: wind.sim_id, part_id: "cube", type: "wind", stage: "solve", phase: "solve", pct: 50, detail: "lattice step 300/600" } }); });
    expect(within(screen.getByTestId("imm-live")).getByTestId("phase-bar").dataset.phase).toBe("solve");
    expect(screen.getByTestId("live-caption").textContent).toMatch(/Wind · solving · lattice step 300\/600/);
    await act(async () => { applyProgressEvent({ type: "sim_progress", run_id: wind.run_id, ts: Date.now() / 1000, data: { sim_id: wind.sim_id, part_id: "cube", type: "wind", stage: "done", phase: "done", pct: 100, detail: "done" } }); });
    await settle();
    expect(screen.getByTestId("immersive").dataset.status).toBe("done");
    expect(screen.queryByTestId("imm-live")).toBeNull();
  });
});
