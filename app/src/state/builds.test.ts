import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";
import { buildSchema, type Build } from "../api/builds";
import type { CoreClient } from "../api/client";
import type { AgentRun, PartSummary } from "../api/types";
import { activeBuildId, buildActions, builds, buildsError, normalizeTabs, openBuildIds, openBuilds, resetBuilds, runBuild, trashBlocked, trashedBuilds, trashRefusalText } from "./builds";
import { bindClient, parts, resetStore, runs, selectedPartId, selectedRunId } from "./store";

const FX = resolve(import.meta.dirname, "../../test/fixtures/core-ui");
const REAL = z.array(buildSchema).parse(JSON.parse(readFileSync(resolve(FX, "projects_with_trash.json"), "utf8")));

const b = (id: string, name: string, trashed: number | null = null): Build =>
  ({ id, name, created_at: 1, updated_at: 1, trashed_at: trashed, part_count: 0, run_count: 0, onshape: null });
const part = (id: string, project: string): PartSummary => ({ part_id: id, id, vars: {}, metrics: null, mtime: 1, project_id: project });

function fakeClient(all: Build[], tabs = { active_project_id: "a" as string | null, open_project_ids: ["a"] }) {
  const byBuild: Record<string, PartSummary[]> = { a: [part("arm", "a")], b: [part("motor", "b")], c: [part("plate", "c")] };
  return {
    listBuilds: vi.fn(async () => all),
    buildTabs: vi.fn(async () => tabs),
    setBuildTabs: vi.fn(async (t: typeof tabs) => t),
    parts: vi.fn(async (id: string | null) => byBuild[id ?? "a"] ?? []),
    createBuild: vi.fn(async (name: string) => b("n", name)),
    renameBuild: vi.fn(async (id: string, name: string) => ({ ...all.find((x) => x.id === id) as Build, name })),
    trashBuild: vi.fn(async (id: string) => ({ ...all.find((x) => x.id === id) as Build, trashed_at: 5 })),
    restoreBuild: vi.fn(async (id: string) => ({ ...all.find((x) => x.id === id) as Build, trashed_at: null })),
    emptyTrash: vi.fn(async () => ({ purged: all.filter((x) => x.trashed_at !== null).map((x) => x.id), count: 1 })),
  };
}

beforeEach(() => { resetStore(); resetBuilds(); });
afterEach(() => { vi.restoreAllMocks(); });

