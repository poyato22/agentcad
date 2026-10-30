// Plan 6 controls against the real S3 core's schemas and records (core/tests/fixtures/ui/s3_*)
// plus the fake core's assumed S1 / S2 additions: the generic form still renders every new
// setting, and the custom pieces write the shapes the core validates (anisotropy with the print
// direction, contact pairs with a two-face pick, the fatigue cycle, the printer), the result
// sections, the OpenFOAM card, and the calibration panel (fit preview, confirm before saving).
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { useState } from "preact/hooks";
import { afterEach, describe, expect, it, vi } from "vitest";
import { p6Types } from "../../../../../test/fake-core/accuracy";
import { calibrationPreviewSchema, calibrationSchema, type Calibration, type CalibrationBody } from "../../../../api/accuracy";
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
const TYPES = p6Types(read("s3_types.json") as { id: string; schema: unknown }[]).map((x) => studyTypeInfoSchema.parse(x));
const STATIC = TYPES.find((t) => t.id === "static")!;
const PLA = (read("s3_materials.json") as { builtin: StudyMaterial[] }).builtin.find((m) => m.name === "PLA")!;
const SAVED = calibrationSchema.parse(read("s3_cal_save.json"));
const PREVIEW = calibrationPreviewSchema.parse(read("s3_cal_fit.json"));

afterEach(cleanup);

/** The static form with its state, like the Studies page holds it. */
function Harness({ onValue, picking = null, onPick, calibrations = [] }: {
  onValue: (v: Record<string, unknown>) => void; picking?: Path | null; onPick?: (p: Path | null, then?: Path) => void; calibrations?: Calibration[];
}) {
  const [v, setV] = useState<Record<string, unknown>>(() => withDefaults(STATIC.schema, { ...STARTERS.static, material: { name: "PLA" } }));
  onValue(v);
  return <SchemaForm schema={STATIC.schema} value={v} onChange={setV} materials={["PLA"]} materialRows={[PLA]} calibrations={calibrations}
    picking={picking} onPickFace={onPick} errors={validate(v, STATIC.schema, STATIC.schema)} />;
}
const mat = (v: Record<string, unknown>) => v.material as Record<string, unknown>;

