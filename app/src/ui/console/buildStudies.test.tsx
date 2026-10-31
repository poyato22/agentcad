// FW-app I3: every study and generative run of the build reaches the dock's Simulations stream —
// from the real core's records (study_list.json, topopt_list.json / topopt_running.json), unless
// the dock's agent tree already shows it.
import { cleanup, render, screen, waitFor } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../api/client";
import { studyRecordSchema, studySummarySchema, type StudySummary } from "../../api/studies";
import { topoptRecordSchema, type TopoptRecord } from "../../api/topopt";
import type { PartSummary } from "../../api/types";
import { activeBuildId } from "../../state/builds";
import { bindClient, parts, resetStore } from "../../state/store";
import { buildStudies, KEEP, mergeRecords, refreshBuildStudies, resetBuildStudies, wantsRefresh, type BuildStudy } from "./buildStudies";
import { treeFrame } from "./derived";
import { extraStudies, recordRunning, recordView, SimStream } from "./SimStream";
import type { DockEntry } from "./treeStreams";

const FX = resolve(__dirname, "../../../../core/tests/fixtures/ui");
const json = (name: string): unknown => JSON.parse(readFileSync(resolve(FX, name), "utf8"));
const SIMS = (json("study_list.json") as unknown[]).map((s) => studySummarySchema.parse(s));
const [TOPOPT] = (json("topopt_list.json") as unknown[]).map((t) => topoptRecordSchema.parse(t));
const RUNNING_TO = topoptRecordSchema.parse(json("topopt_running.json"));
const STATIC = studyRecordSchema.parse(json("study_static.json"));
const BUILD = TOPOPT.project_id as string;

afterEach(() => { cleanup(); resetBuildStudies(); resetStore(); activeBuildId.value = null; treeFrame.value = { buildId: null, view: [], sim: [] }; vi.useRealTimers(); });

const partIds = (list: readonly StudySummary[]) => new Set(list.map((s) => s.part_id));

describe("the build's study records (FW-app I3)", () => {
  it("keeps the build's part and assembly studies and its generative runs, newest first", () => {
    const other: StudySummary = { ...SIMS[0], sim_id: "sim_0000000000aa", part_id: "not_in_build", created_at: 9e9 };
    const asm: StudySummary = { ...SIMS[0], sim_id: "sim_0000000000bb", part_id: "", created_at: 8e9, project_id: BUILD } as StudySummary;
    const foreignTo: TopoptRecord = { ...TOPOPT, topopt_id: "to_0000000000cc", project_id: "elsewhere" };
    const out = mergeRecords([...SIMS, other, asm, SIMS[0]], [TOPOPT, foreignTo], partIds(SIMS), BUILD);
    const ids = out.map((r) => r.id);
    expect(ids).toContain(TOPOPT.topopt_id);
    expect(ids).toContain(asm.sim_id);
    expect(ids).not.toContain(other.sim_id);
    expect(ids).not.toContain(foreignTo.topopt_id);
    expect(new Set(ids).size).toBe(ids.length);                          // a study listed twice shows once
    expect(out.map((r) => r.at)).toEqual([...out.map((r) => r.at)].sort((a, b) => b - a));
    const many = Array.from({ length: KEEP + 5 }, (_, i) => ({ ...SIMS[0], sim_id: `sim_${String(i).padStart(12, "0")}`, created_at: i }));
    expect(mergeRecords(many, [], partIds(SIMS), BUILD)).toHaveLength(KEEP);
  });

  it("refreshes from GET /sims (all, and the build's assembly ones) and GET /topopt?project_id", async () => {
    const listStudies = vi.fn().mockResolvedValue(SIMS);
    const topoptList = vi.fn().mockResolvedValue([TOPOPT]);
    bindClient({ listStudies, topoptList } as unknown as CoreClient);
    activeBuildId.value = BUILD;
    parts.value = [...partIds(SIMS)].map((id) => ({ part_id: id, project_id: BUILD } as unknown as PartSummary));
    await refreshBuildStudies();
    expect(listStudies).toHaveBeenCalledWith();
    expect(listStudies).toHaveBeenCalledWith({ projectId: BUILD });
    expect(topoptList).toHaveBeenCalledWith({ projectId: BUILD });
    expect(buildStudies.value.buildId).toBe(BUILD);
    expect(buildStudies.value.items.length).toBe(Math.min(KEEP, new Set(SIMS.map((s) => s.sim_id)).size + 1));
  });

  it("follows the events that start, move or end a study or a generative run", () => {
    const known = new Set([SIMS[0].sim_id]);
    const ev = (type: string, data: Record<string, unknown>) => ({ type, run_id: "r", ts: 0, data });
    expect(wantsRefresh(ev("sim_progress", { sim_id: "sim_new000000000", stage: "mesh" }), known)).toBe(true);
    expect(wantsRefresh(ev("sim_progress", { sim_id: SIMS[0].sim_id, stage: "solve" }), known)).toBe(false);   // a known one moving
    expect(wantsRefresh(ev("sim_progress", { sim_id: SIMS[0].sim_id, stage: "done", phase: "done" }), known)).toBe(true);
    expect(wantsRefresh(ev("topopt_progress", { topopt_id: TOPOPT.topopt_id }), new Set())).toBe(true);
    for (const t of ["sim_finished", "topopt_finished", "run_finished"]) expect(wantsRefresh(ev(t, {}), known)).toBe(true);
    expect(wantsRefresh(ev("tool_result", { name: "mcp__mises__topopt_generate" }), known)).toBe(true);
    expect(wantsRefresh(ev("tool_result", { name: "mirror_measure" }), known)).toBe(false);
    expect(wantsRefresh(ev("agent_text", {}), known)).toBe(false);
  });
});

