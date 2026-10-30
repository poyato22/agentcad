// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { CoreError } from "../../../api/client";
import { StudiesController, type StudiesClient } from "./controller";

const SUM = (over: Record<string, unknown>) => ({
  sim_id: "sim_aaaaaaaaaaaa", part_id: "beam", run_id: "r1", type: "wind", status: "running", stage: "solve", phase: "solve", pct: 10,
  created_at: 1, finished_at: null, error: null, hint: null, inputs: {}, results: null, guide: true, label: "guide only", fidelity_class: "cfd_lbm",
  stale: false, ...over,
});

function client(list: () => unknown[]) {
  return {
    listStudies: vi.fn(async (f: { runId?: string }) => (f.runId ? list() : [])),
    startStudy: vi.fn().mockResolvedValue({ run_id: "r1", status: "running" }),
    cancelStudy: vi.fn().mockResolvedValue({ sim_id: "sim_aaaaaaaaaaaa", status: "cancelling" }),
    cancel: vi.fn().mockResolvedValue({ ok: true }),
    getStudy: vi.fn(async () => ({ ...SUM({ status: "done" }), warnings: [], caveats: [] })),
    studyCharts: vi.fn().mockResolvedValue({ sim_id: "sim_aaaaaaaaaaaa", type: "wind", status: "done", charts: [] }),
    getRun: vi.fn().mockResolvedValue({ status: "running" }),
    steps: vi.fn().mockResolvedValue([]),
  };
}
const wait = (ms = 30) => new Promise((r) => setTimeout(r, ms));

describe("StudiesController", () => {
  it("follows a started study to its result by run id", async () => {
    let status = "running";
    const c = client(() => [SUM({ status })]);
    const ctl = new StudiesController(() => c as unknown as StudiesClient, { pollMs: 5 });
    ctl.historyPart.value = "beam";
    await ctl.start("beam", "wind", { speed_m_s: 5 });
    expect(c.startStudy).toHaveBeenCalledWith({ type: "wind", part_id: "beam", settings: { speed_m_s: 5 } });
    await wait();
    expect(ctl.active.value?.simId).toBe("sim_aaaaaaaaaaaa");
    status = "done";
    await wait();
    expect(ctl.active.value).toBeNull();
    expect(ctl.record.value?.status).toBe("done");
    ctl.dispose();
  });

  it("switching the type shows that type's newest result, or none — never the other type's (m9)", async () => {
    const c = client(() => []);
    const ctl = new StudiesController(() => c as unknown as StudiesClient, { pollMs: 1000 });
    ctl.history.value = [SUM({ sim_id: "sim_wind00000001", type: "wind", status: "done" }), SUM({ sim_id: "sim_static000001", type: "static", status: "done", created_at: 2 })] as never;
    ctl.record.value = { ...SUM({ sim_id: "sim_wind00000001", type: "wind", status: "done" }), warnings: [], caveats: [] } as never;
    ctl.showType("static");
    expect(ctl.selectedSimId.value).toBe("sim_static000001");
    ctl.record.value = { ...SUM({ sim_id: "sim_wind00000001", type: "wind", status: "done" }), warnings: [], caveats: [] } as never;
    ctl.showType("modal");
    expect(ctl.record.value).toBeNull();
    expect(ctl.selectedSimId.value).toBeNull();
    ctl.dispose();
  });

  it("cancel stops the study by its id; one the core says is not running falls back to the run", async () => {
    const c = client(() => [SUM({})]);
    const ctl = new StudiesController(() => c as unknown as StudiesClient, { pollMs: 1000 });
    await ctl.start("beam", "wind", {});
    ctl.active.value = { ...ctl.active.value!, simId: "sim_aaaaaaaaaaaa" };
    await ctl.cancel();
    expect(c.cancelStudy).toHaveBeenCalledWith("sim_aaaaaaaaaaaa");
    expect(c.cancel).not.toHaveBeenCalled();
    ctl.active.value = { ...ctl.active.value!, cancelling: false };
    c.cancelStudy.mockRejectedValueOnce(new CoreError("stale_version", "sim_aaaaaaaaaaaa is not running", { status: 409 }));
    await ctl.cancel();
    expect(c.cancel).toHaveBeenCalledWith("r1");
    ctl.dispose();
  });

  it("a study that has no record yet is cancelled through its run", async () => {
    const c = client(() => []);
    const ctl = new StudiesController(() => c as unknown as StudiesClient, { pollMs: 1000 });
    await ctl.start("beam", "wind", {});
    await ctl.cancel();
    expect(c.cancelStudy).not.toHaveBeenCalled();
    expect(c.cancel).toHaveBeenCalledWith("r1");
    ctl.dispose();
  });
});
