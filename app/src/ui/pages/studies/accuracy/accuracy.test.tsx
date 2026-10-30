// Plan 6 controls against the fake core's assumed schemas (test/fake-core/accuracy.ts): the
// generic form still renders every new setting, and the custom pieces write the shapes the core
// validates: the print direction gizmo, contact pairs (with a two-face pick), the fatigue cycle,
// plus the result sections, the OpenFOAM card and the calibration panel.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { useState } from "preact/hooks";
import { afterEach, describe, expect, it, vi } from "vitest";
import { p6Types } from "../../../../../test/fake-core/accuracy";
import type { Calibration, CalibrationBody } from "../../../../api/accuracy";
import { studyRecordSchema, studyTypeInfoSchema, type StudyMaterial, type StudyRecord, type StudySolvers } from "../../../../api/studies";
import type { MisesEvent } from "../../../../api/types";
import { validate, withDefaults, type Path } from "../formSchema";
import { STARTERS } from "../model";
import { ResultPanel } from "../ResultPanel";
import { SchemaForm } from "../SchemaForm";
import { CalibrationPanel, type CalibrationClient } from "./Calibration";
import { OpenFoamCard } from "./OpenFoamCard";

const DIR = resolve(import.meta.dirname, "../../../../../../core/tests/fixtures/ui");
const read = (n: string): unknown => JSON.parse(readFileSync(resolve(DIR, n), "utf8"));
const TYPES = p6Types(read("study_types.json") as { id: string; schema: unknown }[]).map((x) => studyTypeInfoSchema.parse(x));
const STATIC = TYPES.find((t) => t.id === "static")!;
const PLA = { ...(read("study_materials.json") as { builtin: StudyMaterial[] }).builtin[0], orthotropic: { e_xy_mpa: 2300, e_z_mpa: 1840, strength_z_mpa: 31 } } as StudyMaterial;

afterEach(cleanup);

/** The static form with its state, like the Studies page holds it. */
function Harness({ onValue, picking = null, onPick }: { onValue: (v: Record<string, unknown>) => void; picking?: Path | null; onPick?: (p: Path | null, then?: Path) => void }) {
  const [v, setV] = useState<Record<string, unknown>>(() => withDefaults(STATIC.schema, { ...STARTERS.static, material: { name: "PLA" } }));
  onValue(v);
  return <SchemaForm schema={STATIC.schema} value={v} onChange={setV} materials={["PLA"]} materialRows={[PLA]} picking={picking} onPickFace={onPick}
    errors={validate(v, STATIC.schema, STATIC.schema)} />;
}

