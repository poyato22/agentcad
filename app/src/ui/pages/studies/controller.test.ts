import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { CoreError } from "../../../api/client";
import type { SimRecord, SimSummary } from "../../../api/sims";
import type { AgentRun, Step } from "../../../api/types";
import { StudiesController, type StudiesClient } from "./controller";
import { DEFAULT_SETUP } from "./model";

const FIX = resolve(import.meta.dirname, "../../../../../core/tests/fixtures/ui");
const fx = <T>(n: string): T => JSON.parse(readFileSync(resolve(FIX, n), "utf8")) as T;
const DONE = fx<SimRecord>("sim_done.json");
const FAILED = fx<SimRecord>("sim_failed.json");
const LIST = fx<SimSummary[]>("sims_list.json");
const summaryOf = (r: SimRecord): SimSummary => LIST.find((s) => s.sim_id === r.sim_id) as SimSummary;

const run = (status: AgentRun["status"]): AgentRun => ({
  id: "r1", parent_id: null, agent: "studies", provider: "ui", model: "studies-page", task: "Studies page: static study of mount_plate",
  status, branch: null, onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0, created_at: 1, updated_at: 1,
});

function client(over: Partial<StudiesClient> = {}): StudiesClient & Record<string, ReturnType<typeof vi.fn>> {
  return {
    simSolvers: vi.fn(async () => fx("sims_solvers.json")),
    listSims: vi.fn(async () => LIST),
    getSim: vi.fn(async (id: string) => (id === DONE.sim_id ? DONE : FAILED)),
    startSim: vi.fn(async () => ({ run_id: "r1", status: "running" as const })),
    cancel: vi.fn(async () => ({ ok: true as const })),
    getRun: vi.fn(async () => run("running")),
    steps: vi.fn(async () => [] as Step[]),
    createRun: vi.fn(async () => ({ run_id: "d1" })),
    ...over,
  } as unknown as StudiesClient & Record<string, ReturnType<typeof vi.fn>>;
}

const until = async (cond: () => boolean) => {
  for (let i = 0; i < 400 && !cond(); i++) await new Promise((r) => setTimeout(r, 2));
  expect(cond()).toBe(true);
};

