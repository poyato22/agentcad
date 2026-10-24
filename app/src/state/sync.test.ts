import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { startSync } from "./sync";
import { approvals, conn, coreStatus, liveText, parts, resetStore, runs, sessionRuns, steps } from "./store";
import type { CoreClient } from "../api/client";
import type { StreamState } from "../api/events";
import type { AgentRun, Approval, CoreStatusInfo, MisesEvent, Step } from "../api/types";

const RID = "b7c79a57cbb4";
const R2 = "c0ffeec0ffee";

function run(id: string): AgentRun {
  return {
    id, parent_id: null, agent: "designer", provider: "fake", model: "fake-model", task: "thicken the plate",
    status: "running", branch: null, onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0,
    created_at: 1792869413, updated_at: 1792869413,
  };
}
function step(id: number, runId = RID): Step {
  return { id, run_id: runId, ts: id, kind: "tool_call", payload: { call_id: `c${id}`, name: "mirror_measure", input: {} }, evidence_hash: "e", prev_hash: null, collapsed: false };
}
const status: CoreStatusInfo = { core_version: "0.1.0", onshape: "not_configured", onshape_calls_year: 0, onshape_cap_year: 2500, runs_running: 1, approvals_pending: 0 };

class FakeStream {
  ev = new Set<(e: MisesEvent) => void>();
  st = new Set<(s: StreamState) => void>();
  onEvent(cb: (e: MisesEvent) => void) { this.ev.add(cb); return () => { this.ev.delete(cb); }; }
  onState(cb: (s: StreamState) => void) { this.st.add(cb); return () => { this.st.delete(cb); }; }
  emit(e: MisesEvent) { for (const cb of this.ev) cb(e); }
  state(s: StreamState) { for (const cb of this.st) cb(s); }
}

function fakeClient(stepsImpl: (runId: string, after: number) => Step[]) {
  const c = {
    listRuns: vi.fn(async () => [run(RID)]),
    pendingApprovals: vi.fn(async (): Promise<Approval[]> => []),
    parts: vi.fn(async () => []),
    status: vi.fn(async () => status),
    steps: vi.fn(async (runId: string, after = 0) => stepsImpl(runId, after)),
  };
  return { c, client: c as unknown as CoreClient };
}

// Manual frame scheduler: effects run only when the test flushes a "frame".
function frames() {
  let queued: (() => void) | null = null;
  return {
    schedule: (cb: () => void) => { queued = cb; return () => { queued = null; }; },
    async flush() {
      const cb = queued; queued = null; cb?.();
      // let the fake client's promises (and their .finally) settle
      for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
    },
    get pending() { return queued !== null; },
    /** Flush frames until none is booked (fetch results land one frame after they resolve). */
    async drain() {
      for (let i = 0; i < 20 && queued !== null; i++) await this.flush();
    },
  };
}

const toolCall = (runId: string, i: number): MisesEvent =>
  ({ type: "tool_call", run_id: runId, data: { call_id: `x${i}`, name: "mirror_measure", input: {} }, ts: i });

