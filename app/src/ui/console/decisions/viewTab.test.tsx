// view-tab: renders land in the View tab (with their pictures, labelled by what made them and
// when), what waits on the user sits at its top ("Needs your decision", an "N to decide" badge on
// the tab), and a decision is made once: the buttons go read-only at once, a double click or a
// second card for the same item sends one request, a replayed request can't reopen it.
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CoreError, type CoreClient } from "../../../api/client";
import type { ConceptView } from "../../../api/concept";
import type { Approval, Question, Step } from "../../../api/types";
import { activeBuildId } from "../../../state/builds";
import { reduce } from "../../../state/reduce";
import { approvals, bindClient, questions, resetStore, runs, selectedRunId, steps } from "../../../state/store";
import { PitchCards } from "../../sketch/PitchCards";
import { concepts, resetConceptState } from "../../sketch/state";
import { resetCatalog } from "../../dialogs/catalog";
import { ApprovalCard } from "../approvals/ApprovalCard";
import { resetApprovalUi } from "../approvals/decide";
import { QuestionCard } from "../approvals/QuestionCard";
import { ConsolePane } from "../ConsolePane";
import { resetOffListRuns } from "../derived";
import { DEFAULT_LAYOUT, dockLayout, LAYOUT_KEY, resetDockLayout, setDockLayout } from "../dockLayout";
import { splitStreams } from "../streams";
import { resetIds, run, step, toolCall } from "../testSteps";
import { buildThread } from "../threadModel";
import { buildLog } from "../logModel";
import { resetUnread } from "../unread";
import { resetConsoleView } from "../view";
import { DecisionsSection } from "./DecisionsSection";
import { resetOnce } from "./once";
import { pendingDecisions } from "./pending";
import { renderSetOf } from "./renders";

// jsdom can't make trusted clicks: the cards' trust check is stubbed (real input: e2e)
vi.mock("../approvals/approvalKeys", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../approvals/approvalKeys")>()),
  isTrustedActivation: () => true,
}));

const RID = "12539eaab442";
const NOW = 2_000_000_000;

/** A render_views call as a Claude Code session records it (home-tvc): the tool_call step and
 *  its "measure" result, both marked stream "view", the render ids in output.data. */
function renderViews(callId: string, ts: number, ids: Record<string, string>): Step[] {
  return [
    toolCall(RID, callId, "render_views", { views: Object.keys(ids), size: 1024 }, ts, { stream: "view" }),
    step(RID, "measure", {
      call_id: callId, name: "render_views", images: [{ b64_bytes: 24912, label: "", media_type: "image/png" }],
      output: { ok: true, step_kind: "measure", summary: `rendered 22 parts: ${Object.keys(ids).join(", ")}; 1 image(s) attached`,
        data: { render_ids: ids, views: {}, size: 1024 }, handles: [], warnings: [] },
    }, ts + 1, { stream: "view" }),
  ];
}

const approval = (over: Partial<Approval> = {}): Approval => ({
  id: "ap1", run_id: RID, action: "print_send", payload: { printer: "p1" }, payload_hash: "h1", microversion: null, deltas: {},
  status: "pending", expires_at: NOW + 600, decided_by: null, decided_at: null, edited_payload: null, ...over,
} as unknown as Approval);

const question: Question = { question_id: "q1", run_id: RID, question: "Measure the bore diameter", options: ["8 mm", "10 mm"], image_id: "rv_q_front" };

async function frame() { await act(async () => { await new Promise((r) => setTimeout(r, 40)); }); }

function client(over: Record<string, unknown> = {}) {
  const c = {
    agents: vi.fn().mockResolvedValue([]), models: vi.fn().mockResolvedValue({ default: "x/y", vendors: [] }),
    localStatus: vi.fn().mockRejectedValue(new Error("404")), getProfile: vi.fn().mockResolvedValue({}),
    renderImage: vi.fn().mockResolvedValue(new Blob(["png"], { type: "image/png" })),
    partThumb: vi.fn().mockRejectedValue(new Error("none")), concept: vi.fn().mockRejectedValue(new Error("none")),
    getRun: vi.fn().mockRejectedValue(new Error("none")), ...over,
  };
  bindClient(c as unknown as CoreClient);
  return c;
}

function mountDock(list: Step[]) {
  const c = client();
  const r = run(RID, { role: "lead", project_id: null, agent: "engineer" });
  runs.value = new Map([[r.id, r]]);
  steps.value = new Map([[RID, list]]);
  selectedRunId.value = RID;
  return { c, ...render(<ConsolePane />) };
}

beforeEach(() => {
  resetIds(80_000); resetConsoleView(); try { localStorage.removeItem(LAYOUT_KEY); } catch { /* none */ }
  resetDockLayout(); resetUnread(); setDockLayout(DEFAULT_LAYOUT, false); resetOnce(); resetApprovalUi(); resetConceptState();
  vi.spyOn(Date, "now").mockReturnValue(NOW * 1000);
});
afterEach(() => {
  cleanup(); resetStore(); resetCatalog(); resetConsoleView(); resetOffListRuns(); dockLayout.value = DEFAULT_LAYOUT;
  activeBuildId.value = null; vi.restoreAllMocks();
});

