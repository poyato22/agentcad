import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SimRecord, SimSummary } from "../../../api/sims";
import type { PartSummary } from "../../../api/types";
import { parts, resetStore, selectedRunId } from "../../../state/store";
import type { ViewerHandle } from "../../viewer/scene";
import { consoleCollapsed, setConsoleCollapsed } from "../router";
import { StudiesController, type StudiesClient } from "./controller";
import { resetStudiesPage, StudiesPage } from "./index";

const handle = vi.hoisted(() => ({ setPart: vi.fn(), setMode: vi.fn(), setHighlight: vi.fn(), fit: vi.fn(), resize: vi.fn(), dispose: vi.fn() }));
vi.mock("../../viewer/scene", async (orig) => ({ ...(await orig<typeof import("../../viewer/scene")>()), createViewer: (): ViewerHandle => handle }));

const FIX = resolve(import.meta.dirname, "../../../../../core/tests/fixtures/ui");
const fx = <T,>(n: string): T => JSON.parse(readFileSync(resolve(FIX, n), "utf8")) as T;
const DONE = fx<SimRecord>("sim_done.json");
const FAILED = fx<SimRecord>("sim_failed.json");
const LIST = fx<SimSummary[]>("sims_list.json");
const PARTS = fx<PartSummary[]>("parts.json");

function client(over: Partial<Record<keyof StudiesClient, unknown>> = {}): StudiesClient {
  return {
    simSolvers: vi.fn(async () => fx("sims_solvers.json")),
    listSims: vi.fn(async () => LIST),
    getSim: vi.fn(async (id: string) => (id === DONE.sim_id ? DONE : FAILED)),
    startSim: vi.fn(async () => ({ run_id: "r1", status: "running" })),
    cancel: vi.fn(async () => ({ ok: true })),
    getRun: vi.fn(),
    steps: vi.fn(async () => []),
    createRun: vi.fn(async () => ({ run_id: "d1" })),
    ...over,
  } as unknown as StudiesClient;
}

const never = () => new Promise<never>(() => {});
const deps = { glb: { part: never, sim: never }, profileMaterial: async () => null };

beforeEach(() => { resetStore(); resetStudiesPage(); parts.value = PARTS; for (const f of Object.values(handle)) f.mockClear(); });
afterEach(() => { cleanup(); resetStore(); setConsoleCollapsed(false); });

