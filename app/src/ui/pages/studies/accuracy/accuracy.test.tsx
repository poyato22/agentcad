// Plan 6 controls against the real merged core's schemas and records (core/tests/fixtures/ui:
// s3_* for S3, s1_* / s2_* for S1 / S2): the generic form still renders every new
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
const REJECTED = calibrationPreviewSchema.parse(read("s3_cal_fit_rejected.json"));
const REJECTED_SAVED = calibrationSchema.parse(read("s3_cal_save_rejected.json"));
const BASIS = "calibrated E (3), strength (3), Z strength (1), low confidence";

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

  it("nonlinear (S1): the generic switch and fields, then contact pairs (core ContactPair: sides a / b)", () => {
    let v: Record<string, unknown> = {};
    render(<Harness onValue={(x) => { v = x; }} />);
    const group = screen.getByText("Nonlinear").closest("fieldset")!;
    fireEvent.click(within(group).getByRole("checkbox"));
    // switched on with one effect (the core refuses a nonlinear block with none)
    expect(v.nonlinear).toEqual({ large_deflection: true, plasticity: false, tangent_modulus_mpa: null, contacts: [], rigid_supports: [], increments: 10, max_increments: 200, min_increment: 1e-5, penalty: 50 });
    fireEvent.click(screen.getByTestId("add-contact-pair"));
    const nl = () => v.nonlinear as { contacts: unknown[] };
    expect(nl().contacts).toEqual([{ kind: "contact", a: { face: "top", instance: null }, b: { face: "bottom", instance: null }, friction: 0.2 }]);
    fireEvent.change(screen.getByLabelText("Contact 1 face A"), { target: { value: "x-min" } });
    fireEvent.input(screen.getByLabelText("Contact 1 friction"), { target: { value: "0.35" } });
    expect(nl().contacts[0]).toEqual({ kind: "contact", a: { face: "x-min", instance: null }, b: { face: "bottom", instance: null }, friction: 0.35 });
    fireEvent.change(screen.getByLabelText("Contact 1 kind"), { target: { value: "bonded" } });
    expect(nl().contacts[0]).toMatchObject({ kind: "bonded", a: { face: "x-min" }, b: { face: "bottom" } });
    expect(screen.queryByLabelText("Contact 1 friction")).toBeNull();
    fireEvent.input(screen.getByLabelText("Contact 1 instance A"), { target: { value: "lid" } });
    expect(nl().contacts[0]).toMatchObject({ a: { face: "x-min", instance: "lid" } });
    expect(validate(v, STATIC.schema, STATIC.schema)).toEqual([]);
  });

  it("Pick pair arms face A then face B", () => {
    const onPick = vi.fn();
    const { rerender } = render(<Harness onValue={() => {}} onPick={onPick} />);
    fireEvent.click(within(screen.getByText("Nonlinear").closest("fieldset")!).getByRole("checkbox"));
    fireEvent.click(screen.getByTestId("add-contact-pair"));
    fireEvent.click(screen.getByTestId("pick-pair"));
    expect(onPick).toHaveBeenCalledWith(["nonlinear", "contacts", 0, "a", "face"], ["nonlinear", "contacts", 0, "b", "face"]);
    rerender(<Harness onValue={() => {}} onPick={onPick} picking={["nonlinear", "contacts", 0, "b", "face"]} />);
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
    expect(screen.getByRole("option", { name: `PLA · ${BASIS}` })).toBeTruthy();
    expect(validate(v, STATIC.schema, STATIC.schema)).toEqual([]);
  });

  it("the badge never says calibrated for a rejected record", () => {
    render(<Harness onValue={() => {}} calibrations={[REJECTED_SAVED]} />);
    fireEvent.input(screen.getByTestId("material-printer"), { target: { value: "MK4" } });
    expect(screen.getByRole("option", { name: "PLA · datasheet / estimate · calibration rejected" })).toBeTruthy();
  });

  it("the anisotropy ratio inputs stop at the core's bounds (E_z / E at most 1.2)", () => {
    render(<Harness onValue={() => {}} />);
    fireEvent.click(screen.getByTestId("print-direction-on"));
    expect(screen.getByLabelText("E across ÷ in-plane (empty: typical)").getAttribute("max")).toBe("1.2");
  });

  it("the wind solver offers OpenFOAM by name (S2)", () => {
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
    expect(screen.getByTestId("solver-label").textContent).toContain(`PLA (${BASIS})`);
  });

  it("a run-out says so", () => {
    const r = S3.results as Record<string, unknown>;
    panel({ ...S3, results: { ...r, fatigue: { ...(r.fatigue as object), runout: true, cycles_to_failure: 1e12, safety_factor: null } } });
    expect(screen.getByTestId("fatigue-life").textContent).toBe("run-out");
    expect(screen.getByTestId("fatigue-result").textContent).toContain("lasts the required life");
  });

  it("nonlinear (S1, the real records): converged at the full load, peak plastic strain; contact pressure", () => {
    panel(studyRecordSchema.parse(read("s1_static_nonlinear.json")));
    const s = screen.getByTestId("nonlinear-result");
    expect(s.textContent).toContain("converged at the full load in 13 increments (1 cutback)");
    expect(s.textContent).toContain("large deflection + plasticity");
    expect(s.textContent).toContain("16.2%");
    expect(s.textContent).toContain("post-yield slope estimated");
    expect(screen.getByTestId("fidelity-note").textContent).toBe("nonlinear (large deflection + plasticity) · ±10% (validated)");
    cleanup();
    panel(studyRecordSchema.parse(read("s1_static_contact.json")));
    expect(screen.getByTestId("nonlinear-result").textContent).toContain("1.28 MPa");
    expect(screen.getByTestId("nonlinear-result").textContent).toContain("1 contact pair / support");
  });

  it("a nonlinear solve that stopped short says how far it got", () => {
    const r = studyRecordSchema.parse(read("s1_static_nonlinear.json"));
    const res = r.results as Record<string, unknown>;
    panel({ ...r, results: { ...res, nonlinear: { ...(res.nonlinear as object), converged: false, load_reached_pct: 70, fields_at_fraction: 0.7 } } });
    expect(screen.getByTestId("nonlinear-result").textContent).toContain("stopped at 70% of the load");
    expect(screen.getByTestId("nonlinear-result").textContent).toContain("results are at 70% of the load");
  });

  it("OpenFOAM (S2, the real record): the band fix-bench validated (sphere, plate)", () => {
    panel(studyRecordSchema.parse(read("s2_wind_openfoam.json")));
    expect(screen.getByTestId("fidelity-note").textContent).toBe("OpenFOAM k-ω SST · ±10% (validated)");
    expect(screen.getByTestId("solver-label").textContent).toContain("OpenFOAM v2512");
  });
});

