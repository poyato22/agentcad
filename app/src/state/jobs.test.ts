// Plan 9 B3: the shown build's running background jobs follow GET /jobs and the events that
// start or end one; a failed cancel (the job already ended) drops it.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../api/client";
import type { BackgroundJob } from "../api/jobs";
import { activeBuildId } from "./builds";
import { buildJobs, isLiveJob, liveJobs, onJobEvent, refreshJobs, resetJobs, stopJob, stoppingJobs } from "./jobs";
import { bindClient, resetStore } from "./store";

const [JOB] = JSON.parse(readFileSync(resolve(__dirname, "../../../core/tests/fixtures/ui/p9/streams_jobs.json"), "utf8")) as BackgroundJob[];

afterEach(() => { resetJobs(); resetStore(); activeBuildId.value = null; vi.useRealTimers(); });

describe("background jobs state", () => {
  it("lists the shown build's jobs from GET /jobs", async () => {
    const listJobs = vi.fn().mockResolvedValue([JOB]);
    bindClient({ listJobs } as unknown as CoreClient);
    activeBuildId.value = JOB.build_id;
    await refreshJobs();
    expect(listJobs).toHaveBeenCalledWith(JOB.build_id);
    expect(buildJobs.value.map((j) => j.job_id)).toEqual([JOB.job_id]);
    expect(isLiveJob(JOB.job_id)).toBe(true);
    activeBuildId.value = "another";
    expect(buildJobs.value).toEqual([]);
  });

  it("sim_finished drops the job at once; a sim tool's result refetches", async () => {
    vi.useFakeTimers();
    const listJobs = vi.fn().mockResolvedValue([]);
    bindClient({ listJobs } as unknown as CoreClient);
    activeBuildId.value = JOB.build_id;
    liveJobs.value = [JOB];
    onJobEvent({ type: "sim_finished", run_id: JOB.run_id, ts: 0, data: { job_id: JOB.job_id, status: "done" } });
    expect(liveJobs.value).toEqual([]);
    listJobs.mockClear();
    onJobEvent({ type: "tool_result", run_id: JOB.run_id, ts: 0, data: { name: "mcp__mises__design_review", ok: true } });
    onJobEvent({ type: "tool_result", run_id: JOB.run_id, ts: 0, data: { name: "mirror_measure", ok: true } });
    await vi.advanceTimersByTimeAsync(200);
    expect(listJobs).toHaveBeenCalledTimes(1);           // debounced, and only for a job tool
  });

  it("a refused cancel (the job already ended) drops the job", async () => {
    const cancelJob = vi.fn().mockRejectedValue(new Error("409"));
    bindClient({ cancelJob } as unknown as CoreClient);
    liveJobs.value = [JOB];
    expect(await stopJob(JOB.job_id)).toBe(false);
    expect(liveJobs.value).toEqual([]);
    expect(stoppingJobs.value.has(JOB.job_id)).toBe(false);
  });
});