describe("builds", () => {
  it("parses the real core's /projects (with a trashed build)", () => {
    expect(REAL.some((x) => x.trashed_at !== null)).toBe(true);
    expect(REAL.every((x) => typeof x.part_count === "number")).toBe(true);
  });

  it("normalizes tabs: trashed or unknown tabs drop, the active one is always open", () => {
    const all = [b("a", "Quad"), b("b", "Bench"), b("t", "Old", 9)];
    expect(normalizeTabs(all, "t", ["t", "b", "zz"])).toEqual({ active: "b", open: ["b"] });
    expect(normalizeTabs(all, "a", ["b"])).toEqual({ active: "a", open: ["b", "a"] });
    expect(normalizeTabs([], null, [])).toEqual({ active: null, open: [] });
  });

  it("refresh loads builds + the profile's tabs and the active build's parts", async () => {
    const c = fakeClient([b("a", "Quad"), b("b", "Bench"), b("t", "Old", 9)], { active_project_id: "b", open_project_ids: ["a", "b"] });
    bindClient(c as unknown as CoreClient);
    await buildActions.refresh();
    expect(activeBuildId.value).toBe("b");
    expect(openBuilds.value.map((x) => x.name)).toEqual(["Quad", "Bench"]);
    expect(trashedBuilds.value.map((x) => x.id)).toEqual(["t"]);
    // sync already loaded the core's active build (the profile's): no second /parts, no write-back
    expect(c.parts).not.toHaveBeenCalled();
    expect(c.setBuildTabs).not.toHaveBeenCalled();
  });

  it("refresh reloads the parts when the profile's active build is gone (trashed)", async () => {
    const c = fakeClient([b("a", "Quad"), b("t", "Old", 9)], { active_project_id: "t", open_project_ids: ["t"] });
    bindClient(c as unknown as CoreClient);
    await buildActions.refresh();
    expect(activeBuildId.value).toBe("a");
    expect(c.parts).toHaveBeenCalledWith("a");
    expect(parts.value.map((p) => p.part_id)).toEqual(["arm"]);
  });

  it("switching opens the tab, saves the tabs, shows that build's parts and keeps each build's selection", async () => {
    const c = fakeClient([b("a", "Quad"), b("b", "Bench")]);
    bindClient(c as unknown as CoreClient);
    runs.value = new Map<string, AgentRun>([
      ["r1", { id: "r1", parent_id: null, agent: "designer", provider: "fake", model: "m", task: "arms", status: "done", branch: null, onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0, created_at: 1, updated_at: 1, project_id: "a" }],
      ["r2", { id: "r2", parent_id: null, agent: "designer", provider: "fake", model: "m", task: "motor", status: "done", branch: null, onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0, created_at: 2, updated_at: 2, project_id: "b" }],
    ]);
    await buildActions.refresh();
    selectedPartId.value = "arm";
    selectedRunId.value = "r1";
    await buildActions.switchTo("b");
    expect(openBuildIds.value).toEqual(["a", "b"]);
    expect(c.setBuildTabs).toHaveBeenLastCalledWith({ active_project_id: "b", open_project_ids: ["a", "b"] });
    expect(parts.value.map((p) => p.part_id)).toEqual(["motor"]);
    expect(selectedPartId.value).toBeNull();
    expect(selectedRunId.value).toBe("r2");             // the run shown follows the build
    await buildActions.switchTo("a");
    expect(selectedPartId.value).toBe("arm");
  });

  it("a parts answer for a build you already left is dropped", async () => {
    const c = fakeClient([b("a", "Quad"), b("b", "Bench"), b("c", "Cam")]);
    let release: (() => void) | null = null;
    c.parts.mockImplementationOnce(() => new Promise((r) => { release = () => r([part("motor", "b")]); }));
    bindClient(c as unknown as CoreClient);
    await buildActions.refresh();
    const toB = buildActions.switchTo("b");
    await buildActions.switchTo("c");
    (release as unknown as () => void)();
    await toB;
    expect(parts.value.map((p) => p.part_id)).toEqual(["plate"]);
  });

  it("closing the active tab moves to its neighbour; the last tab can't close", async () => {
    const c = fakeClient([b("a", "Quad"), b("b", "Bench")], { active_project_id: "a", open_project_ids: ["a", "b"] });
    bindClient(c as unknown as CoreClient);
    await buildActions.refresh();
    await buildActions.close("a");
    expect(activeBuildId.value).toBe("b");
    expect(openBuildIds.value).toEqual(["b"]);
    await buildActions.close("b");
    expect(openBuildIds.value).toEqual(["b"]);
  });

  it("trash moves the active build out of the tabs; restore brings it back as the active tab", async () => {
    const all = [b("a", "Quad"), b("b", "Bench")];
    const c = fakeClient(all, { active_project_id: "a", open_project_ids: ["a", "b"] });
    bindClient(c as unknown as CoreClient);
    await buildActions.refresh();
    await buildActions.trash("a");
    expect(c.trashBuild).toHaveBeenCalledWith("a");
    expect(activeBuildId.value).toBe("b");
    expect(openBuildIds.value).toEqual(["b"]);
    expect(trashedBuilds.value.map((x) => x.id)).toEqual(["a"]);
    await buildActions.restore("a");
    expect(activeBuildId.value).toBe("a");
    expect(trashedBuilds.value).toEqual([]);
  });

  it("create switches to the new build; empty trash removes only trashed builds", async () => {
    const c = fakeClient([b("a", "Quad"), b("t", "Old", 9)]);
    bindClient(c as unknown as CoreClient);
    await buildActions.refresh();
    await buildActions.create("Gimbal");
    expect(activeBuildId.value).toBe("n");
    expect(openBuilds.value.map((x) => x.name)).toEqual(["Quad", "Gimbal"]);
    await buildActions.emptyTrash();
    expect(builds.value.map((x) => x.id)).toEqual(["a", "n"]);
  });

  it("a refused Empty Trash keeps every build and names the runs that block it", async () => {
    const c = fakeClient([b("a", "Quad"), b("t", "Old", 9)]);
    c.emptyTrash.mockResolvedValueOnce({ refused: { reason: "running", run_ids: ["r1", "zz"], project_ids: ["t"], message: null, hint: null } } as never);
    runs.value = new Map([["r1", { id: "r1", parent_id: null, agent: "designer", provider: "fake", model: "m", task: "stiffen the arm", status: "running",
      branch: null, onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0, created_at: 1, updated_at: 1 } as AgentRun]]);
    bindClient(c as unknown as CoreClient);
    await buildActions.refresh();
    await buildActions.emptyTrash();
    expect(builds.value.map((x) => x.id)).toEqual(["a", "t"]);
    expect(trashBlocked.value?.run_ids).toEqual(["r1", "zz"]);
    expect(buildsError.value).toBe("The Trash wasn't emptied: 2 runs in trashed builds are still working (designer · stiffen arm, run zz). Wait for them to finish or stop them, then empty it again.");
    // the next try starts clean
    await buildActions.emptyTrash();
    expect(trashBlocked.value).toBe(null);
    expect(builds.value.map((x) => x.id)).toEqual(["a"]);
  });

  it("trashRefusalText words an approval block", () => {
    expect(trashRefusalText({ reason: "pending_approval", run_ids: ["x"], project_ids: [], message: null, hint: null }, (id) => id))
      .toBe("The Trash wasn't emptied: 1 run in a trashed build is waiting for your approval (x). Decide its card, then empty it again.");
  });
});