describe("startSync", () => {
  beforeEach(() => resetStore());
  afterEach(() => resetStore());

  it("open_resyncs_runs_approvals_parts_status_and_steps", async () => {
    const { c, client } = fakeClient(() => [step(1), step(2)]);
    const s = new FakeStream();
    const f = frames();
    const stop = startSync(client, s as never, { schedule: f.schedule });
    s.state("open");
    expect(conn.value).toBe("open");
    await f.drain();
    expect(c.listRuns).toHaveBeenCalledTimes(1);
    expect(c.pendingApprovals).toHaveBeenCalledTimes(1);
    expect(c.parts).toHaveBeenCalledTimes(1);
    expect(c.status).toHaveBeenCalledTimes(1);
    expect(c.steps).toHaveBeenCalledWith(RID, 0);
    expect(runs.value.get(RID)?.agent).toBe("designer");
    expect(steps.value.get(RID)?.map((x) => x.id)).toEqual([1, 2]);
    expect(coreStatus.value).toEqual(status);
    expect(parts.value).toEqual([]);
    expect(approvals.value.size).toBe(0);
    stop();
  });

  it("run_started_events_mark_runs_of_this_core_session_but_resumes_do_not", async () => {
    const { client } = fakeClient(() => []);
    const s = new FakeStream();
    const f = frames();
    const stop = startSync(client, s as never, { schedule: f.schedule });
    s.emit({ type: "run_started", run_id: "new1", data: { agent: "designer", provider: "fake", model: "m", parent_id: null }, ts: 1 });
    s.emit({ type: "run_started", run_id: "old1", data: { resumed: true }, ts: 2 });
    await f.flush();
    expect([...sessionRuns.value]).toEqual(["new1"]);
    stop();
  });

  it("fetch_results_land_in_the_next_frame_not_between_frames", async () => {
    const { client } = fakeClient(() => [step(1)]);
    const s = new FakeStream();
    const f = frames();
    const stop = startSync(client, s as never, { schedule: f.schedule });
    s.state("open");
    await f.flush(); // starts the fetches; their results resolve after this frame
    expect(runs.value.size).toBe(0); // resolved, but not written outside a frame
    expect(coreStatus.value).toBeNull();
    expect(f.pending).toBe(true); // a frame is booked for them
    await f.flush();
    expect(runs.value.get(RID)?.agent).toBe("designer");
    expect(coreStatus.value).toEqual(status);
    stop();
  });

  it("steps_merge_without_duplicates", async () => {
    const pages: Record<number, Step[]> = { 0: [step(1), step(2), step(3)], 3: [step(3), step(4), step(5)], 5: [step(4), step(5), step(6)] };
    const { c, client } = fakeClient((_r, after) => pages[after] ?? []);
    const s = new FakeStream();
    const f = frames();
    const stop = startSync(client, s as never, { schedule: f.schedule });
    runs.value = new Map([[RID, run(RID)]]);
    for (let i = 0; i < 3; i++) {
      s.emit(toolCall(RID, i));
      await f.flush();
    }
    await f.drain();
    expect(c.steps.mock.calls.map((x) => x[1])).toEqual([0, 3, 5]);
    const ids = steps.value.get(RID)?.map((x) => x.id) ?? [];
    expect(ids).toEqual([1, 2, 3, 4, 5, 6]);
    for (let i = 1; i < ids.length; i++) expect(ids[i]).toBeGreaterThan(ids[i - 1]);
    stop();
  });

  it("effects_coalesce_per_frame", async () => {
    const { c, client } = fakeClient(() => []);
    const s = new FakeStream();
    const f = frames();
    const stop = startSync(client, s as never, { schedule: f.schedule });
    runs.value = new Map([[RID, run(RID)], [R2, run(R2)]]);
    for (let i = 0; i < 100; i++) s.emit(toolCall(i % 2 ? RID : R2, i));
    expect(c.steps).not.toHaveBeenCalled(); // nothing runs before the frame
    await f.flush();
    expect(c.steps).toHaveBeenCalledTimes(2);
    expect(c.steps.mock.calls.map((x) => x[0]).sort()).toEqual([RID, R2].sort());
    stop();
  });

  it("events_apply_to_store_once_per_frame", async () => {
    const { client } = fakeClient(() => []);
    const s = new FakeStream();
    const f = frames();
    const stop = startSync(client, s as never, { schedule: f.schedule });
    let writes = 0;
    const unsub = liveText.subscribe(() => { writes++; });
    const before = writes;
    s.emit({ type: "text_delta", run_id: RID, data: { text: "a" }, ts: 1 });
    s.emit({ type: "text_delta", run_id: RID, data: { text: "b" }, ts: 2 });
    expect(f.pending).toBe(true);
    expect(liveText.value.size).toBe(0); // nothing written before the frame
    await f.flush();
    unsub();
    expect(writes - before).toBe(1);
    expect(liveText.value.get(RID)).toBe("ab");
    stop();
  });

  it("resync_on_reopen_fetches_after_last_id", async () => {
    const { c, client } = fakeClient((_r, after) => (after === 0 ? [step(7), step(8), step(9)] : [step(10)]));
    const s = new FakeStream();
    const f = frames();
    const stop = startSync(client, s as never, { schedule: f.schedule });
    s.state("open");
    await f.drain();
    expect(steps.value.get(RID)?.map((x) => x.id)).toEqual([7, 8, 9]);
    s.state("reconnecting");
    expect(conn.value).toBe("reconnecting");
    s.state("open");
    await f.drain();
    expect(c.steps).toHaveBeenLastCalledWith(RID, 9);
    expect(steps.value.get(RID)?.map((x) => x.id)).toEqual([7, 8, 9, 10]);
    stop();
  });

  it("failed_fetch_does_not_break_later_effects", async () => {
    let fail = true;
    const { c, client } = fakeClient(() => { if (fail) { fail = false; throw new Error("boom"); } return [step(1)]; });
    const s = new FakeStream();
    const f = frames();
    const stop = startSync(client, s as never, { schedule: f.schedule, onError: () => {} });
    runs.value = new Map([[RID, run(RID)]]);
    s.emit(toolCall(RID, 1));
    await f.flush();
    s.emit(toolCall(RID, 2));
    await f.flush();
    await f.drain();
    expect(c.steps).toHaveBeenCalledTimes(2);
    expect(steps.value.get(RID)?.map((x) => x.id)).toEqual([1]);
    stop();
  });

  it("stale_approvals_response_does_not_drop_new_card", async () => {
    const appr: Approval = {
      id: "a332e73aa8f2", run_id: RID, action: "fixture_merge", payload: {}, payload_hash: "f".repeat(64),
      microversion: null, deltas: {}, status: "pending", expires_at: 4102444800, decided_by: null, decided_at: null, edited_payload: null,
    };
    const { c, client } = fakeClient(() => []);
    let releaseStale: (v: Approval[]) => void = () => {};
    c.pendingApprovals
      .mockImplementationOnce(() => new Promise<Approval[]>((r) => { releaseStale = r; }))  // started before the approval existed
      .mockImplementation(async () => [appr]);
    const s = new FakeStream();
    const f = frames();
    const stop = startSync(client, s as never, { schedule: f.schedule });
    s.state("open");
    await f.flush();                      // fetchApprovals #1 in flight
    s.emit({ type: "approval_requested", run_id: RID, data: appr as unknown as Record<string, unknown>, ts: 1 });
    await f.flush();                      // card upserted; fetchApprovals marked dirty
    expect(approvals.value.has(appr.id)).toBe(true);
    releaseStale([]);                     // stale response: no pending approvals
    await f.drain();
    expect(c.pendingApprovals).toHaveBeenCalledTimes(2);
    expect(approvals.value.get(appr.id)?.status).toBe("pending");
    stop();
  });

  it("run_with_no_steps_is_fetched_once", async () => {
    const { c, client } = fakeClient(() => []);
    const s = new FakeStream();
    const f = frames();
    const stop = startSync(client, s as never, { schedule: f.schedule });
    s.state("open");
    await f.drain();
    expect(steps.value.get(RID)).toEqual([]);
    s.emit({ type: "run_finished", run_id: "zzz", data: { status: "done" }, ts: 1 }); // triggers fetchRuns again
    await f.drain();
    expect(c.listRuns).toHaveBeenCalledTimes(2);
    expect(c.steps.mock.calls.filter((x) => x[0] === RID)).toHaveLength(1);
    stop();
  });

  it("stop_unsubscribes", async () => {
    const { c, client } = fakeClient(() => []);
    const s = new FakeStream();
    const f = frames();
    const stop = startSync(client, s as never, { schedule: f.schedule });
    stop();
    s.emit(toolCall(RID, 1));
    await f.flush();
    expect(c.steps).not.toHaveBeenCalled();
    expect(s.ev.size).toBe(0);
  });
});
