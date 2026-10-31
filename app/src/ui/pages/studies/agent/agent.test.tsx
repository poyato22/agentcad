// Plan 9 U-D: the Studies page is agent-first. Against the real core's studies context
// (core/scripts/dump_studies_context_fixtures.py): proposals with why and a Run button that
// starts the proposal's study through POST /sims, the study card it brings, the studies agent's
// conversation (one `simulation` run per build), and the manual setup collapsed behind
// "Set up manually".
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CoreError } from "../../../../api/client";
import { studiesContextSchema } from "../../../../api/studiesAgent";
import { studySolversSchema, studyTypeInfoSchema } from "../../../../api/studies";
import type { AgentRun, PartSummary, Step } from "../../../../api/types";
import { activeBuildId } from "../../../../state/builds";
import { actions, parts, resetStore, runs, selectedPartId, steps } from "../../../../state/store";
import { StudiesController, type StudiesClient } from "../controller";
import { resetStudiesPage, StudiesPage } from "../index";
import { PLAN_PROMPT, StudiesAgentController, studiesAgentRun, type StudiesAgentClient } from "./model";

const DIR = resolve(import.meta.dirname, "../../../../../../core/tests/fixtures/ui");
const read = (n: string): unknown => JSON.parse(readFileSync(resolve(DIR, n), "utf8"));
const RULES = studiesContextSchema.parse(read("studies_context.json"));
const AGENT = studiesContextSchema.parse(read("studies_context_agent.json"));
const PID = AGENT.project_id;
const TYPES = (read("study_types.json") as unknown[]).map((x) => studyTypeInfoSchema.parse(x));

const run = (over: Partial<AgentRun> = {}): AgentRun => ({
  id: "sa1", parent_id: null, agent: "simulation", provider: "fake", model: "m", task: "plan", status: "done", branch: null,
  onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0, created_at: 10, updated_at: 10, project_id: PID, ...over,
});

const part = (id: string): PartSummary => ({ part_id: id, vars: {}, mtime: 1, metrics: {
  valid: true, volume_mm3: 4800, area_mm2: 2000, mass_g: 6, bbox: { min: [0, 0, 0], max: [40, 20, 6], size: [40, 20, 6] },
  center_of_mass: [20, 10, 3], faces: 6, edges: 12, holes: [], solids: 1, face_list: [],
} });

describe("studies agent model", () => {
  it("the build's studies agent is its newest top-level simulation run", () => {
    const list = [run({ id: "old", created_at: 1 }), run({ id: "new", created_at: 5 }), run({ id: "arch", created_at: 9, archived: true }),
      run({ id: "other", created_at: 9, project_id: "elsewhere" }), run({ id: "sub", created_at: 9, parent_id: "x" }), run({ id: "eng", created_at: 9, agent: "engineer" })];
    expect(studiesAgentRun(list, PID)?.id).toBe("new");
    expect(studiesAgentRun(list, null)).toBeNull();
  });

  it("parses the real context; one broken proposal does not hide the others", () => {
    expect(AGENT.proposals_source).toBe("agent");
    expect(AGENT.proposals.map((p) => p.type)).toEqual(["static", "modal"]);
    expect(RULES.proposals_source).toBe("rules");
    expect(RULES.proposals[0].why).toMatch(/no stress study/);
    const raw = read("studies_context_agent.json") as { proposals: unknown[] };
    const one = studiesContextSchema.parse({ ...raw, proposals: [{ broken: true }, raw.proposals[0]] });
    expect(one.proposals).toHaveLength(1);
  });

  it("a message goes to the studies agent, or starts it when there is none or it cannot continue", async () => {
    const c = { studiesContext: vi.fn().mockResolvedValue(AGENT), createRun: vi.fn().mockResolvedValue({ run_id: "sa9" }), message: vi.fn().mockResolvedValue({ ok: true }) };
    const a = new StudiesAgentController(() => c as unknown as StudiesAgentClient);
    await a.load(PID);
    expect(c.studiesContext).toHaveBeenCalledWith(PID);
    expect(await a.send("what next?", null)).toBe("sa9");
    expect(c.createRun).toHaveBeenCalledWith({ agent: "simulation", prompt: "what next?", project_id: PID });
    expect(await a.send("and the leg?", run())).toBe("sa1");
    expect(c.message).toHaveBeenCalledWith("sa1", "and the leg?");
    c.message.mockRejectedValueOnce(new CoreError("not_found", "unknown run"));
    expect(await a.send("again", run())).toBe("sa9");
    expect(await a.send("busy?", run({ status: "running" }))).toBeNull();
    expect(a.sendError.value).toMatch(/still working/);
  });

  it("never starts a second studies agent while one is starting or still working (M3)", async () => {
    const c = { studiesContext: vi.fn().mockResolvedValue(AGENT), createRun: vi.fn().mockResolvedValue({ run_id: "sa9" }), message: vi.fn() };
    const a = new StudiesAgentController(() => c as unknown as StudiesAgentClient);
    await a.load(PID);
    expect(await a.send("first", null)).toBe("sa9");
    // the runs list does not have sa9 yet: a second send must not start another
    expect(await a.send("second", null)).toBeNull();
    expect(a.sendError.value).toMatch(/starting/);
    expect(c.createRun).toHaveBeenCalledTimes(1);
    a.noteRun(run({ id: "sa9" }));
    // the core says it is still working (the race before the runs list updates): said, no new run
    c.message.mockRejectedValueOnce(new CoreError("invalid_input", "run sa9 is still working", { status: 400 }));
    expect(await a.send("third", run({ id: "sa9" }))).toBeNull();
    expect(a.sendError.value).toMatch(/still working/);
    expect(c.createRun).toHaveBeenCalledTimes(1);
  });

  it("the proposal Run pressed is remembered per build across a reload", async () => {
    const c = { studiesContext: vi.fn().mockResolvedValue(AGENT), createRun: vi.fn(), message: vi.fn() };
    const a = new StudiesAgentController(() => c as unknown as StudiesAgentClient);
    await a.load(PID);
    a.noteRan(AGENT.proposals[0]);
    const b = new StudiesAgentController(() => c as unknown as StudiesAgentClient);
    await b.load(PID);
    expect(b.ranProposal.value?.id).toBe(AGENT.proposals[0].id);
    await b.load("another_build");
    expect(b.ranProposal.value).toBeNull();
  });

  it("reloads when the agent saves proposals for this build", async () => {
    vi.useFakeTimers();
    try {
      const c = { studiesContext: vi.fn().mockResolvedValue(RULES), createRun: vi.fn(), message: vi.fn() };
      const a = new StudiesAgentController(() => c as unknown as StudiesAgentClient);
      await a.load(PID);
      a.onEvent({ type: "studies_proposed", run_id: "sa1", data: { project_id: "elsewhere" }, ts: 1 }, new Map());
      a.onEvent({ type: "studies_proposed", run_id: "sa1", data: { project_id: PID }, ts: 1 }, new Map());
      await vi.runAllTimersAsync();
      expect(c.studiesContext).toHaveBeenCalledTimes(2);
    } finally { vi.useRealTimers(); }
  });
});