describe("Plan 6 settings in the generated form", () => {
  it("print direction: material.anisotropy is off (null) by default; the gizmo writes the S3 shape", () => {
    let v: Record<string, unknown> = {};
    render(<Harness onValue={(x) => { v = x; }} />);
    expect(mat(v).anisotropy).toBeNull();
    fireEvent.click(screen.getByTestId("print-direction-on"));
    expect(mat(v).anisotropy).toEqual({ print_direction: "+z" });
    expect(screen.getByTestId("print-direction-text").textContent).toBe("layers stack along +z (printed flat)");
    expect(screen.getByTestId("ortho-note").textContent).toContain("typical ratios (estimate)");
    const x = screen.getByRole("radio", { name: "layers stack along +x" });
    fireEvent.click(x);
    expect(mat(v).anisotropy).toEqual({ print_direction: "+x" });
    fireEvent.keyDown(x, { key: "ArrowRight" });
    expect((mat(v).anisotropy as Record<string, unknown>).print_direction).toBe("-x");
    fireEvent.input(screen.getByLabelText("Z ÷ in-plane strength (empty: typical)"), { target: { value: "0.5" } });
    expect(mat(v).anisotropy).toEqual({ print_direction: "-x", strength_z_ratio: 0.5 });
    expect(validate(v, STATIC.schema, STATIC.schema)).toEqual([]);
    fireEvent.click(screen.getByTestId("print-direction-on"));
    expect(mat(v).anisotropy).toBeNull();
  });

  it("nonlinear (assumed S1): the generic switch and fields, then contact pairs with face A / face B", () => {
    let v: Record<string, unknown> = {};
    render(<Harness onValue={(x) => { v = x; }} />);
    const group = screen.getByText("Nonlinear").closest("fieldset")!;
    fireEvent.click(within(group).getByRole("checkbox"));
    expect(v.nonlinear).toEqual({ large_deflection: true, plasticity: false, steps: 10, contact: [] });
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
    const { rerender } = render(<Harness onValue={() => {}} onPick={onPick} />);
    fireEvent.click(within(screen.getByText("Nonlinear").closest("fieldset")!).getByRole("checkbox"));
    fireEvent.click(screen.getByTestId("add-contact-pair"));
    fireEvent.click(screen.getByTestId("pick-pair"));
    expect(onPick).toHaveBeenCalledWith(["nonlinear", "contact", 0, "master"], ["nonlinear", "contact", 0, "slave"]);
    rerender(<Harness onValue={() => {}} onPick={onPick} picking={["nonlinear", "contact", 0, "slave"]} />);
    expect(screen.getByTestId("pick-pair").textContent).toBe("Now click face B…");
    expect(screen.getByTestId("contact-pair").getAttribute("data-armed")).toBe("b");
  });

  it("fatigue: the S3 keys (cycles, cycle, min_pct, sn), never min_ratio or mean_correction", () => {
    let v: Record<string, unknown> = {};
    render(<Harness onValue={(x) => { v = x; }} />);
    fireEvent.click(screen.getByTestId("fatigue-on"));
    expect(v.fatigue).toEqual({ cycles: 1e6, cycle: "zero_to_max", min_pct: 0 });
    expect(screen.getByTestId("fatigue-cycles-text").textContent).toBe("1 million cycles");
    expect(screen.queryByLabelText("Min load")).toBeNull();
    fireEvent.click(screen.getByRole("radio", { name: "Min to max" }));
    fireEvent.input(screen.getByLabelText("Min load"), { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: /10⁹/ }));
    expect(screen.getByTestId("cycle-trace").getAttribute("aria-label")).toBe("Load cycle from 20% to 100% of the case's load");
    fireEvent.click(screen.getByTestId("fatigue-sn-on"));
    fireEvent.input(screen.getByLabelText("S-N A (MPa)"), { target: { value: "60" } });
    expect(v.fatigue).toEqual({ cycles: 1e9, cycle: "min_max", min_pct: 20, sn: { a_mpa: 60, b: -0.1, source: "data sheet" } });
    expect(validate(v, STATIC.schema, STATIC.schema)).toEqual([]);
  });

  it("the printer: written to material.printer; the badge says calibrated only on the printer it applies to", () => {
    let v: Record<string, unknown> = {};
    render(<Harness onValue={(x) => { v = x; }} calibrations={[SAVED]} />);
    expect(screen.getByRole("option", { name: "PLA · datasheet / estimate" })).toBeTruthy();
    fireEvent.input(screen.getByTestId("material-printer"), { target: { value: "MK4" } });
    expect(mat(v).printer).toBe("MK4");
    expect(screen.getByRole("option", { name: "PLA · calibrated (7 tests)" })).toBeTruthy();
    expect(validate(v, STATIC.schema, STATIC.schema)).toEqual([]);
  });

  it("the wind solver offers OpenFOAM by name (assumed S2)", () => {
    const wind = TYPES.find((t) => t.id === "wind")!;
    render(<SchemaForm schema={wind.schema} value={withDefaults(wind.schema, {})} onChange={() => {}} />);
    const opt = screen.getByRole("option", { name: "OpenFOAM k-ω SST (Docker, optional)" }) as HTMLOptionElement;
    expect(opt.value).toBe("openfoam");
  });
});