describe("the Simulations stream shows them (FW-app I3)", () => {
  const recs = (): BuildStudy[] => mergeRecords(SIMS, [RUNNING_TO], partIds(SIMS), BUILD);

  it("skips a record the tree shows (by id, or by a run in the dock's tree)", () => {
    const r = recs();
    const first = r.find((x) => x.kind === "sim") as Extract<BuildStudy, { kind: "sim" }>;
    const entry = {
      key: "run/c:1", runId: "lead", agent: "designer", order: 1,
      item: { kind: "activity", key: "c:1", text: "sim_run", tone: "ok", ts: 1, diff: null, extra: [], hint: null, stepIds: [],
        call: { callId: "1", name: "sim_run", input: {}, result: { payload: { output: { data: { sim_id: first.id } } } }, ok: true, startedTs: 1, summary: null, hint: null } },
    } as unknown as DockEntry;
    expect(extraStudies([entry], r, []).map((x) => x.id)).not.toContain(first.id);
    const byRun = r.filter((x) => x.runId === first.runId).map((x) => x.id);
    expect(extraStudies([], r, [first.runId as string]).some((x) => byRun.includes(x.id))).toBe(false);
    expect(extraStudies([], r, []).length).toBe(r.length);
  });

  it("a running record runs until its finish (or its last phase) arrives", () => {
    const to = recs().find((x) => x.kind === "topopt") as BuildStudy;
    expect(recordRunning(to, new Map(), new Map())).toBe(true);
    expect(recordRunning(to, new Map([[to.id, "done"]]), new Map())).toBe(false);
    const sim = { ...(recs().find((x) => x.kind === "sim") as BuildStudy), status: "running" } as BuildStudy;
    const live = new Map([[sim.id, { sim_id: sim.id, run_id: null, part_id: sim.partId, type: null, call_id: null, step_id: null, stage: "done", phase: "done", pct: 100, detail: null, ts: 1 }]]);
    expect(recordRunning(sim, new Map(), new Map())).toBe(true);
    expect(recordRunning(sim, new Map(), live)).toBe(false);
  });

  it("a finished record's card carries the core's card; a running one has none yet", () => {
    const sim = recs().find((x) => x.kind === "sim") as Extract<BuildStudy, { kind: "sim" }>;
    const done = recordView(sim, STATIC, false);
    expect(done).toMatchObject({ kind: "study", simId: sim.id, partId: sim.partId, running: false, runId: null });
    expect(done.card?.sim_id).toBe(STATIC.card?.sim_id);
    expect(recordView(sim, STATIC, true).card).toBeNull();
  });

  it("renders a record's study card (and a generative run's card) under the tree's entries", async () => {
    const getStudy = vi.fn().mockResolvedValue(STATIC);
    const topoptGet = vi.fn().mockResolvedValue(RUNNING_TO);
    bindClient({ getStudy, topoptGet, getRun: vi.fn().mockRejectedValue(new Error("no")), getStudyGlb: vi.fn().mockRejectedValue(new Error("no")), studyCharts: vi.fn().mockRejectedValue(new Error("no")) } as unknown as CoreClient);
    activeBuildId.value = BUILD;
    const sim = SIMS.find((s) => s.type === "static") ?? SIMS[0];
    buildStudies.value = { buildId: BUILD, items: mergeRecords([sim], [RUNNING_TO], partIds(SIMS), BUILD) };
    const { container } = render(<SimStream entries={[]} />);
    expect(screen.queryByText(/Studies the agents run show here/)).toBeNull();
    await waitFor(() => expect(container.querySelectorAll("[data-record]")).toHaveLength(2));
    // the running generative run is on top
    expect(container.querySelector("[data-record]")?.getAttribute("data-record")).toBe("topopt");
    expect(container.querySelector('[data-record=sim] [data-testid=study-card]')).not.toBeNull();
    await waitFor(() => expect(getStudy).toHaveBeenCalledWith(sim.sim_id));
  });
});
