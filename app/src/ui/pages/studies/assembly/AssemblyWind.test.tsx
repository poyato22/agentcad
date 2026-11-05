import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import { studySummarySchema, type StudySummary } from "../../../../api/studies";
import { immersive } from "../immersive/state";
import { AssemblyWind, type AssemblyWindClient } from "./AssemblyWind";

const row = (o: Partial<StudySummary>) => ({ sim_id: "sim_000000000001", type: "wind", status: "done", results: { cd: 0.5, drag_n: 3.2 }, ...o }) as StudySummary;

afterEach(() => { cleanup(); immersive.value = null; });

describe("AssemblyWind", () => {
  it("runs a wind study of the build, with the speed set", async () => {
    const c: AssemblyWindClient = { listStudies: vi.fn(async () => []), startStudy: vi.fn(async () => ({})) };
    render(<AssemblyWind projectId="p1" client={c} />);
    fireEvent.input(screen.getByTestId("asy-wind-speed"), { target: { value: "89" } });
    fireEvent.click(screen.getByTestId("asy-wind-run"));
    await waitFor(() => expect(c.startStudy).toHaveBeenCalled());
    const body = (c.startStudy as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(body.project_id).toBe("p1");
    expect(body.settings.speed_m_s).toBe(89);
  });
  it("lists only wind studies, with their numbers, and opens one immersive", async () => {
    const c: AssemblyWindClient = { listStudies: vi.fn(async () => [row({}), row({ sim_id: "sim_000000000002", type: "static" })]), startStudy: vi.fn() };
    render(<AssemblyWind projectId="p1" client={c} />);
    const rows = await screen.findAllByTestId("asy-wind-row");
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain("Cd 0.5");
    fireEvent.click(rows[0]);
    expect(immersive.value?.simId).toBe("sim_000000000001");
  });
  it("cannot start a second one while one runs", async () => {
    const c: AssemblyWindClient = { listStudies: vi.fn(async () => [row({ status: "running", results: null })]), startStudy: vi.fn() };
    render(<AssemblyWind projectId="p1" client={c} />);
    await waitFor(() => expect((screen.getByTestId("asy-wind-run") as HTMLButtonElement).disabled).toBe(true));
  });
});

describe("an assembly study's record", () => {
  it("parses with no part", () => {
    const r = studySummarySchema.parse({ sim_id: "s", part_id: null, project_id: "p1", run_id: null, status: "done", created_at: 1, finished_at: 2, error: null, hint: null, inputs: {}, results: null, guide: true, label: "x", stale: false });
    expect(r.part_id).toBe("");
  });
});