describe("Plan 6 results", () => {
  const S3 = studyRecordSchema.parse(read("s3_static.json"));
  const panel = (rec: StudyRecord) => render(<ResultPanel rec={rec} charts={null} onAsk={() => {}} onOpenRun={() => {}} onImmersive={() => {}} asked={null} />);

  it("the real S3 study: fatigue fails before 10⁶, across the layers governs, the S-N chart at σa = A·N^b", () => {
    panel(S3);
    const f = screen.getByTestId("fatigue-result");
    expect(screen.getByTestId("fatigue-label").textContent).toBe("fatigue estimate");
    expect(f.textContent).toContain("fails before the required life");
    expect(f.textContent).toContain("governs: across layers (Z)");
    expect(screen.getByTestId("fatigue-life").textContent).toBe("874 thousand");
    expect(screen.getByTestId("sn-point").getAttribute("class")).toContain("is-fail");
    expect(screen.getByTestId("sn-svg").getAttribute("aria-label")).toBe("S-N curve. The hotspot's amplitude 3.56 MPa lasts 874 thousand cycles; 1 million are required.");
    const l = screen.getByTestId("layers-result");
    expect(l.textContent).toContain("Tension across the layers governs");
    expect(l.textContent).toContain("Z allowable 21.4 MPa");
  });

  it("the calibrated study names its basis on the result", () => {
    panel(studyRecordSchema.parse(read("s3_static_calibrated.json")));
    expect(screen.getByTestId("solver-label").textContent).toContain("PLA (calibrated (7 tests))");
  });

  it("a run-out says so", () => {
    const r = S3.results as Record<string, unknown>;
    panel({ ...S3, results: { ...r, fatigue: { ...(r.fatigue as object), runout: true, cycles_to_failure: 1e12, safety_factor: null } } });
    expect(screen.getByTestId("fatigue-life").textContent).toBe("run-out");
    expect(screen.getByTestId("fatigue-result").textContent).toContain("lasts the required life");
  });

  it("nonlinear (assumed S1): converged at the full load, peak plastic strain and contact pressure", () => {
    panel({ ...S3, results: { ...S3.results, nonlinear: { converged: true, increments: 12, load_reached_pct: 100, plasticity: true, large_deflection: true, contact_pairs: 1, max_plastic_strain: 0.0042, max_contact_pressure_mpa: 18.4 } } });
    const s = screen.getByTestId("nonlinear-result");
    expect(s.textContent).toContain("converged at the full load in 12 increments");
    expect(s.textContent).toContain("0.42%");
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

  it("a dep the app does not know is never sent (the core's allowlist is not the only guard)", () => {
    const install = vi.fn();
    render(<OpenFoamCard solvers={solvers("missing", { kind: "install", label: "Download", dep: "../../etc" })} chosen
      client={() => ({ systemInstall: install })} onInstalled={() => {}} subscribe={() => () => {}} />);
    expect(screen.queryByTestId("openfoam-install")).toBeNull();
    expect(install).not.toHaveBeenCalled();
  });

  it("ready and not chosen: nothing to say", () => {
    const { container } = render(<OpenFoamCard solvers={solvers("ok", null)} chosen={false} client={() => ({ systemInstall: vi.fn() })} onInstalled={() => {}} subscribe={() => () => {}} />);
    expect(container.textContent).toBe("");
  });
});

describe("calibration panel", () => {
  function fake(saved: Calibration[] = []): CalibrationClient & { fits: CalibrationBody[]; saves: CalibrationBody[] } {
    const fits: CalibrationBody[] = [];
    const saves: CalibrationBody[] = [];
    return {
      fits, saves,
      calibrations: vi.fn(async () => saved),
      printers: vi.fn(async () => [{ id: "p1", kind: "prusalink", host: "h", name: "MK4", model: "MK4", has_key: false }]),
      fitCalibration: vi.fn(async (b: CalibrationBody) => { fits.push(b); return PREVIEW; }),
      saveCalibration: vi.fn(async (b: CalibrationBody) => { saves.push(b); return SAVED; }),
      deleteCalibration: vi.fn(),
    };
  }

  it("enter tests, Fit shows the core's preview; Apply opens a confirmation (before → after, n, confidence); an untrusted Confirm saves nothing", async () => {
    const f = fake();
    render(<CalibrationPanel client={() => f} materials={[PLA]} studies={[]} initialMaterial="PLA" initialPrinter="MK4" onApplied={() => {}} onClose={() => {}} />);
    await waitFor(() => expect(f.calibrations).toHaveBeenCalled());
    expect((screen.getByTestId("cal-printer") as HTMLInputElement).value).toBe("MK4");
    expect(screen.getByTestId("cal-badge").textContent).toBe("datasheet / estimate");
    expect((screen.getByTestId("cal-fit") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByTestId("cal-add-tensile"));
    fireEvent.input(screen.getByLabelText("Test 1 break force (N)"), { target: { value: "1300" } });
    expect(screen.getAllByTestId("cal-implied")[0].textContent).toContain("32.5 MPa strength");
    fireEvent.click(screen.getByTestId("cal-add-bend"));
    fireEvent.input(screen.getByLabelText("Test 2 load (N)"), { target: { value: "40" } });
    fireEvent.input(screen.getByLabelText("Test 2 deflection (mm)"), { target: { value: "2" } });
    expect(screen.getAllByTestId("cal-implied")[1].textContent).toContain("E 2,048 MPa");
    await act(async () => { fireEvent.click(screen.getByTestId("cal-fit")); });
    expect(f.fits[0]).toEqual({ material: "PLA", printer: "MK4", tensile: [{ force_n: 1300, width_mm: 10, thickness_mm: 4, orientation: "xy" }],
      bend: [{ span_mm: 64, width_mm: 10, thickness_mm: 4, load_n: 40, deflection_mm: 2, orientation: "xy" }], part: [] });
    // the result is the core's fit response, not an echo of what was typed
    expect(screen.getByTestId("cal-e").textContent).toBe("2,036 MPa");
    expect(screen.getByTestId("cal-strength").textContent).toBe("31 MPa");
    expect(screen.getByTestId("cal-confidence").textContent).toBe("low confidence: add tests");
    expect(screen.getAllByTestId("cal-scatter").map((c) => c.getAttribute("data-quantity"))).toEqual(["Stiffness E", "Strength"]);
    // Apply… only opens the confirmation
    fireEvent.click(screen.getByTestId("cal-apply"));
    expect(f.saveCalibration).not.toHaveBeenCalled();
    const box = screen.getByTestId("cal-confirm");
    expect(box.textContent).toContain("Apply to PLA on MK4?");
    const rows = within(box).getAllByRole("row").map((r) => r.textContent);
    expect(rows).toContain("Young's modulus2,300 MPa2,036 MPa");
    expect(rows).toContain("strength51 MPa31 MPa");
    expect(screen.getByTestId("cal-confirm-basis").textContent).toBe("calibrated (7 tests) · low confidence: add tests");
    fireEvent.click(screen.getByTestId("cal-confirm-apply")); // synthetic: not a user's click
    await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
    expect(f.saveCalibration).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("cal-confirm-cancel"));
    expect(screen.queryByTestId("cal-confirm")).toBeNull();
    // an edit drops the preview: its result belonged to other numbers
    fireEvent.input(screen.getByLabelText("Test 1 break force (N)"), { target: { value: "1310" } });
    expect(screen.queryByTestId("cal-result")).toBeNull();
  });

  it("a saved record with the core's stored defaults shows as applied (no echo comparison)", async () => {
    const f = fake([SAVED]);
    render(<CalibrationPanel client={() => f} materials={[PLA]} studies={[]} initialMaterial="PLA" initialPrinter="MK4" onApplied={() => {}} onClose={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("cal-result").getAttribute("data-state")).toBe("saved"));
    expect(screen.getAllByTestId("cal-test")).toHaveLength(7);
    expect(screen.getByTestId("cal-applied").textContent).toBe("Applied: calibrated (7 tests) on MK4.");
    expect(screen.getByTestId("cal-badge").textContent).toBe("calibrated (7 tests)");
    expect(screen.queryByTestId("cal-apply")).toBeNull();
    // the same material on another printer is not calibrated
    fireEvent.input(screen.getByTestId("cal-printer"), { target: { value: "default" } });
    expect(screen.getByTestId("cal-badge").textContent).toBe("datasheet / estimate");
  });

  it("a printer key the core refuses is said at the field", async () => {
    const f = fake();
    render(<CalibrationPanel client={() => f} materials={[PLA]} studies={[]} initialMaterial="PLA" onApplied={() => {}} onClose={() => {}} />);
    expect((screen.getByTestId("cal-printer") as HTMLInputElement).value).toBe("default");
    fireEvent.input(screen.getByTestId("cal-printer"), { target: { value: "-MK4/x" } });
    expect(screen.getByRole("alert").textContent).toContain("1–40 letters");
  });
});
