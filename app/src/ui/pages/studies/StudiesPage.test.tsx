// The Studies page against the real core's answers: the type picker and the form generated from
// /sims/types, field validation before the round trip, the POST /sims body, phases and cancel by
// sim_id, results of every type with "guide only" and the fidelity note, compare, templates, the
// material editor, and "Ask agent to fix" with the full settings and result as data.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  studyChartsSchema, studyCompareSchema, studyMaterialsSchema, studyRecordSchema, studySolversSchema, studySummarySchema, studyTemplateSchema,
  studyTypeInfoSchema, STUDY_TYPES,
} from "../../../api/studies";
import type { PartSummary } from "../../../api/types";
import { actions, parts, resetStore } from "../../../state/store";
import { StudiesController, type StudiesClient } from "./controller";
import { resetStudiesPage, StudiesPage } from "./index";
import { TYPE_ORDER } from "./model";

const DIR = resolve(import.meta.dirname, "../../../../../core/tests/fixtures/ui");
const read = (n: string): unknown => JSON.parse(readFileSync(resolve(DIR, n), "utf8"));
const TYPES = (read("study_types.json") as unknown[]).map((x) => studyTypeInfoSchema.parse(x));
const LIST = (read("study_list.json") as unknown[]).map((x) => studySummarySchema.parse(x));
const REC = Object.fromEntries(STUDY_TYPES.map((t) => [t, studyRecordSchema.parse(read(`study_${t}.json`))]));
const CHARTS = Object.fromEntries(STUDY_TYPES.map((t) => [t, studyChartsSchema.parse(read(`study_${t}_charts.json`))]));
const BEAM: PartSummary = { part_id: "beam", vars: { length: 100 }, mtime: 1, metrics: {
  valid: true, volume_mm3: 10000, area_mm2: 4200, mass_g: 12.4, bbox: { min: [0, 0, 0], max: [100, 10, 10], size: [100, 10, 10] },
  center_of_mass: [50, 5, 5], faces: 6, edges: 12, holes: [], solids: 1,
  face_list: [{ id: "f0", type: "plane", center: [0, 5, 5], normal: [-1, 0, 0], area: 100 }, { id: "f1", type: "plane", center: [100, 5, 5], normal: [1, 0, 0], area: 100 }],
} };
const CUBE: PartSummary = { ...BEAM, part_id: "cube" };
const beamList = () => LIST.filter((s) => s.part_id === "beam");

function client() {
  const byId = new Map([...Object.values(REC)].map((r) => [r.sim_id, r]));
  return {
    studyTypes: vi.fn().mockResolvedValue(TYPES),
    studySolvers: vi.fn().mockResolvedValue(studySolversSchema.parse(read("study_solvers.json"))),
    listStudies: vi.fn(async (f: { partId?: string; runId?: string }) => (f.partId ? LIST.filter((s) => s.part_id === f.partId) : [])),
    getStudy: vi.fn(async (id: string) => byId.get(id) ?? { ...REC[LIST.find((s) => s.sim_id === id)?.type as "static" ?? "static"], sim_id: id }),
    studyCharts: vi.fn(async (id: string) => Object.values(CHARTS).find((c) => c.sim_id === id) ?? CHARTS.static),
    startStudy: vi.fn().mockResolvedValue({ run_id: "run9", status: "running" }),
    cancelStudy: vi.fn().mockResolvedValue({ sim_id: "x", status: "cancelling" }),
    compareStudies: vi.fn().mockResolvedValue(studyCompareSchema.parse(read("study_compare.json"))),
    studyTemplates: vi.fn().mockResolvedValue((read("study_templates.json") as unknown[]).map((x) => studyTemplateSchema.parse(x))),
    addStudyTemplate: vi.fn(async (b: { name: string; type: string; settings: Record<string, unknown> }) => ({ id: "tpl_0000000001", created_at: 1, ...b })),
    deleteStudyTemplate: vi.fn().mockResolvedValue({ deleted: "x" }),
    studyMaterials: vi.fn().mockResolvedValue(studyMaterialsSchema.parse(read("study_materials.json"))),
    addStudyMaterial: vi.fn(async (m: Record<string, unknown>) => ({ ...(read("study_material_add.json") as object), ...m })),
    deleteStudyMaterial: vi.fn().mockResolvedValue({ deleted: "x" }),
    cancel: vi.fn().mockResolvedValue({ ok: true }),
    getRun: vi.fn().mockResolvedValue({ status: "running" }),
    steps: vi.fn().mockResolvedValue([]),
    createRun: vi.fn().mockResolvedValue({ run_id: "fix1" }),
  };
}