describe("StudiesPage", () => {
  it("shows the newest finished study with every guide-only label", async () => {
    const ctl = new StudiesController(() => client(), { pollMs: 1 });
    const { getByTestId, container } = render(<StudiesPage controller={ctl} deps={deps} />);
    await waitFor(() => expect(getByTestId("sf").textContent).toContain("3.17"));
    expect(getByTestId("sf").getAttribute("data-verdict")).toBe("pass");
    expect(getByTestId("sf-incl-fixture").textContent).toContain("3.17");
    expect(getByTestId("peak-excludes").textContent).toBe("peak excludes the fixed face (nodes on the fixed face)");
    expect(getByTestId("max-vm").textContent).toContain("10.4 MPa");
    expect(getByTestId("max-disp").textContent).toContain("0.832 mm");
    expect(getByTestId("error-band").textContent).toContain("±5.1%");
    expect(getByTestId("guide-label").textContent).toContain("guide only, not a guarantee");
    const solver = getByTestId("solver-label").textContent ?? "";
    for (const s of ["CalculiX 2.23", "C3D10", "gmsh 4.15.2", "1.5 mm", "coarse 3 mm", "PETG"]) expect(solver).toContain(s);
    // the material model in full, not cut at the first ";"
    expect(solver).toContain(`PETG ${DONE.material.model}`);
    expect(getByTestId("legend").textContent).toContain("MPa");
    expect(getByTestId("legend").textContent).toContain("10.4");
    expect(container.textContent).toContain("FIG. 1 — DESIGN");
    expect(container.textContent).toContain("FIG. 2 — STRESS");
  });

  it("history lists every study by what differs, and opens one", async () => {
    const c = client();
    const ctl = new StudiesController(() => c, { pollMs: 1 });
    const { findAllByTestId, getByTestId } = render(<StudiesPage controller={ctl} deps={deps} />);
    const rows = await findAllByTestId("study-row");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toMatch(/^Study · \d\d:\d\d(:\d\d)? · PETG/);
    fireEvent.click(rows[0]);
    await waitFor(() => expect(getByTestId("study-failed").textContent).toContain("CalculiX (ccx) is not installed"));
    expect(getByTestId("study-failed").textContent).toContain("conda install -c conda-forge calculix");
    expect(rows[0].getAttribute("aria-current")).toBe("true");
  });

  it("missing CalculiX: the install card with the command, and Run is off", async () => {
    const ctl = new StudiesController(() => client({ simSolvers: vi.fn(async () => fx("sims_solvers_missing.json")) }), { pollMs: 1 });
    const { findByTestId, getByRole } = render(<StudiesPage controller={ctl} deps={deps} />);
    const card = await findByTestId("install-card");
    expect(card.textContent).toContain("CalculiX");
    expect(card.textContent).toContain("conda install -c conda-forge calculix");
    expect((getByRole("button", { name: "Run study" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("every missing tool's install command has a Copy button", async () => {
    const both = fx<{ ccx: object; gmsh: object }>("sims_solvers_missing.json");
    const solvers = { ...both, gmsh: { ...both.gmsh, found: false, version: null } };
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const ctl = new StudiesController(() => client({ simSolvers: vi.fn(async () => solvers) }), { pollMs: 1 });
    const { findByTestId } = render(<StudiesPage controller={ctl} deps={deps} />);
    const card = await findByTestId("install-card");
    const copies = Array.from(card.querySelectorAll("button")).filter((b) => /copy/i.test(b.getAttribute("aria-label") ?? ""));
    expect(copies).toHaveLength(2);
    fireEvent.click(copies[0]);
    fireEvent.click(copies[1]);
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(2));
    expect(writeText).toHaveBeenNthCalledWith(1, "conda install -c conda-forge calculix");
    expect(writeText).toHaveBeenNthCalledWith(2, "uv sync"); // the command inside "run `uv sync` in core/"
  });

  it("polling stops while the page is unmounted and resumes when it mounts again", async () => {
    const ctl = new StudiesController(() => client(), { pollMs: 1 });
    const pause = vi.spyOn(ctl, "pause");
    const resume = vi.spyOn(ctl, "resume");
    const { unmount } = render(<StudiesPage controller={ctl} deps={deps} />);
    expect(resume).toHaveBeenCalledTimes(1);
    unmount();
    expect(pause).toHaveBeenCalledTimes(1);
  });

  it("run sends the setup; a preset fills it; progress shows a Cancel", async () => {
    const c = client({ listSims: vi.fn(async (f: { runId?: string }) => (f.runId ? [{ ...LIST[1], status: "running", stage: "solve_coarse", results: null }] : LIST)) });
    const ctl = new StudiesController(() => c, { pollMs: 5 });
    const { getByLabelText, getByRole, findByTestId } = render(<StudiesPage controller={ctl} deps={deps} />);
    await waitFor(() => expect((getByRole("button", { name: "Run study" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.change(getByLabelText("Preset"), { target: { value: "Bike mount" } });
    fireEvent.change(getByLabelText("Material"), { target: { value: "PETG" } });
    expect((getByLabelText("Force (N)") as HTMLInputElement).value).toBe("50");
    fireEvent.click(getByRole("button", { name: "Run study" }));
    await waitFor(() => expect(c.startSim).toHaveBeenCalledWith({
      part_id: "mount_plate", fix: "bottom", load: { face: "top", newtons: 50, direction: "-z" }, material: "PETG", environment: "Bike mount",
    }));
    const progress = await findByTestId("study-progress");
    await waitFor(() => expect(progress.textContent).toContain("solving (coarse)"));
    fireEvent.click(getByRole("button", { name: "Cancel study" }));
    await waitFor(() => expect(c.cancel).toHaveBeenCalledWith("r1"));
    ctl.dispose();
  });

  it("a setup the core would refuse is said before running", async () => {
    const c = client();
    const ctl = new StudiesController(() => c, { pollMs: 1 });
    const { getByLabelText, getByRole, findByTestId } = render(<StudiesPage controller={ctl} deps={deps} />);
    await waitFor(() => expect((getByRole("button", { name: "Run study" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.change(getByLabelText("Load face"), { target: { value: "bottom" } });
    expect((await findByTestId("setup-problems")).textContent).toContain("the same");
    expect((getByRole("button", { name: "Run study" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("ask agent to fix starts a designer run, selects it and opens the console", async () => {
    setConsoleCollapsed(true);
    const c = client();
    const ctl = new StudiesController(() => c, { pollMs: 1 });
    const { findByRole, findByTestId } = render(<StudiesPage controller={ctl} deps={deps} />);
    fireEvent.click(await findByRole("button", { name: "Ask agent" }));
    await waitFor(() => expect(selectedRunId.value).toBe("d1"));
    expect(consoleCollapsed.value).toBe(false);
    expect((await findByTestId("asked")).textContent).toContain("designer");
    expect(vi.mocked(c.createRun).mock.calls[0][0].prompt).toContain("Safety factor 3.17");
  });

  it("after sending, Ask stays off for that study and 'open run' goes back to the run", async () => {
    const c = client();
    const ctl = new StudiesController(() => c, { pollMs: 1 });
    const { findByRole, getByRole } = render(<StudiesPage controller={ctl} deps={deps} />);
    const ask = await findByRole("button", { name: "Ask agent" });
    fireEvent.click(ask);
    await waitFor(() => expect((ask as HTMLButtonElement).disabled).toBe(true));
    fireEvent.click(ask);
    expect(c.createRun).toHaveBeenCalledTimes(1);
    act(() => { selectedRunId.value = null; setConsoleCollapsed(true); });
    fireEvent.click(getByRole("button", { name: "Open run" }));
    expect(selectedRunId.value).toBe("d1");
    expect(consoleCollapsed.value).toBe(false);
  });

  it("the stale banner shows when the part changes while the page is open", async () => {
    let edited = false;
    const c = client({
      listSims: vi.fn(async () => LIST.map((x) => ({ ...x, stale: edited }))),
      getSim: vi.fn(async (id: string) => ({ ...(id === DONE.sim_id ? DONE : FAILED), stale: edited })),
    });
    const ctl = new StudiesController(() => c, { pollMs: 1 });
    const { findByTestId, queryByTestId } = render(<StudiesPage controller={ctl} deps={deps} />);
    await findByTestId("sf");
    expect(queryByTestId("stale")).toBeNull();
    edited = true;
    act(() => { parts.value = PARTS.map((p) => ({ ...p, mtime: p.mtime + 5 })); }); // an agent edited the part
    expect((await findByTestId("stale")).textContent).toContain("part changed since this study");
  });

  it("untrusted text from the core renders as text", async () => {
    const evil = { ...FAILED, error: "<img src=x onerror=alert(1)>", hint: "<b>bold</b>" };
    const ctl = new StudiesController(() => client({ listSims: vi.fn(async () => [{ ...LIST[0], error: evil.error }]), getSim: vi.fn(async () => evil) }), { pollMs: 1 });
    const { findByTestId, container } = render(<StudiesPage controller={ctl} deps={deps} />);
    const band = await findByTestId("study-failed");
    expect(band.textContent).toContain("<img src=x onerror=alert(1)>");
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
  });

  it("no built part: says so, nothing to run", async () => {
    parts.value = [];
    const ctl = new StudiesController(() => client(), { pollMs: 1 });
    const { container, getByRole } = render(<StudiesPage controller={ctl} deps={deps} />);
    await act(async () => { await Promise.resolve(); });
    expect(container.textContent).toContain("No built parts");
    expect((getByRole("button", { name: "Run study" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