describe("renders belong in the View tab", () => {
  it("a render_views result is a render set (its ids), never a conversation row", () => {
    const list = renderViews("c1", NOW, { front: "rv_a_front", iso: "rv_a_iso" });
    const items = buildThread(buildLog({ runs: [run(RID)], steps: new Map([[RID, list]]), approvals: [], questions: [], liveText: new Map(),
      filter: { scope: "run", kind: "everything" }, selectedRunId: RID, expandedFolds: new Set(), inflight: new Map() }));
    const s = splitStreams(items);
    expect(s.text.some((i) => JSON.stringify(i).includes("render_views"))).toBe(false);
    expect(s.view).toHaveLength(1);
    expect(renderSetOf(s.view[0])?.images).toEqual([{ id: "rv_a_front", label: "front" }, { id: "rv_a_iso", label: "iso" }]);
  });

  it("the View tab shows the newest render set large, with its images, by whom and when; earlier ones in the strip", async () => {
    const { c } = mountDock([...renderViews("c1", NOW - 600, { front: "rv_old_front" }), ...renderViews("c2", NOW, { hero: "rv_new_hero", side: "rv_new_side" })]);
    await frame();
    const main = document.querySelector("[data-dock] [data-view-main]") as HTMLElement;
    expect(main).toBeTruthy();
    expect(main.querySelector("[data-render-set]")?.getAttribute("data-render-set")).toBe("2");
    await waitFor(() => expect(main.querySelectorAll("[data-render-image] img")).toHaveLength(2));
    expect(c.renderImage).toHaveBeenCalledWith("rv_new_hero");
    const cap = main.querySelector("[data-view-caption]")?.textContent ?? "";
    expect(cap).toMatch(/^render views · .+ · \d\d:\d\d$/);
    expect(document.querySelectorAll("[data-filmstrip] [data-tile]")).toHaveLength(2);
    expect(document.querySelector("[data-stream-body=view] [data-stream-empty]")).toBeNull();
    // the conversation doesn't hold the render
    expect(document.querySelector("[data-stream-body=text] [data-render-set]")).toBeNull();
  });
});

describe("Needs your decision, at the top of the View tab", () => {
  it("lists what waits (approval, measurement ask, pitch) with what and who, and badges the tab", async () => {
    mountDock([]);
    activeBuildId.value = "pb1";
    concepts.value = { pb1: {
      concept: { project_id: "pb1", status: "pitching", sketch_ids: [], chosen_option: null, pitch_ids: ["ckp_1"], approved_sketch_id: null, updated_at: null },
      design_class: null, sketches: [],
      pitches: [{ pitch_id: "ckp_1", project_id: "pb1", run_id: RID, question: "", options: [], chosen: null, created_at: NOW }],
    } as unknown as ConceptView };
    approvals.value = new Map([["ap1", approval()]]);
    questions.value = new Map([["q1", question]]);
    await frame();
    const sec = document.querySelector("[data-dock] [data-stream-body=view] [data-decisions]") as HTMLElement;
    expect(sec).toBeTruthy();
    expect(sec.querySelector("[data-decisions-count]")?.textContent).toBe("3");
    const kinds = Array.from(sec.querySelectorAll<HTMLElement>("[data-decision-item]")).map((e) => e.dataset.decisionItem);
    expect(kinds).toEqual(["approval", "question", "pitch"]);
    expect(sec.textContent).toContain("Measure: Measure the bore diameter");
    expect(sec.querySelector("[data-decision-agent]")?.textContent).toBeTruthy();
    const badge = document.querySelector("[data-stream-tab=view] [data-to-decide]") as HTMLElement;
    expect(badge.textContent).toBe("3 to decide");
    expect(document.querySelector("[data-stream-tab=view] [data-unread]")).toBeNull();   // distinct from unread
    // nothing pending: no section, no badge
    approvals.value = new Map(); questions.value = new Map(); concepts.value = {};
    await frame();
    expect(document.querySelector("[data-decisions]")).toBeNull();
    expect(document.querySelector("[data-to-decide]")).toBeNull();
  });
});