const noGl = { createRenderer: () => { throw new Error("no WebGL in tests"); }, partGlb: async () => null, resultGlb: async () => null };
let c: ReturnType<typeof client>;
let ctl: StudiesController;
async function settle() { await act(async () => { await new Promise((r) => setTimeout(r, 20)); }); }
function mount() { return render(<StudiesPage controller={ctl} deps={{ view: noGl }} />); }

beforeEach(() => {
  resetStore(); resetStudiesPage();
  parts.value = [BEAM, CUBE];
  actions.selectPart("beam");
  c = client();
  ctl = new StudiesController(() => c as unknown as StudiesClient, { pollMs: 10 });
});
afterEach(() => { ctl.dispose(); cleanup(); });

describe("Studies page", () => {
  it("offers all six study types and renders each one's settings from its schema", async () => {
    mount();
    await settle();
    for (const t of STUDY_TYPES) {
      fireEvent.click(screen.getByTestId(`type-${t}`));
      await settle();
      expect(screen.getByTestId(`type-${t}`).getAttribute("aria-selected")).toBe("true");
      expect(screen.getByTestId("schema-form")).toBeTruthy();
    }
    // wind: speed with its unit and range; the advanced fold holds the lattice settings
    const speed = document.querySelector('[data-field="speed_m_s"]')!;
    expect(speed.textContent).toMatch(/Wind speed m\/s/);
    expect(speed.textContent).toMatch(/> 0, ≤ 150/);
    expect(document.querySelector(".sf-adv")?.textContent).toMatch(/Refinement|Iterations|Memory budget/);
    expect(screen.getByTestId("type-doc").textContent).toMatch(/low fidelity/);
  });

  it("the type tablist has roving focus: one Tab stop, ←/→ and Home/End move and select (M-9)", async () => {
    mount();
    await settle();
    fireEvent.click(screen.getByTestId(`type-${TYPE_ORDER[0]}`));
    await settle();
    const tabs = TYPE_ORDER.map((t) => screen.getByTestId(`type-${t}`));
    expect(tabs.map((b) => b.tabIndex)).toEqual(TYPE_ORDER.map((_, i) => (i === 0 ? 0 : -1)));
    fireEvent.keyDown(tabs[0], { key: "ArrowRight" });
    await settle();
    expect(screen.getByTestId(`type-${TYPE_ORDER[1]}`).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId(`type-${TYPE_ORDER[1]}`).tabIndex).toBe(0);
    fireEvent.keyDown(tabs[1], { key: "ArrowLeft" });
    fireEvent.keyDown(tabs[0], { key: "ArrowLeft" });     // wraps to the last
    await settle();
    expect(screen.getByTestId(`type-${TYPE_ORDER.at(-1)!}`).getAttribute("aria-selected")).toBe("true");
    fireEvent.keyDown(tabs[0], { key: "Home" });
    await settle();
    expect(screen.getByTestId(`type-${TYPE_ORDER[0]}`).getAttribute("aria-selected")).toBe("true");
  });

  it("static: the load-case list adds, selects and reorders cases; faces offer the part's face ids", async () => {
    mount();
    await settle();
    const cases = () => within(screen.getByTestId("load-cases")).getAllByTestId("load-case");
    expect(cases()).toHaveLength(1);
    fireEvent.click(screen.getByTestId("add-load-case"));
    await settle();
    expect(cases()).toHaveLength(2);
    expect(cases()[1].dataset.selected).toBe("true");
    // the new case is held like the first (bottom), not on the face it loads (B1-11)
    expect((document.getElementById("sf-load_cases-1-supports-0-face") as HTMLSelectElement).value)
      .toBe((document.getElementById("sf-load_cases-0-supports-0-face") as HTMLSelectElement).value);
    const face = document.getElementById("sf-load_cases-1-loads-0-face") as HTMLSelectElement;
    expect([...face.options].map((o) => o.value)).toEqual(expect.arrayContaining(["top", "x-max", "f0", "f1"]));
    fireEvent.change(face, { target: { value: "f1" } });
    const row = cases()[1];
    fireEvent.click(within(row).getAllByLabelText(/Move .* up/)[0]);
    await settle();
    expect((document.getElementById("sf-load_cases-0-loads-0-face") as HTMLSelectElement).value).toBe("f1");
    // rows are keyed by identity, not position: the moved row is the same element, still selected
    expect(cases()[0]).toBe(row);
    expect(cases()[0].dataset.selected).toBe("true");
    // removing the selected case moves the selection to its neighbour
    fireEvent.click(within(cases()[0]).getAllByLabelText(/^Remove /)[0]);
    await settle();
    expect(cases()).toHaveLength(1);
    expect(cases()[0].dataset.selected).toBe("true");
  });

  it("a bad range is said at its field and nothing is sent", async () => {
    mount();
    await settle();
    fireEvent.click(screen.getByTestId("type-modal"));
    await settle();
    fireEvent.input(document.getElementById("sf-n_modes")!, { target: { value: "500" } });
    await settle();
    fireEvent.click(screen.getByTestId("run-study"));
    await settle();
    expect(document.querySelector('[data-field="n_modes"] .sf-err')?.textContent).toBe("at most 30");
    expect(screen.getByTestId("run-error").textContent).toMatch(/n_modes at most 30/);
    expect(c.startStudy).not.toHaveBeenCalled();
  });

  it("run sends {type, part_id, settings}; progress by phase; cancel stops the study by its id", async () => {
    mount();
    await settle();
    fireEvent.click(screen.getByTestId("type-wind"));
    await settle();
    fireEvent.input(document.getElementById("sf-speed_m_s")!, { target: { value: "12" } });
    fireEvent.click(screen.getByTestId("run-study"));
    await settle();
    expect(c.startStudy).toHaveBeenCalledWith(expect.objectContaining({ type: "wind", part_id: "beam", settings: expect.objectContaining({ speed_m_s: 12, refinement: 2 }) }));
    c.listStudies.mockImplementation(async (f) => (f.runId ? [{ ...LIST[0], sim_id: "sim_aaaaaaaaaaaa", status: "running", phase: "solve", pct: 40, type: "wind", part_id: "beam", run_id: "run9" }] : beamList()));
    await act(async () => { ctl.onEvent({ type: "sim_progress", run_id: "run9", ts: 1, data: { sim_id: "sim_aaaaaaaaaaaa", part_id: "beam", type: "wind", stage: "solve", phase: "solve", pct: 40, detail: "lattice step 1600/4000" } }); });
    const prog = screen.getByTestId("study-progress");
    expect(prog.dataset.phase).toBe("solve");
    expect(within(prog).getByTestId("phase-bar").getAttribute("aria-valuetext")).toMatch(/lattice step 1600\/4000/);
    fireEvent.click(screen.getByTestId("cancel-study"));
    await settle();
    expect(c.cancelStudy).toHaveBeenCalledWith("sim_aaaaaaaaaaaa");
  });

  it.each(STUDY_TYPES)("a %s result keeps 'guide only' and the fidelity label in sight", async (t) => {
    const part = REC[t].part_id;
    actions.selectPart(part);
    c.listStudies.mockImplementation(async (f) => (f.partId ? [{ ...LIST.find((s) => s.sim_id === REC[t].sim_id) ?? LIST[0], sim_id: REC[t].sim_id, type: t, status: "done", part_id: part }] : []));
    mount();
    await settle();
    await waitFor(() => screen.getByTestId("study-result"));
    const r = screen.getByTestId("study-result");
    expect(r.dataset.type).toBe(t);
    expect(within(r).getByTestId("guide-label").textContent).toMatch(/GUIDE ONLY/);
    if (t === "wind") {
      expect(within(r).getByTestId("fidelity-note").textContent).toBe("low fidelity (preview) · ±40% (validated)");
      expect(within(r).getByTestId("result-note").textContent).toMatch(/skin-friction/);
      expect(within(r).getAllByTestId("metric").map((m) => m.dataset.key)).toEqual(expect.arrayContaining(["cd", "cl", "drag_n"]));
      expect(within(r).getByTestId("convergence-band").textContent).toMatch(/not the accuracy/);
      await waitFor(() => expect(r.querySelector('[data-chart="cd_vs_yaw"]')).toBeTruthy());
    }
    if (t === "drop") expect(within(r).getByTestId("fidelity-note").textContent).toMatch(/estimate/);
    if (t === "modal") await waitFor(() => expect(r.querySelector('[data-chart="modal_frequencies"]')).toBeTruthy());
  });

  it("compare two studies side by side", async () => {
    mount();
    await settle();
    const checks = screen.getAllByTestId("compare-check").filter((x) => !(x as HTMLInputElement).disabled);
    fireEvent.click(checks[0]); fireEvent.click(checks[1]);
    fireEvent.click(screen.getByTestId("compare"));
    await settle();
    expect(c.compareStudies).toHaveBeenCalledWith([expect.any(String), expect.any(String)]);
    const view = screen.getByTestId("compare-view");
    expect(within(view).getAllByTestId("compare-row").map((r) => r.dataset.key)).toContain("safety_factor");
    expect(view.querySelector('td[data-best="true"]')).toBeTruthy();
  });

  it("templates: save the setup, load one (switching the type)", async () => {
    mount();
    await settle();
    fireEvent.input(screen.getByTestId("template-name"), { target: { value: "Tip check" } });
    fireEvent.click(screen.getByTestId("template-save"));
    await settle();
    expect(c.addStudyTemplate).toHaveBeenCalledWith(expect.objectContaining({ name: "Tip check", type: "static" }));
    const breeze = (await c.studyTemplates()).find((t: { name: string }) => t.name === "Breeze")!;
    fireEvent.change(screen.getByTestId("template-pick"), { target: { value: breeze.id } });
    fireEvent.click(screen.getByTestId("template-load"));
    await settle();
    expect(screen.getByTestId("type-wind").getAttribute("aria-selected")).toBe("true");
    expect((document.getElementById("sf-speed_m_s") as HTMLInputElement).value).toBe("5");
  });

  it("the material editor saves a custom material, which the material field then offers", async () => {
    mount();
    await settle();
    fireEvent.click(screen.getByText("Edit materials"));
    const ed = screen.getByTestId("material-editor");
    fireEvent.input(within(ed).getByTestId("material-name"), { target: { value: "Spool CF" } });
    fireEvent.click(within(ed).getByTestId("material-save"));
    await settle();
    expect(c.addStudyMaterial).toHaveBeenCalledWith(expect.objectContaining({ name: "Spool CF", e_mpa: 2000, nu: 0.35 }));
    const sel = document.getElementById("sf-material-name") as HTMLSelectElement;
    expect([...sel.options].map((o) => o.value)).toContain("Spool CF");
  });

  it("ask agent to fix: a designer run with the full settings and result as untrusted data", async () => {
    mount();
    await settle();
    await waitFor(() => screen.getByTestId("ask-agent"));
    fireEvent.click(screen.getByTestId("ask-agent"));
    await settle();
    const body = c.createRun.mock.calls[0][0] as { agent: string; prompt: string; edit_scope: string[] };
    expect(body.agent).toBe("designer");
    expect(body.edit_scope).toEqual(["beam"]);
    expect(body.prompt).toMatch(/<study_result nonce="[0-9a-f]{24}">/);
    expect(body.prompt).toMatch(/"settings": \{/);
    expect(screen.getByTestId("asked").textContent).toMatch(/full settings and result/);
  });

  it("no CalculiX: FEM types say so, wind still runs", async () => {
    c.studySolvers.mockResolvedValue(studySolversSchema.parse({ ...(read("study_solvers.json") as object), ccx: { name: "CalculiX", found: false, version: null, install: "conda install -c conda-forge calculix" } }));
    mount();
    await settle();
    expect(screen.getByTestId("install-card")).toBeTruthy();
    expect((screen.getByTestId("run-study") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByTestId("type-wind"));
    await settle();
    expect(screen.queryByTestId("install-card")).toBeNull();
    expect((screen.getByTestId("run-study") as HTMLButtonElement).disabled).toBe(false);
  });
});