describe("Studies page, agent-first", () => {
  const noGl = { createRenderer: () => { throw new Error("no WebGL in tests"); }, partGlb: async () => null, resultGlb: async () => null };
  let sc: Record<string, ReturnType<typeof vi.fn>>;
  let ac: { studiesContext: ReturnType<typeof vi.fn>; createRun: ReturnType<typeof vi.fn>; message: ReturnType<typeof vi.fn> };
  let ctl: StudiesController;
  let agent: StudiesAgentController;
  const settle = async () => { await act(async () => { await new Promise((r) => setTimeout(r, 20)); }); };

  beforeEach(() => {
    localStorage.clear();
    resetStore(); resetStudiesPage();
    parts.value = [part("hip_bracket"), part("leg_front")];
    actions.selectPart("leg_front");
    activeBuildId.value = PID;
    sc = {
      studyTypes: vi.fn().mockResolvedValue(TYPES), studySolvers: vi.fn().mockResolvedValue(studySolversSchema.parse(read("study_solvers.json"))),
      listStudies: vi.fn().mockResolvedValue([]), getStudy: vi.fn(), studyCharts: vi.fn(), startStudy: vi.fn().mockResolvedValue({ run_id: "st1", status: "running" }),
      cancelStudy: vi.fn(), compareStudies: vi.fn(), studyTemplates: vi.fn().mockResolvedValue([]), addStudyTemplate: vi.fn(), deleteStudyTemplate: vi.fn(),
      studyMaterials: vi.fn().mockResolvedValue({ builtin: [], custom: [] }), addStudyMaterial: vi.fn(), deleteStudyMaterial: vi.fn(),
      cancel: vi.fn(), getRun: vi.fn().mockResolvedValue({ status: "running" }), steps: vi.fn().mockResolvedValue([]), createRun: vi.fn(),
    };
    ac = { studiesContext: vi.fn().mockResolvedValue(AGENT), createRun: vi.fn().mockResolvedValue({ run_id: "sa9" }), message: vi.fn().mockResolvedValue({ ok: true }) };
    ctl = new StudiesController(() => sc as unknown as StudiesClient, { pollMs: 10_000 });
    agent = new StudiesAgentController(() => ac as unknown as StudiesAgentClient);
  });
  afterEach(() => { ctl.dispose(); agent.dispose(); cleanup(); activeBuildId.value = null; });

  const mount = () => render(<StudiesPage controller={ctl} agent={agent} deps={{ view: noGl }} />);

  it("opens on the agent's proposals, each with why and Run; the manual setup is collapsed", async () => {
    mount();
    await settle();
    const pane = screen.getByTestId("studies-agent");
    const cards = within(pane).getAllByTestId("study-proposal");
    expect(cards).toHaveLength(2);
    expect(cards[0].textContent).toMatch(/Stress: hip bracket at 2x body weight/);
    expect(cards[0].textContent).toMatch(/has no stress study/);
    expect(screen.getByTestId("proposals-source").dataset.source).toBe("agent");
    expect(screen.queryByTestId("schema-form")).toBeNull();
    expect(screen.queryByTestId("type-static")).toBeNull();
    const toggle = screen.getByTestId("manual-setup-toggle");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    await settle();
    expect(screen.getByTestId("schema-form")).toBeTruthy();
    expect(screen.getByTestId("type-static")).toBeTruthy();
  });

  it("Run starts the proposal's study through POST /sims and shows its card", async () => {
    mount();
    await settle();
    const first = screen.getAllByTestId("study-proposal")[0];
    fireEvent.click(within(first).getByTestId("run-proposal"));
    await settle();
    expect(sc.startStudy).toHaveBeenCalledWith({ type: "static", part_id: "hip_bracket", settings: AGENT.proposals[0].settings });
    expect(selectedPartId.value).toBe("hip_bracket");
    const card = screen.getByTestId("agent-study-card");
    expect(card.dataset.status).toBe("running");
    // while it runs, only its own Run is disabled; another proposal's Run queues it (N2)
    const [ranCard, other] = screen.getAllByTestId("study-proposal");
    const ranBtn = within(ranCard).getByTestId("run-proposal") as HTMLButtonElement;
    expect([ranBtn.textContent, ranBtn.disabled]).toEqual(["Running…", true]);
    const next = within(other).getByTestId("run-proposal") as HTMLButtonElement;
    expect([next.textContent, next.disabled]).toEqual(["Run next", false]);
    expect(within(other).queryByTestId("proposal-blocked")).toBeNull();
    fireEvent.click(next);
    await settle();
    expect(next.dataset.state).toBe("queued");
    fireEvent.click(next);                              // pressed again: off the queue
    await settle();
    expect(next.dataset.state).toBe("new");
    fireEvent.click(next);
    await settle();
    // the running study ends: the queued one starts, with its own settings
    act(() => { ctl.active.value = null; });
    await settle();
    expect(sc.startStudy).toHaveBeenLastCalledWith({ type: AGENT.proposals[1].type, part_id: "leg_front", settings: AGENT.proposals[1].settings });
    await act(async () => { await ctl.cancel(); ctl.active.value = null; });
    fireEvent.click(within(screen.getAllByTestId("study-proposal")[0]).getByTestId("run-proposal"));
    await settle();
    // the manual setup now holds the proposal's settings
    fireEvent.click(screen.getByTestId("manual-setup-toggle"));
    await settle();
    expect((document.getElementById("sf-load_cases-0-loads-0-newtons") as HTMLInputElement).value).toBe("60");
  });

  it("a proposal whose study is running says so and cannot run twice; the Run card sits under its proposal", async () => {
    ac.studiesContext.mockResolvedValue({ ...AGENT, proposals: [{ ...AGENT.proposals[0], status: "running" }, AGENT.proposals[1]] });
    mount();
    await settle();
    const [first, second] = screen.getAllByTestId("study-proposal");
    const run0 = within(first).getByTestId("run-proposal") as HTMLButtonElement;
    expect(run0.textContent).toBe("Running…");
    expect(run0.disabled).toBe(true);
    fireEvent.click(within(second).getByTestId("run-proposal"));
    await settle();
    expect(within(screen.getAllByTestId("study-proposal")[1]).getByTestId("agent-study-card")).toBeTruthy();
  });

  it("the conversation shows the studies agent's thread and the composer starts it", async () => {
    runs.value = new Map([["sa1", run()]]);
    const st: Step[] = [
      { id: 1, run_id: "sa1", ts: 1, kind: "message", payload: { role: "user", text: "What should I test?" } },
      { id: 2, run_id: "sa1", ts: 2, kind: "message", payload: { role: "assistant", text: "Start with the hip bracket.", stop_reason: "end_turn" } },
    ].map((s) => ({ evidence_hash: null, prev_hash: null, collapsed: false, ...s })) as unknown as Step[];
    steps.value = new Map([["sa1", st]]);
    mount();
    await settle();
    expect(screen.getByTestId("sa-user").textContent).toBe("What should I test?");
    expect(screen.getByTestId("sa-agent").textContent).toMatch(/Start with the hip bracket/);
    const input = screen.getByTestId("studies-agent-input") as HTMLTextAreaElement;
    fireEvent.input(input, { target: { value: "And the legs?" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await settle();
    expect(ac.message).toHaveBeenCalledWith("sa1", "And the legs?");
  });

  it("with no studies agent yet, one click asks it for a plan", async () => {
    mount();
    await settle();
    fireEvent.click(screen.getByTestId("studies-agent-plan"));
    await settle();
    expect(ac.createRun).toHaveBeenCalledWith({ agent: "simulation", prompt: PLAN_PROMPT, project_id: PID });
  });
});