describe("Plan 6 settings in the generated form", () => {
  it("print direction: off by default, the gizmo writes an axis, the orthotropic data shows", () => {
    let v: Record<string, unknown> = {};
    render(<Harness onValue={(x) => { v = x; }} />);
    expect(v.print_direction).toBeNull();
    fireEvent.click(screen.getByTestId("print-direction-on"));
    expect(v.print_direction).toBe("+z");
    expect(screen.getByTestId("print-direction-text").textContent).toBe("layers stack along +z (printed flat)");
    expect(screen.getByTestId("ortho-note").textContent).toBe("E 2,300 in-plane · 1,840 across layers MPa · Z strength 31 MPa");
    const x = screen.getByRole("radio", { name: "layers stack along +x" });
    fireEvent.click(x);
    expect(v.print_direction).toBe("+x");
    expect(x.getAttribute("aria-checked")).toBe("true");
    // arrow keys move the choice
    fireEvent.keyDown(x, { key: "ArrowRight" });
    expect(v.print_direction).toBe("-x");
    expect(validate(v, STATIC.schema, STATIC.schema)).toEqual([]);
  });

  it("nonlinear: the generic switch and fields, then contact pairs with face A / face B", () => {
    let v: Record<string, unknown> = {};
    render(<Harness onValue={(x) => { v = x; }} />);
    const group = screen.getByText("Nonlinear").closest("fieldset")!;
    fireEvent.click(within(group).getByRole("checkbox"));
    expect(v.nonlinear).toEqual({ large_deflection: true, plasticity: false, steps: 10, contact: [] });
    expect(screen.getByText("Large deflection")).toBeTruthy();
    expect(screen.getByText("Load steps")).toBeTruthy();
    fireEvent.click(screen.getByTestId("add-contact-pair"));
    expect((v.nonlinear as { contact: unknown[] }).contact).toEqual([{ kind: "faces", master: "top", slave: "bottom", friction: 0.2 }]);
    fireEvent.change(screen.getByLabelText("Contact 1 face A"), { target: { value: "x-min" } });
    fireEvent.input(screen.getByLabelText("Contact 1 friction"), { target: { value: "0.35" } });
    expect((v.nonlinear as { contact: unknown[] }).contact[0]).toEqual({ kind: "faces", master: "x-min", slave: "bottom", friction: 0.35 });
    fireEvent.change(screen.getByLabelText("Contact 1 kind"), { target: { value: "rigid" } });
    expect((v.nonlinear as { contact: unknown[] }).contact[0]).toEqual({ kind: "rigid", face: "bottom", friction: 0.35 });
    expect(validate(v, STATIC.schema, STATIC.schema)).toEqual([]);
  });

  it("Pick pair arms face A then face B", () => {
    const onPick = vi.fn();
    let v: Record<string, unknown> = {};
    const { rerender } = render(<Harness onValue={(x) => { v = x; }} onPick={onPick} />);
    fireEvent.click(within(screen.getByText("Nonlinear").closest("fieldset")!).getByRole("checkbox"));
    fireEvent.click(screen.getByTestId("add-contact-pair"));
    fireEvent.click(screen.getByTestId("pick-pair"));
    expect(onPick).toHaveBeenCalledWith(["nonlinear", "contact", 0, "master"], ["nonlinear", "contact", 0, "slave"]);
    rerender(<Harness onValue={(x) => { v = x; }} onPick={onPick} picking={["nonlinear", "contact", 0, "slave"]} />);
    expect(screen.getByTestId("pick-pair").textContent).toBe("Now click face B…");
    expect(screen.getByTestId("contact-pair").getAttribute("data-armed")).toBe("b");
    void v;
  });

  it("fatigue: cycle shape, life presets in words, min ÷ max only for min-max", () => {
    let v: Record<string, unknown> = {};
    render(<Harness onValue={(x) => { v = x; }} />);
    fireEvent.click(screen.getByTestId("fatigue-on"));
    expect(v.fatigue).toEqual({ cycles: 1e6, cycle: "fully_reversed", min_ratio: 0, mean_correction: "goodman" });
    expect(screen.getByTestId("fatigue-cycles-text").textContent).toBe("1 million cycles");
    expect(screen.queryByLabelText("Min ÷ max (R)")).toBeNull();
    fireEvent.click(screen.getByRole("radio", { name: "Min to max" }));
    fireEvent.input(screen.getByLabelText("Min ÷ max (R)"), { target: { value: "0.2" } });
    fireEvent.click(screen.getByRole("button", { name: /10⁹/ }));
    expect(v.fatigue).toEqual({ cycles: 1e9, cycle: "min_max", min_ratio: 0.2, mean_correction: "goodman" });
    expect(screen.getByTestId("cycle-trace").getAttribute("aria-label")).toBe("Load cycle from 0.2 to 1 times the case's load");
    expect(validate(v, STATIC.schema, STATIC.schema)).toEqual([]);
  });

  it("the wind solver offers OpenFOAM by name", () => {
    const wind = TYPES.find((t) => t.id === "wind")!;
    render(<SchemaForm schema={wind.schema} value={withDefaults(wind.schema, {})} onChange={() => {}} />);
    const opt = screen.getByRole("option", { name: "OpenFOAM k-ω SST (Docker, optional)" }) as HTMLOptionElement;
    expect(opt.value).toBe("openfoam");
  });

  it("the material picker carries the calibration badge", () => {
    render(<Harness onValue={() => {}} />);
    expect(screen.getByRole("option", { name: "PLA · datasheet / estimate" })).toBeTruthy();
  });
});