describe("integration: a build switch reaches the other build-scoped views", () => {
  it("onBuildSwitch listeners (A4's assembly, A7's spec) get the new build; the run switcher lists its runs", async () => {
    const { onBuildSwitch } = await import("./builds");
    const { buildRuns } = await import("../ui/console/derived");
    const c = fakeClient([b("a", "Quad"), b("b", "Bench")]);
    bindClient(c as unknown as CoreClient);
    const run = (id: string, project: string | null, t: number): AgentRun => ({ id, parent_id: null, agent: "designer", provider: "fake", model: "m", task: id,
      status: "done", branch: null, onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0, created_at: t, updated_at: t, project_id: project });
    runs.value = new Map([["r1", run("r1", "a", 1)], ["r2", run("r2", "b", 2)], ["r0", run("r0", null, 0)]]);
    await buildActions.refresh();
    const seen: string[] = [];
    const off = onBuildSwitch((id) => seen.push(id));
    expect(buildRuns.value.map((r) => r.id).sort()).toEqual(["r0", "r1"]);   // build a, plus a build-less run
    await buildActions.switchTo("b");
    expect(seen).toEqual(["b"]);
    expect(buildRuns.value.map((r) => r.id).sort()).toEqual(["r0", "r2"]);
    await buildActions.switchTo("b");                                          // no change: no event
    expect(seen).toEqual(["b"]);
    off();
  });

  it("a switch waits for the profile save; a failed save is said in plain words and the switch stays (I-2)", async () => {
    const c = fakeClient([b("a", "Quad"), b("b", "Bench")]);
    bindClient(c as unknown as CoreClient);
    await buildActions.refresh();
    let reject!: (e: Error) => void;
    c.setBuildTabs.mockImplementationOnce(() => new Promise((_r, rej) => { reject = rej; }));
    let done = false;
    const sw = buildActions.switchTo("b").then(() => { done = true; });
    expect(activeBuildId.value).toBe("b");             // the UI switches at once
    expect(runBuild()).toEqual({ project_id: "b" });   // and a new run started now names the new build
    await new Promise((r) => setTimeout(r, 0));
    expect(done).toBe(false);                          // still saving
    reject(new Error("disk full"));
    await sw;
    expect(buildsError.value).toContain("Saving the open builds failed");
    expect(buildsError.value).toContain("disk full");
    expect(activeBuildId.value).toBe("b");
  });

  it("runBuild is empty before the builds load", () => {
    expect(runBuild()).toEqual({});
  });
});
