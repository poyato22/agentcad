// Plan 13 H4: the Studies page's "Whole assembly" target against the real core's answers: the
// studies agent's pick, Run with the type's defaults, live progress by run id, the finished
// record (checks, metrics), Watch opening the assembly's full window with the study's motion, and
// Check part stresses.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { assemblyKindsSchema, assemblyStudyRecordSchema } from "../../../../api/assemblyStudies";
import { immersive, resetAssemblyState } from "../../../assembly/state";
import { AssemblyResult, AssemblySide } from "./AssemblyStudies";
import { AssemblyStudiesController, type AssemblyStudiesClient } from "./controller";

const DIR = resolve(import.meta.dirname, "../../../../../../core/tests/fixtures/ui/assembly_studies");
const read = (n: string): unknown => JSON.parse(readFileSync(resolve(DIR, n), "utf8"));
const KINDS = assemblyKindsSchema.parse(read("assembly_study_kinds.json")).kinds;
const WALK = assemblyStudyRecordSchema.parse(read("assembly_study_walk.json"));

function client(): AssemblyStudiesClient & { done: boolean } {
  const c = {
    done: false,
    assemblyStudyKinds: vi.fn(async () => KINDS),
    listAssemblyStudies: vi.fn(async () => (c.done ? [{ ...WALK, run_id: "r1", status: "done" }] : [])),
    getAssemblyStudy: vi.fn(async () => ({ ...WALK, run_id: "r1" })),
    startAssemblyStudy: vi.fn(async () => ({ run_id: "r1", status: "running" })),
    assemblyStudyFem: vi.fn(async () => ({ run_id: "r2", status: "running" })),
    cancelJob: vi.fn(), cancel: vi.fn(),
  };
  return c as unknown as AssemblyStudiesClient & { done: boolean };
}

afterEach(() => { cleanup(); resetAssemblyState(); });

describe("Whole assembly studies", () => {
  it("runs the recommended study, follows it and shows the result with Watch and part stresses", async () => {
    const cl = client();
    const c = new AssemblyStudiesController(() => cl, { pollMs: 10 });
    render(<div><AssemblySide c={c} projectId="b1" /><AssemblyResult c={c} /></div>);
    await waitFor(() => expect(screen.getByTestId("assembly-recommended").textContent).toMatch(/walk study/i));
    expect(screen.getByTestId("asy-kind-walk").getAttribute("aria-selected")).toBe("true");
    fireEvent.click(screen.getByTestId("asy-run"));
    await waitFor(() => expect(cl.startAssemblyStudy).toHaveBeenCalled());
    const body = (cl.startAssemblyStudy as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(body).toMatchObject({ project_id: "b1", kind: "walk", settings: { gait: "walk", cycles: 3 } });
    act(() => c.onEvent({ type: "assembly_study_progress", run_id: "r1", ts: 0, data: { study_id: WALK.study_id, project_id: "b1", kind: "walk", phase: "stability", pct: 50, detail: "static margin" } }));
    expect(screen.getByTestId("asy-progress").getAttribute("data-phase")).toBe("stability");
    cl.done = true;
    await waitFor(() => expect(screen.getByTestId("assembly-result").getAttribute("data-verdict")).toBe(WALK.verdict));
    expect(screen.getAllByTestId("asy-check").length).toBe(WALK.checks.length);
    fireEvent.click(screen.getByTestId("asy-watch"));
    expect(immersive.value).toMatchObject({ mode: "animation", loop: true, title: "Walk study" });
    expect((immersive.value as unknown as { steps: unknown[] }).steps).toHaveLength(WALK.playback!.steps.length);
    fireEvent.click(screen.getByTestId("asy-check-stresses"));
    await waitFor(() => expect(cl.assemblyStudyFem).toHaveBeenCalledWith(WALK.study_id));
    await waitFor(() => expect(screen.getByTestId("asy-check-stresses").textContent).toMatch(/Checking part stresses/));
    c.dispose();
  });
});