describe("Plan 6 results", () => {
  const base = studyRecordSchema.parse(read("study_static.json"));
  const rec = (results: Record<string, unknown>): StudyRecord => ({ ...base, results: { ...base.results, ...results } });

  it("nonlinear: converged at the full load, peak plastic strain and contact pressure", () => {
    render(<ResultPanel rec={rec({ nonlinear: { converged: true, increments: 12, load_reached_pct: 100, plasticity: true, large_deflection: true, contact_pairs: 1, max_plastic_strain: 0.0042, max_contact_pressure_mpa: 18.4 } })}
      charts={null} onAsk={() => {}} onOpenRun={() => {}} onImmersive={() => {}} asked={null} />);
    const s = screen.getByTestId("nonlinear-result");
    expect(s.textContent).toContain("converged at the full load in 12 increments");
    expect(s.textContent).toContain("0.42%");
    expect(s.textContent).toContain("18.4 MPa");
  });

  it("nonlinear: a solve that stopped early says so", () => {
    render(<ResultPanel rec={rec({ nonlinear: { converged: false, load_reached_pct: 62 } })} charts={null} onAsk={() => {}} onOpenRun={() => {}} onImmersive={() => {}} asked={null} />);
    expect(screen.getByTestId("nonlinear-result").textContent).toContain("stopped at 62% of the load");
  });

  it("fatigue: life against the required life, the label, and the S-N chart", () => {
    render(<ResultPanel rec={rec({ fatigue: { cycles_to_failure: 3.2e5, required_cycles: 1e6, safety_factor: 0.86, sigma_a_mpa: 6, sigma_m_mpa: 0, sigma_ar_mpa: 6,
      sn: { sf_mpa: 76, b: -0.085, source: "estimate", label: "S-N estimate" }, mean_correction: "goodman", label: "fatigue estimate" } })}
      charts={null} onAsk={() => {}} onOpenRun={() => {}} onImmersive={() => {}} asked={null} />);
    const s = screen.getByTestId("fatigue-result");
    expect(screen.getByTestId("fatigue-label").textContent).toBe("fatigue estimate");
    expect(s.textContent).toContain("fails before the required life");
    expect(screen.getByTestId("fatigue-life").textContent).toBe("320 thousand");
    expect(screen.getByTestId("sn-svg").getAttribute("aria-label")).toContain("lasts 320 thousand cycles; 1 million are required");
    expect(screen.getByTestId("sn-point").getAttribute("class")).toContain("is-fail");
  });
});

describe("OpenFOAM card", () => {
  const solvers = (status: string, fix: unknown): StudySolvers => ({
    ccx: { name: "CalculiX", found: true, version: "2.23", install: "x" }, gmsh: { name: "gmsh", found: true, version: "4", install: "x" },
    cfd: [{ id: "openfoam", name: "OpenFOAM", status, detail: "the pinned image is not downloaded yet", fix }],
  });

  it("downloads the image on a click and streams its log", async () => {
    const listeners: ((e: MisesEvent) => void)[] = [];
    const install = vi.fn().mockResolvedValue({ ok: true, fix: null });
    const onInstalled = vi.fn();
    render(<OpenFoamCard solvers={solvers("missing", { kind: "install", label: "Download OpenFOAM image", dep: "openfoam" })} chosen
      client={() => ({ systemInstall: install })} onInstalled={onInstalled} subscribe={(cb) => { listeners.push(cb); return () => {}; }} />);
    expect(screen.getByTestId("openfoam-card").textContent).toContain("runs on the built-in solver");
    fireEvent.click(screen.getByTestId("openfoam-install"));
    expect(install).toHaveBeenCalledWith("openfoam");
    act(() => { for (const l of listeners) l({ type: "system_install", run_id: null, ts: 1, data: { dep: "openfoam", line: "pulling layer 1/3", done: false, ok: false } }); });
    expect(screen.getByLabelText("OpenFOAM install log").textContent).toBe("pulling layer 1/3");
    act(() => { for (const l of listeners) l({ type: "system_install", run_id: null, ts: 2, data: { dep: "openfoam", line: "", done: true, ok: true } }); });
    expect(onInstalled).toHaveBeenCalled();
  });

  it("ready and not chosen: nothing to say", () => {
    const { container } = render(<OpenFoamCard solvers={solvers("ok", null)} chosen={false} client={() => ({ systemInstall: vi.fn() })} onInstalled={() => {}} subscribe={() => () => {}} />);
    expect(container.textContent).toBe("");
  });
});