describe("OpenFOAM card", () => {
  // the real core's two rows (container runtime, pinned image) per install state
  const ROWS = read("s2_openfoam_rows.json") as Record<string, NonNullable<StudySolvers["cfd"]>>;
  const base = { ccx: { name: "CalculiX", found: true, version: "2.23", install: "x" }, gmsh: { name: "gmsh", found: true, version: "4", install: "x" } };
  const real = (state: string): StudySolvers => ({ ...base, cfd: structuredClone(ROWS[state]) });
  const solvers = (status: string, fix: unknown): StudySolvers => ({
    ...base, cfd: [ROWS.image_missing[0], { ...ROWS.image_missing[1], status, fix } as NonNullable<StudySolvers["cfd"]>[number]],
  });

  it("the real rows: image not pulled → the pull button; no runtime → the runtime row's OrbStack link; daemon down → start it", () => {
    const props = { chosen: true, client: () => ({ systemInstall: vi.fn() }), onInstalled: () => {}, subscribe: () => () => {} };
    const { unmount } = render(<OpenFoamCard solvers={real("image_missing")} {...props} />);
    expect(screen.getByTestId("openfoam-card").getAttribute("data-status")).toBe("optional_missing");
    expect(screen.getByTestId("openfoam-install").textContent).toBe("Pull the OpenFOAM image");
    unmount();
    const r2 = render(<OpenFoamCard solvers={real("no_runtime")} {...props} />);
    expect(screen.queryByTestId("openfoam-install")).toBeNull();
    expect(screen.getByRole("link", { name: "Get OrbStack" })).toBeTruthy();
    r2.unmount();
    render(<OpenFoamCard solvers={real("daemon_down")} {...props} />);
    expect(screen.getByTestId("openfoam-card").textContent).toContain("not running");
    expect(screen.queryByTestId("openfoam-install")).toBeNull();
    cleanup();
    const { container } = render(<OpenFoamCard solvers={real("ok")} {...props} chosen={false} />);
    expect(container.textContent).toBe("");
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
    expect(screen.getByTestId("cal-confirm-basis").textContent).toBe(`${BASIS}: add tests`);
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
    expect(screen.getByTestId("cal-applied").textContent).toBe(`Applied: ${BASIS} on MK4.`);
    expect(screen.getByTestId("cal-badge").textContent).toBe(BASIS);
    expect(screen.queryByTestId("cal-apply")).toBeNull();
    // the same material on another printer is not calibrated
    fireEvent.input(screen.getByTestId("cal-printer"), { target: { value: "default" } });
    expect(screen.getByTestId("cal-badge").textContent).toBe("datasheet / estimate");
  });

  it("a rejected fit (the real core's screen): its reasons and outliers show, and it cannot be applied", async () => {
    const f = fake();
    f.fitCalibration = vi.fn(async () => REJECTED);
    render(<CalibrationPanel client={() => f} materials={[PLA]} studies={[]} initialMaterial="PLA" initialPrinter="MK4" onApplied={() => {}} onClose={() => {}} />);
    await waitFor(() => expect(f.calibrations).toHaveBeenCalled());
    for (let i = 0; i < 3; i++) {
      fireEvent.click(screen.getByTestId("cal-add-tensile"));
      fireEvent.input(screen.getByLabelText(`Test ${i + 1} break force (N)`), { target: { value: String([13000, 11800, 12400][i]) } });
    }
    await act(async () => { fireEvent.click(screen.getByTestId("cal-fit")); });
    expect(screen.getByTestId("cal-result").getAttribute("data-status")).toBe("rejected");
    expect(screen.getByTestId("cal-rejected").textContent).toContain("rejected — implausible");
    expect(screen.getByTestId("cal-rejected").textContent).toContain("every strength test was rejected as an outlier (see outliers)");
    expect(screen.getAllByTestId("cal-outlier")).toHaveLength(3);
    expect(screen.getAllByTestId("cal-outlier")[0].textContent).toContain("325 MPa is 6.37x the data sheet");
    expect(screen.getByTestId("cal-n").textContent).toContain("2 of 5");
    expect(screen.queryByTestId("cal-apply")).toBeNull();
    expect(screen.getByTestId("cal-not-applicable")).toBeTruthy();
  });

  it("a saved rejected record says it is not applied", async () => {
    const f = fake([REJECTED_SAVED]);
    render(<CalibrationPanel client={() => f} materials={[PLA]} studies={[]} initialMaterial="PLA" initialPrinter="MK4" onApplied={() => {}} onClose={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("cal-result").getAttribute("data-state")).toBe("saved"));
    expect(screen.getByTestId("cal-applied").textContent).toBe("Saved but rejected — implausible on MK4: not applied, studies keep the datasheet values.");
    expect(screen.getByTestId("cal-badge").textContent).toBe("datasheet / estimate · calibration rejected");
  });

  it("a printer key the core refuses is said at the field", async () => {
    const f = fake();
    render(<CalibrationPanel client={() => f} materials={[PLA]} studies={[]} initialMaterial="PLA" onApplied={() => {}} onClose={() => {}} />);
    expect((screen.getByTestId("cal-printer") as HTMLInputElement).value).toBe("default");
    fireEvent.input(screen.getByTestId("cal-printer"), { target: { value: "-MK4/x" } });
    expect(screen.getByRole("alert").textContent).toContain("1–40 letters");
  });
});
