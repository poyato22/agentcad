import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadScenario, startFakeCore, type FakeCore, type Scenario } from "../../../test/fake-core/server";
import { FIXTURE_PROJECT, REAL_SPEC } from "../../../test/fake-core/spec";
import { CoreClient, CoreError } from "../../api/client";
import type { MisesEvent, Question } from "../../api/types";
import { bindClient, conn, parts, questions, resetStore } from "../../state/store";
import { QuestionCard } from "../console/approvals/QuestionCard";
import { errorWords, SpecController } from "./controller";
import { SpecPanel, specPanelOpen } from "./index";
import { Scatter } from "./Scatter";
import { SpecCard } from "./SpecCard";
import { StudyView } from "./StudyView";

let trusted = true;
vi.mock("../console/approvals/approvalKeys", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../console/approvals/approvalKeys")>()),
  isTrustedActivation: () => trusted,
}));

const FIX = resolve(import.meta.dirname, "../../../../core/tests/fixtures/ui");
const EVENTS = readFileSync(resolve(FIX, "spec_events.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l) as MisesEvent);
const STUDY = REAL_SPEC.studies[0];
const SPEC = REAL_SPEC.spec.spec;

let core: FakeCore;
let clock = 0;
let client: CoreClient;

async function start(spec?: Scenario["spec"]) {
  const sc: Scenario = { ...loadScenario("base"), ...(spec ? { spec } : {}) };
  core = await startFakeCore({ port: 0, token: "t", scenario: sc });
  client = new CoreClient({ baseUrl: core.url, wsUrl: "", token: "t", owned: false });
  bindClient(client);
  parts.value = await client.parts();
}

const calls = (suffix: string) => core.state.calls.filter((c) => c.path.endsWith(suffix));

beforeEach(() => {
  trusted = true;
  clock = 100_000;
  vi.spyOn(performance, "now").mockImplementation(() => clock);
});

afterEach(async () => {
  cleanup();
  resetStore();
  specPanelOpen.value = false;
  await core.stop();
});

describe("interview card (real batched question_asked)", () => {
  it("posts the batch as a JSON object of answers", async () => {
    await start();
    const ev = EVENTS.find((e) => e.type === "question_asked")!;
    core.emit({ ...ev, run_id: "b7c79a57cbb4" });
    const q = core.state.questions.get(ev.data.question_id as string) as Question;
    expect(q.questions).toHaveLength(3);
    questions.value = new Map([[q.question_id, q]]);
    const { container } = render(<QuestionCard question={q} runLabel="engineer · drone" />);
    clock += 500;
    const card = container.querySelector("[data-interview]") as HTMLElement;
    expect(card).toBeTruthy();
    expect(card.textContent).toContain("A few questions first:");
    expect(card.textContent).not.toContain("1. What will this drone do?"); // the listing is not repeated
    const send = screen.getByRole("button", { name: "Send answers" }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);

    const use = within(card.querySelector('[data-ask="use"]') as HTMLElement);
    fireEvent.click(use.getByRole("button", { name: "racing" }));
    expect(use.getByRole("button", { name: "racing" }).getAttribute("aria-pressed")).toBe("true");
    const size = card.querySelector('[data-ask="size_class"]') as HTMLElement;
    fireEvent.input(within(size).getByRole("textbox"), { target: { value: "4 inch" } });
    expect(send.disabled).toBe(true);
    expect(card.textContent).toContain("Answer 3 to send.");
    fireEvent.click(within(card.querySelector('[data-ask="motor_prop"]') as HTMLElement).getByRole("button", { name: "2207" }));
    expect(send.disabled).toBe(false);

    fireEvent.click(send);
    await waitFor(() => expect(calls("/answer")).toHaveLength(1));
    const body = calls("/answer")[0].body as { text: string };
    expect(JSON.parse(body.text)).toEqual({ use: "racing", size_class: "4 inch", motor_prop: "2207" });
    await waitFor(() => expect(questions.value.size).toBe(0));
  });

  it("ranks by keyboard and sends the order", async () => {
    await start();
    core.emit({ type: "question_asked", run_id: "b7c79a57cbb4", data: {
      question_id: "q_rank", question: "Last one:\n1. Rank", options: [],
      questions: [{ id: "priorities", prompt: "Rank what matters most", options: ["weight", "impact strength", "stiffness"], multi: false, rank: true, allow_text: true }],
    } });
    const q = core.state.questions.get("q_rank") as Question;
    render(<QuestionCard question={q} runLabel="engineer" />);
    clock += 500;
    fireEvent.click(screen.getByRole("button", { name: "Move stiffness up" }));
    const row = document.querySelector('[data-rank="weight"]') as HTMLElement;
    fireEvent.keyDown(row, { key: "ArrowDown", altKey: true });
    fireEvent.click(screen.getByRole("button", { name: "Send answers" }));
    await waitFor(() => expect(calls("/answer")).toHaveLength(1));
    expect(JSON.parse((calls("/answer")[0].body as { text: string }).text)).toEqual({ priorities: ["stiffness", "weight", "impact strength"] });
  });

  it("an untrusted or too-early click sends nothing", async () => {
    await start();
    core.emit({ type: "question_asked", run_id: "b7c79a57cbb4", data: {
      question_id: "q_one", question: "Q", options: [],
      questions: [{ id: "use", prompt: "Use?", options: ["a"], multi: false, rank: false, allow_text: true }],
    } });
    render(<QuestionCard question={core.state.questions.get("q_one") as Question} runLabel="engineer" />);
    fireEvent.click(screen.getByRole("button", { name: "a" }));
    fireEvent.click(screen.getByRole("button", { name: "Send answers" })); // not armed yet
    clock += 500;
    trusted = false;
    fireEvent.click(screen.getByRole("button", { name: "Send answers" }));
    await new Promise((r) => setTimeout(r, 30));
    expect(calls("/answer")).toHaveLength(0);
  });
});

describe("spec card", () => {
  it("shows requirements with current values, priority dots and the estimate tag", async () => {
    await start({ spec: "fixture" });
    const c = new SpecController(() => client);
    await c.load();
    expect(c.projectId.value).toBe(FIXTURE_PROJECT);
    await c.runCheck("mount_plate");
    const { container } = render(<SpecCard spec={c.spec.value!} check={c.check.value} onSave={(s) => c.save(s)} />);
    const mass = container.querySelector('[data-req="mass_budget"]') as HTMLElement;
    expect(mass.dataset.status).toBe("pass");
    expect(mass.textContent).toContain("≤ 8 g");
    expect(mass.querySelector("[data-current]")!.textContent).toBe("5.75 g");
    expect(mass.textContent).toContain("●●●●●");
    expect(container.querySelector('[data-req="drop_safety"]')!.textContent).toContain("estimate");
    expect(container.querySelector('[data-case="drop_hit"]')!.textContent).toContain("estimate");
    expect(container.querySelector("[data-tally]")!.textContent).toBe("2 pass · 4 not checked");
  });

  it("edit round trip: the PUT carries the user's edit and the card shows it", async () => {
    await start({ spec: "fixture" });
    const c = new SpecController(() => client);
    await c.load();
    await c.runCheck("mount_plate");
    const r = render(<SpecCard spec={c.spec.value!} check={c.check.value} onSave={(s) => c.save(s)} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.input(screen.getByLabelText("mass_budget target"), { target: { value: "5" } });
    fireEvent.input(screen.getByLabelText("drop_hit force in newtons"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls("/spec").filter((x) => x.method === "PUT")).toHaveLength(1));
    const put = calls("/spec").find((x) => x.method === "PUT")!.body as typeof SPEC;
    expect(put.requirements.find((x) => x.id === "mass_budget")).toMatchObject({ target: 5, source: "user" });
    expect(put.requirements.find((x) => x.id === "desk_safety")).toMatchObject({ source: "agent" });
    expect(put.load_cases.find((x) => x.id === "drop_hit")).toMatchObject({ newtons: 30, estimate: true });
    expect((await client.getSpec(FIXTURE_PROJECT)).spec.requirements[0].target).toBe(5);
    await waitFor(() => expect(c.check.value?.requirements.find((x) => x.id === "mass_budget")?.status).toBe("fail"));
    r.rerender(<SpecCard spec={c.spec.value!} check={c.check.value} onSave={(s) => c.save(s)} />);
    expect(r.container.querySelector('[data-req="mass_budget"]')!.textContent).toContain("≤ 5 g");
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  });

  it("a bad edit is listed and not sent; the core's 422 is shown", async () => {
    await start({ spec: "fixture" });
    const c = new SpecController(() => client);
    await c.load();
    render(<SpecCard spec={c.spec.value!} check={null} onSave={(s) => c.save(s)} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.input(screen.getByLabelText("stiffness target"), { target: { value: "abc" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByRole("alert").textContent).toContain("stiffness: needs a positive number");
    expect(calls("/spec").filter((x) => x.method === "PUT")).toHaveLength(0);
    // the core's own validation (a load case referenced by a requirement can't go): its words show
    expect(await c.save({ ...SPEC, load_cases: [] })).toMatch(/unknown load case/);
  });
});

describe("study scatter", () => {
  it("highlights the Pareto front from the real study", () => {
    const { container } = render(<Scatter iterations={STUDY.iterations} requirements={SPEC.requirements} bestId={STUDY.best_id} selected={null} onSelect={() => {}} width={480} />);
    const pts = [...container.querySelectorAll<SVGGElement>("[data-point]")];
    expect(pts).toHaveLength(STUDY.iterations.length);
    const kinds = Object.fromEntries(pts.map((p) => [p.dataset.point, p.dataset.kind]));
    for (const it of STUDY.iterations) expect(kinds[String(it.n)]).toBe(it.pareto ? "pareto" : it.feasible ? "feasible" : "infeasible");
    const front = container.querySelector("[data-front]") as SVGPolylineElement;
    expect(front.getAttribute("points")!.split(" ")).toHaveLength(STUDY.iterations.filter((i) => i.pareto).length);
    expect(container.querySelector('[data-threshold="sf"]')!.textContent).toBe("SF ≥ 2.00");
    expect(container.querySelector('[data-threshold="mass"]')!.textContent).toBe("≤ 8 g");
    expect(container.textContent).toContain(`best #${STUDY.best_id}`);
    expect(container.querySelector("[aria-label='Legend']")!.textContent).toContain("Pareto front");
  });

  it("click, hover and keyboard pick a variant", () => {
    const picked: number[] = [];
    const { container } = render(<Scatter iterations={STUDY.iterations} requirements={SPEC.requirements} bestId={STUDY.best_id} selected={null} onSelect={(n) => picked.push(n)} width={480} />);
    const p0 = container.querySelector('[data-point="0"]') as SVGGElement;
    fireEvent.click(p0);
    expect(picked).toEqual([0]);
    fireEvent.mouseEnter(p0);
    expect(container.querySelector("[data-tip]")!.textContent).toContain("fails the spec");
    // Tab target is the best variant; → walks to the next heavier one, Enter previews it
    const tab = container.querySelector('[data-point][tabindex="0"]') as SVGGElement;
    expect(tab.dataset.point).toBe(String(STUDY.best_id));
    const svg = container.querySelector("svg.sps-plot") as SVGSVGElement;
    fireEvent.keyDown(svg, { key: "Home" });
    fireEvent.keyDown(svg, { key: "Enter" });
    const lightest = [...STUDY.iterations].sort((a, b) => a.metrics.mass_g! - b.metrics.mass_g!)[0];
    expect(picked.at(-1)).toBe(lightest.n);
  });
});

describe("study view: preview and use a variant", () => {
  async function mountView() {
    await start({ spec: "fixture", studies: "fixture" });
    const c = new SpecController(() => client);
    await c.load();
    const r = render(<StudyView c={c} study={c.studies.value[0]} requirements={c.spec.value!.requirements} />);
    return { c, ...r };
  }

  it("clicking a point previews that variant with its requirement checks", async () => {
    const { container } = await mountView();
    expect(container.querySelector("[data-variant]")!.getAttribute("data-variant")).toBe(String(STUDY.best_id));
    fireEvent.click(container.querySelector('[data-point="0"]')!);
    const v = container.querySelector("[data-variant]") as HTMLElement;
    expect(v.dataset.variant).toBe("0");
    expect(v.textContent).toContain("fails the spec");
    expect((v.querySelector('[data-req="stiffness"]') as HTMLElement).dataset.status).toBe("fail");
    expect(container.querySelector('[data-iter="0"]')!.getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector('[data-point="0"]')!.hasAttribute("data-selected")).toBe(true);
  });

  it("the table sorts and selects", async () => {
    const { container } = await mountView();
    fireEvent.click(screen.getByRole("button", { name: "min SF" }));
    const order = [...container.querySelectorAll<HTMLElement>("[data-iter]")].map((r) => Number(r.dataset.iter));
    const want = [...STUDY.iterations].sort((a, b) => b.metrics.min_sf! - a.metrics.min_sf!).map((i) => i.n);
    expect(order).toEqual(want);
    fireEvent.keyDown(container.querySelector('[data-iter="3"]')!, { key: "Enter" });
    expect(container.querySelector("[data-variant]")!.getAttribute("data-variant")).toBe("3");
  });

  it("Use this variant confirms the change, then calls study_apply and rebuilds the part", async () => {
    const { container, c } = await mountView();
    const best = STUDY.iterations.find((i) => i.n === STUDY.best_id)!;
    fireEvent.click(screen.getByRole("button", { name: "Use this variant" }));
    const confirm = screen.getByRole("group", { name: "Confirm variant" });
    expect(confirm.textContent).toContain("thickness");
    expect(confirm.textContent).toContain(best.vars.thickness.toFixed(2));
    expect(calls("/apply")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Apply and rebuild" }));
    await waitFor(() => expect(calls("/apply")).toHaveLength(1));
    expect(calls("/apply")[0].path).toBe(`/projects/${FIXTURE_PROJECT}/studies/${STUDY.id}/apply`);
    expect(calls("/apply")[0].body).toEqual({ iteration: best.n });
    await waitFor(() => expect(container.querySelector("[data-applied]")).toBeTruthy());
    expect(core.state.parts.find((p) => p.part_id === "mount_plate")!.vars.thickness).toBe(best.vars.thickness);
    expect(c.applied.value?.n).toBe(best.n);
  });

  it("an infeasible variant needs 'Use anyway' and is sent with allow_infeasible", async () => {
    const { container } = await mountView();
    fireEvent.click(container.querySelector('[data-point="0"]')!);
    expect(screen.queryByRole("button", { name: "Use this variant" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Use anyway" }));
    expect(screen.getByRole("group", { name: "Confirm variant" }).textContent).toContain("This variant fails the spec.");
    fireEvent.click(screen.getByRole("button", { name: "Apply and rebuild" }));
    await waitFor(() => expect(calls("/apply")).toHaveLength(1));
    expect(calls("/apply")[0].body).toEqual({ iteration: 0, allow_infeasible: true });
  });

  it("a refusal is shown in plain words", async () => {
    const stale = new CoreError("stale_version", "mount_plate is at version 4, not 3", { status: 409 });
    expect(errorWords(stale)).toMatch(/^The part changed since this study/);
    const { c } = await mountView();
    core.state.spec.studies.get(STUDY.id)!.status = "running";
    await c.refreshStudies();
    expect(await c.apply(c.studies.value[0], STUDY.best_id!)).toBe(false);
    expect(c.applyError.value).toMatch(/still running/);
  });
});

describe("console panel", () => {
  it("is hidden without a spec, and opens to the spec and the latest study", async () => {
    await start();
    conn.value = "open";
    const empty = new SpecController(() => client);
    const r1 = render(<SpecPanel c={empty} />);
    await waitFor(() => expect(empty.spec.value).not.toBeNull());
    expect(r1.container.querySelector("[data-spec-panel]")).toBeNull();
    r1.unmount();
    await core.stop();

    await start({ spec: "fixture", studies: "fixture" });
    conn.value = "open";
    const c = new SpecController(() => client);
    const { container } = render(<SpecPanel c={c} />);
    await waitFor(() => expect(container.querySelector("[data-spec-panel]")).toBeTruthy());
    const bar = screen.getByRole("button", { expanded: false });
    await waitFor(() => expect(bar.textContent).toContain("2 pass"));
    expect(bar.textContent).toContain("done");
    fireEvent.click(bar);
    expect(container.querySelector("[data-spec-card]")).toBeTruthy();
    expect(container.querySelector(`[data-study="${STUDY.id}"]`)).toBeTruthy();
  });

  it("live: study_progress refreshes the studies", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      await start({ spec: "fixture" });
      const c = new SpecController(() => client);
      await c.load();
      expect(c.studies.value).toHaveLength(0);
      core.state.spec.studies.set(STUDY.id, { ...structuredClone(STUDY), project_id: FIXTURE_PROJECT, status: "running", best_id: null, iterations: STUDY.iterations.slice(0, 2) });
      c.selectIteration(1);
      c.onEvent({ type: "study_progress", run_id: "r", ts: 0, data: { study_id: STUDY.id, n: 2, total: 6 } });
      expect(c.progress.value.get(STUDY.id)).toEqual({ n: 2, total: 6 });
      await vi.advanceTimersByTimeAsync(500);
      vi.useRealTimers();
      await waitFor(() => expect(c.studies.value[0]?.iterations).toHaveLength(2));
      const { container } = render(<StudyView c={c} study={c.studies.value[0]} requirements={SPEC.requirements} />);
      expect(container.querySelector("[role=progressbar]")!.getAttribute("aria-valuenow")).toBe("2");
      expect(container.querySelector("[data-study-status]")!.textContent).toContain("2/6");
      expect((screen.getByRole("button", { name: "Use anyway" }) as HTMLButtonElement).disabled).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});