describe("calibration panel", () => {
  function fake(): CalibrationClient & { saved: CalibrationBody[] } {
    const saved: CalibrationBody[] = [];
    let cal: Calibration | null = null;
    return {
      saved,
      calibrations: vi.fn(async () => (cal ? [cal] : [])),
      printers: vi.fn(async () => [{ id: "p1", kind: "prusalink", host: "h", name: "MK4", model: "MK4", has_key: false }]),
      saveCalibration: vi.fn(async (b: CalibrationBody) => {
        saved.push(b);
        cal = { id: "cal_1", material: b.material, printer_profile: b.printer_profile, tests: b.tests, applied: false, created_at: 1, updated_at: 1,
          fit: { e_mpa: 2048, yield_mpa: 31.25, uts_mpa: 31.25, knockdown: 0.61, n: b.tests.length, scatter_pct: 5.7, confidence: "medium",
            per_test: [{ index: 0, kind: "tensile", e_mpa: null, strength_mpa: 32.5, residual_pct: 4, used: true }, { index: 1, kind: "bend", e_mpa: 2048, strength_mpa: null, residual_pct: 0, used: true }],
            datasheet: { e_mpa: 2300, yield_mpa: 51 }, warnings: ["fewer than 3 tests: the scatter is not known well"] } };
        return cal;
      }),
      applyCalibration: vi.fn(async () => ({ calibration: { ...cal!, applied: true }, material: { name: "PLA", calibration: { n_tests: 2 } } })),
      deleteCalibration: vi.fn(),
    };
  }

  it("enter a tensile bar and a bend, see what each implies, fit, see the scatter, apply", async () => {
    const f = fake();
    const onApplied = vi.fn();
    render(<CalibrationPanel client={() => f} materials={[PLA]} studies={[]} initialMaterial="PLA" onApplied={onApplied} onClose={() => {}} />);
    await waitFor(() => expect((screen.getByTestId("cal-printer") as HTMLInputElement).value).toBe("MK4"));
    expect(screen.getByTestId("cal-badge").textContent).toBe("datasheet / estimate");
    expect((screen.getByTestId("cal-fit") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByTestId("cal-add-tensile"));
    fireEvent.input(screen.getByLabelText("Test 1 break force (N)"), { target: { value: "1300" } });
    expect(screen.getAllByTestId("cal-implied")[0].textContent).toContain("32.5 MPa strength");
    fireEvent.click(screen.getByTestId("cal-add-bend"));
    fireEvent.input(screen.getByLabelText("Test 2 load (N)"), { target: { value: "40" } });
    expect((screen.getByTestId("cal-fit") as HTMLButtonElement).disabled).toBe(true); // deflection missing
    fireEvent.input(screen.getByLabelText("Test 2 deflection (mm)"), { target: { value: "2" } });
    expect(screen.getAllByTestId("cal-implied")[1].textContent).toContain("E 2,048 MPa");
    await act(async () => { fireEvent.click(screen.getByTestId("cal-fit")); });
    await waitFor(() => expect(screen.getByTestId("cal-result")).toBeTruthy());
    expect(f.saved[0]).toEqual({ material: "PLA", printer_profile: "MK4", tests: [
      { kind: "tensile", break_n: 1300, width_mm: 10, thickness_mm: 4, elongation_pct: null },
      { kind: "bend", span_mm: 64, width_mm: 10, thickness_mm: 4, load_n: 40, deflection_mm: 2 },
    ] });
    expect(screen.getByTestId("cal-e").textContent).toBe("2,048 MPa");
    expect(screen.getByTestId("cal-confidence").textContent).toBe("medium confidence");
    const charts = screen.getAllByTestId("cal-scatter");
    expect(charts.map((c) => c.getAttribute("data-quantity"))).toEqual(["Stiffness E", "Strength"]);
    expect(within(charts[0]).getByRole("img").getAttribute("aria-label")).toBe("Stiffness E: 1 test, fitted 2,048 MPa, scatter ±5.7%, datasheet 2,300 MPa.");
    await act(async () => { fireEvent.click(screen.getByTestId("cal-apply")); });
    await waitFor(() => expect(screen.getByTestId("cal-note").textContent).toBe("PLA is now calibrated (2 tests): new studies use the fitted values."));
    expect(onApplied).toHaveBeenCalled();
  });
});