describe("StudiesController", () => {
  it("history: newest first, shows the newest finished study", async () => {
    const c = client();
    const s = new StudiesController(() => c, { pollMs: 1 });
    await s.loadHistory("mount_plate");
    expect(c.listSims).toHaveBeenCalledWith({ partId: "mount_plate" });
    expect(s.history.value.map((x) => x.sim_id)).toEqual(LIST.map((x) => x.sim_id));
    expect(s.selectedSimId.value).toBe(DONE.sim_id); // the done one, not the newer failed one
    await until(() => s.record.value?.sim_id === DONE.sim_id);
  });

  it("solver status: missing ccx is known before running", async () => {
    const c = client({ simSolvers: vi.fn(async () => fx("sims_solvers_missing.json")) } as never);
    const s = new StudiesController(() => c, { pollMs: 1 });
    await s.loadSolvers();
    expect(s.solvers.value?.ccx.found).toBe(false);
    expect(s.ccxMissing.value).toBe(true);
  });

  it("run → progress → result", async () => {
    const stages: SimSummary[] = [
      { ...summaryOf(DONE), status: "running", stage: "mesh_coarse", results: null },
      { ...summaryOf(DONE), status: "running", stage: "solve_fine", results: null },
      summaryOf(DONE),
    ];
    let i = 0;
    const c = client({
      listSims: vi.fn(async (f: { runId?: string }) => (f.runId ? [stages[Math.min(i++, 2)]] : LIST)),
    } as never);
    const s = new StudiesController(() => c, { pollMs: 1 });
    const seen: (string | null)[] = [];
    s.active.subscribe((a) => { if (a) seen.push(a.stage); });
    await s.loadHistory("mount_plate");
    await s.start("mount_plate", { ...DEFAULT_SETUP, material: "PETG" });
    expect(c.startSim).toHaveBeenCalledWith({ part_id: "mount_plate", fix: "bottom", load: { face: "top", newtons: 20, direction: "-z" }, material: "PETG", environment: null });
    expect(s.active.value?.runId).toBe("r1");
    await until(() => s.active.value === null);
    expect(seen).toContain("mesh_coarse");
    expect(seen).toContain("solve_fine");
    expect(s.selectedSimId.value).toBe(DONE.sim_id);
    await until(() => s.record.value?.status === "done");
    expect(s.runError.value).toBeNull();
  });

  it("refuses a bad setup without calling the core", async () => {
    const c = client();
    const s = new StudiesController(() => c, { pollMs: 1 });
    await s.start("mount_plate", { ...DEFAULT_SETUP, face: "bottom" });
    expect(c.startSim).not.toHaveBeenCalled();
    expect(s.runError.value?.message).toContain("the same");
  });

  it("a start the core refuses shows its message and hint", async () => {
    const c = client({ startSim: vi.fn(async () => { throw new CoreError("not_found", "no built part 'mount_plate'", { status: 404, hint: "build it first" }); }) } as never);
    const s = new StudiesController(() => c, { pollMs: 1 });
    await s.start("mount_plate", DEFAULT_SETUP);
    expect(s.runError.value).toEqual({ message: "no built part 'mount_plate'", hint: "build it first" });
    expect(s.active.value).toBeNull();
  });

  it("a run that fails before storing a study shows the run's error step", async () => {
    const err: Step = {
      id: 3, run_id: "r1", ts: 1, kind: "error", evidence_hash: "h", prev_hash: null, collapsed: false,
      payload: { call_id: "u", name: "sim_static_run", error: "invalid_input", message: "unknown material 'X'", hint: "use one of: PLA", retryable: false },
    };
    const c = client({
      listSims: vi.fn(async (f: { runId?: string }) => (f.runId ? [] : LIST)),
      getRun: vi.fn(async () => run("failed")),
      steps: vi.fn(async () => [err]),
    } as never);
    const s = new StudiesController(() => c, { pollMs: 1 });
    await s.start("mount_plate", DEFAULT_SETUP);
    await until(() => s.active.value === null);
    expect(s.runError.value).toEqual({ message: "unknown material 'X'", hint: "use one of: PLA" });
  });

  it("a failed study for a missing solver re-checks the solver (install card)", async () => {
    let solverCalls = 0;
    const c = client({
      simSolvers: vi.fn(async () => { solverCalls++; return fx("sims_solvers_missing.json"); }),
      listSims: vi.fn(async (f: { runId?: string }) => (f.runId ? [summaryOf(FAILED)] : LIST)),
    } as never);
    const s = new StudiesController(() => c, { pollMs: 1 });
    await s.loadHistory("mount_plate");
    await s.start("mount_plate", DEFAULT_SETUP);
    await until(() => s.active.value === null);
    await until(() => s.ccxMissing.value);
    expect(solverCalls).toBe(1);
    expect(s.selectedSimId.value).toBe(FAILED.sim_id);
  });

  it("cancel kills the run; the cancelled study ends the progress", async () => {
    let cancelled = false;
    const c = client({
      listSims: vi.fn(async (f: { runId?: string }) => (f.runId
        ? [{ ...summaryOf(DONE), status: cancelled ? "cancelled" : "running", stage: cancelled ? "cancelled" : "solve_fine", results: null }]
        : LIST)),
      cancel: vi.fn(async () => { cancelled = true; return { ok: true as const }; }),
    } as never);
    const s = new StudiesController(() => c, { pollMs: 1 });
    await s.start("mount_plate", DEFAULT_SETUP);
    await until(() => s.active.value?.stage === "solve_fine");
    await s.cancel();
    expect(c.cancel).toHaveBeenCalledWith("r1");
    await until(() => s.active.value === null);
  });

  it("resumes following a study that is still running when the page opens", async () => {
    const running = { ...summaryOf(DONE), status: "running" as const, stage: "mesh_fine" as const, results: null };
    const c = client({ listSims: vi.fn(async (f: { runId?: string }) => (f.runId ? [running] : [running, ...LIST])) } as never);
    const s = new StudiesController(() => c, { pollMs: 50 });
    await s.loadHistory("mount_plate");
    expect(s.active.value?.runId).toBe(DONE.run_id);
    expect(s.active.value?.stage).toBe("mesh_fine");
    s.dispose();
  });

  it("ask agent to fix: a designer run scoped to the part, with the result as context", async () => {
    const c = client();
    const s = new StudiesController(() => c, { pollMs: 1 });
    const runId = await s.ask(DONE);
    expect(runId).toBe("d1");
    const body = vi.mocked(c.createRun).mock.calls[0][0] as { agent: string; prompt: string; edit_scope: string[] };
    expect(body.agent).toBe("designer");
    expect(body.edit_scope).toEqual(["mount_plate"]);
    expect(body.prompt).toContain("guide only, not a guarantee");
    expect(s.asked.value).toEqual({ simId: DONE.sim_id, runId: "d1", error: null });
  });

  it("a part edit re-reads the shown study, so it turns stale without reselecting", async () => {
    let edited = false;
    const c = client({
      listSims: vi.fn(async () => LIST.map((x) => ({ ...x, stale: edited }))),
      getSim: vi.fn(async (id: string) => ({ ...(id === DONE.sim_id ? DONE : FAILED), stale: edited })),
    } as never);
    const s = new StudiesController(() => c, { pollMs: 1 });
    await s.loadHistory("mount_plate");
    await until(() => s.record.value?.sim_id === DONE.sim_id);
    expect(s.record.value?.stale).toBe(false);
    edited = true;
    await s.loadHistory("mount_plate"); // the page calls this when the part's mtime changes
    await until(() => s.record.value?.stale === true);
    expect(s.selectedSimId.value).toBe(DONE.sim_id);
  });

  it("polling pauses while the page is hidden and resumes on mount", async () => {
    const c = client({
      listSims: vi.fn(async (f: { runId?: string }) => (f.runId ? [{ ...summaryOf(DONE), status: "running", stage: "solve_fine", results: null }] : LIST)),
    } as never);
    const s = new StudiesController(() => c, { pollMs: 2 });
    await s.start("mount_plate", DEFAULT_SETUP);
    await until(() => s.active.value?.stage === "solve_fine");
    s.pause();
    await new Promise((r) => setTimeout(r, 20));
    const n = vi.mocked(c.listSims).mock.calls.length;
    await new Promise((r) => setTimeout(r, 40));
    expect(vi.mocked(c.listSims).mock.calls.length).toBe(n); // nothing while hidden
    expect(s.active.value?.runId).toBe("r1"); // still following
    s.resume();
    await until(() => vi.mocked(c.listSims).mock.calls.length > n);
    s.dispose();
  });
});
