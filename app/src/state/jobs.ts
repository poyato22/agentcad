// Plan 9 D4 (B3): the shown build's running background jobs (an agent's studies, reviews and
// design studies, GET /jobs). An agent whose turn has ended can still have some: the composer's
// Stop / Esc count them as busy, and each running card in the Simulations stream has its own Stop
// (POST /jobs/{id}/cancel). Refetched when the build changes and on the events that start or end a
// job (a sim tool's tool_result, sim_finished); a job's sim_finished drops it at once.
import { computed, effect, signal, type ReadonlySignal, type Signal } from "@preact/signals";
import type { BackgroundJob } from "../api/jobs";
import { onCoreEvent } from "../api/events";
import type { MisesEvent } from "../api/types";
import { debug } from "../lib/log";
import { activeBuildId } from "./builds";
import { actions, conn } from "./store";

export const liveJobs: Signal<readonly BackgroundJob[]> = signal<readonly BackgroundJob[]>([]);
/** Job ids the user asked to stop (the card shows "stopping…" until sim_finished). */
export const stoppingJobs: Signal<ReadonlySet<string>> = signal<ReadonlySet<string>>(new Set());

/** The running jobs of the shown build. */
export const buildJobs: ReadonlySignal<readonly BackgroundJob[]> = computed(() => {
  const b = activeBuildId.value;
  return b ? liveJobs.value.filter((j) => j.build_id === b) : [];
});

export function isLiveJob(jobId: string | null): boolean {
  return jobId !== null && liveJobs.value.some((j) => j.job_id === jobId);
}

let seq = 0;
export async function refreshJobs(): Promise<void> {
  const b = activeBuildId.peek();
  const mine = ++seq;
  if (!b) { liveJobs.value = []; return; }
  try {
    const list = await actions.client().listJobs(b);
    if (mine === seq) liveJobs.value = list;
  } catch (e) {
    debug("jobs refresh failed", e);   // an older core has no /jobs: nothing is a job
  }
}

/** Stop one job. Resolves false when the core refused (it already ended). */
export async function stopJob(jobId: string): Promise<boolean> {
  stoppingJobs.value = new Set([...stoppingJobs.peek(), jobId]);
  try {
    await actions.client().cancelJob(jobId);
    return true;
  } catch (e) {
    debug("job cancel failed", jobId, e);
    dropJob(jobId);
    return false;
  }
}

/** Stop every running job of the shown build (the composer's Stop / Esc). */
export async function stopBuildJobs(): Promise<void> {
  await Promise.all(buildJobs.peek().map((j) => stopJob(j.job_id)));
}

function dropJob(jobId: string): void {
  liveJobs.value = liveJobs.peek().filter((j) => j.job_id !== jobId);
  if (stoppingJobs.peek().has(jobId)) {
    const s = new Set(stoppingJobs.peek());
    s.delete(jobId);
    stoppingJobs.value = s;
  }
}

const SIM_TOOLS = /^(mcp__mises__)?(sim_run|sim_static_run|design_review|study_run)$/;

let timer: ReturnType<typeof setTimeout> | undefined;
function soon(): void {
  clearTimeout(timer);
  timer = setTimeout(() => { void refreshJobs(); }, 150);
}

/** A core event that starts or ends a job (exported for tests). */
export function onJobEvent(ev: MisesEvent): void {
  if (ev.type === "sim_finished") {
    const id = ev.data.job_id;
    if (typeof id === "string") dropJob(id);
    soon();
  } else if (ev.type === "tool_result" && typeof ev.data.name === "string" && SIM_TOOLS.test(ev.data.name)) {
    soon();
  }
}

let wired = false;
/** Start following the shown build's jobs (once; App calls it). */
export function watchJobs(): void {
  if (wired) return;
  wired = true;
  onCoreEvent(onJobEvent);
  // the shown build changed, or the core (re)connected: what runs there now
  effect(() => { void activeBuildId.value; if (conn.value === "open") void refreshJobs(); });
}

/** Test helper. */
export function resetJobs(): void {
  liveJobs.value = [];
  stoppingJobs.value = new Set();
  clearTimeout(timer);
}