describe("a decision is one-time", () => {
  it("approving: one request for a double click and two cards; then read-only 'Approved by you' everywhere", async () => {
    let finish: (a: Approval) => void = () => {};
    const decide = vi.fn().mockImplementation(() => new Promise<Approval>((r) => { finish = r; }));
    client({ decide });
    approvals.value = new Map([["ap1", approval()]]);
    let clock = 1e6;
    vi.spyOn(performance, "now").mockImplementation(() => clock);
    const { container } = render(<div><DecisionsSection /><ApprovalCard approval={approval()} runLabel="Engineer" /></div>);
    clock += 2000;                                                       // the cards have armed
    const buttons = container.querySelectorAll<HTMLButtonElement>("[data-decision=approve]");
    expect(buttons).toHaveLength(2);
    const press = (b: HTMLButtonElement) => { fireEvent.pointerDown(b, { button: 0, isPrimary: true }); clock += 80; fireEvent.click(b, { detail: 1 }); clock += 80; };
    press(buttons[0]); press(buttons[0]); press(buttons[1]);
    await act(async () => { finish(approval({ status: "approved", decided_by: "local-user", decided_at: NOW })); await Promise.resolve(); });
    await frame();
    expect(decide).toHaveBeenCalledTimes(1);
    expect(container.querySelector("[data-decision=approve]")).toBeNull();
    expect(container.textContent).toMatch(/Approved by you \d\d:\d\d/);
    expect(pendingDecisions.value).toHaveLength(0);
  });

  it("a replayed approval_requested never reopens a decided card", () => {
    const decided = approval({ status: "approved", decided_by: "local-user", decided_at: NOW });
    const s = { approvals: new Map([["ap1", decided]]) } as unknown as Parameters<typeof reduce>[0];
    const out = reduce(s, { type: "approval_requested", run_id: RID, data: approval() as unknown as Record<string, unknown>, ts: NOW });
    expect(out.state.approvals.get("ap1")?.status).toBe("approved");
  });

  it("answering an ask: a double click sends once; a 409 already_decided closes it too", async () => {
    let finish: () => void = () => {};
    const answer = vi.fn().mockImplementation(() => new Promise((r) => { finish = () => r({ ok: true }); }));
    client({ answer });
    questions.value = new Map([["q1", question]]);
    let clock = 1e6;
    vi.spyOn(performance, "now").mockImplementation(() => clock);
    const { container } = render(<div><QuestionCard question={question} runLabel="Engineer" /><QuestionCard question={question} runLabel="Engineer" /></div>);
    clock += 2000;
    const opts = Array.from(container.querySelectorAll<HTMLButtonElement>("[data-question] .qcard-options button"));
    fireEvent.click(opts[0], { detail: 1 }); fireEvent.click(opts[0], { detail: 1 }); fireEvent.click(opts[2], { detail: 1 });
    await act(async () => { finish(); await Promise.resolve(); });
    await frame();
    expect(answer).toHaveBeenCalledTimes(1);
    expect(questions.value.has("q1")).toBe(false);

    resetOnce();
    const again = vi.fn().mockRejectedValue(new CoreError("already_decided", "already answered", { status: 409, body: { decision: { kind: "answer", text: "8 mm" } } }));
    client({ answer: again });
    questions.value = new Map([["q1", question]]);
    cleanup();
    const r2 = render(<QuestionCard question={question} runLabel="Engineer" />);
    clock += 2000;
    fireEvent.click(r2.container.querySelector<HTMLButtonElement>(".qcard-options button")!, { detail: 1 });
    await frame();
    expect(again).toHaveBeenCalledTimes(1);
    expect(questions.value.has("q1")).toBe(false);
    expect(r2.container.querySelector("[data-error]")).toBeNull();
  });

  it("choosing a pitch: one request for a double click, then read-only with when", async () => {
    let finish: () => void = () => {};
    const choosePitch = vi.fn().mockImplementation(() => new Promise((r) => { finish = () => r({
      concept: { project_id: "pb1", status: "sketching", sketch_ids: [], chosen_option: "A", pitch_ids: ["ckp_1"], approved_sketch_id: null, updated_at: NOW },
      chosen: { option: "A", mix: [], comment: "", label: "A", decided_at: NOW }, sent: null }); }));
    client({ choosePitch, concept: vi.fn().mockReturnValue(new Promise(() => {})) });
    const opt = { name: "A", mood: [], proportion_note: "", cmf: [], tradeoffs: { mass_delta: "", part_count: 1, molding: "", cost_tier: "", risk: "", gives_up: "" },
      recommended: true, reason: "", baseline: false, callouts: [], sketch: null, silhouettes: {} };
    concepts.value = { pb1: { concept: { project_id: "pb1", status: "pitching", sketch_ids: [], chosen_option: null, pitch_ids: ["ckp_1"], approved_sketch_id: null, updated_at: null },
      design_class: null, sketches: [], pitches: [{ pitch_id: "ckp_1", project_id: "pb1", run_id: RID, question: "", options: [opt, { ...opt, name: "B", recommended: false }], chosen: null, created_at: NOW }] } as unknown as ConceptView };
    const { container } = render(<div><PitchCards projectId="pb1" pitchId="ckp_1" /><PitchCards projectId="pb1" pitchId="ckp_1" /></div>);
    const picks = container.querySelectorAll<HTMLButtonElement>("[data-testid=pitch-pick]");
    fireEvent.click(picks[0]); fireEvent.click(picks[0]); fireEvent.click(picks[3]);
    await act(async () => { finish(); await Promise.resolve(); });
    await frame();
    expect(choosePitch).toHaveBeenCalledTimes(1);
    for (const b of container.querySelectorAll<HTMLButtonElement>("[data-testid=pitch-pick]")) expect(b.disabled).toBe(true);
    expect(container.querySelectorAll("[data-testid=pitch-chosen][data-resolved]")).toHaveLength(2);
    expect(container.textContent).toMatch(/Chose A · by you \d\d:\d\d/);
  });
});
