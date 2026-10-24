// Effect runner: feeds core events through reduce(), at most once per animation frame, and runs
// the requested refetches (deduped per frame, one in flight per key). On every (re)open of the
// event stream it resyncs runs, approvals, parts, status and the steps after the last known id of
// every known run, so a dropped socket, a core restart or bus overflow never loses or duplicates
// log rows (steps are merged by id).
import type { CoreClient } from "../api/client";
import type { EventStream } from "../api/events";
import type { MisesEvent } from "../api/types";
import { debug } from "../lib/log";
import { applyRuns, applySteps, mergeApprovals, reduce, type Effect } from "./reduce";
import { approvals, commitAppState, conn, coreStatus, parts, readAppState } from "./store";

export interface SyncOptions {
  /** Schedules one flush; returns a cancel function. Default: requestAnimationFrame. */
  schedule?: (cb: () => void) => () => void;
  onError?: (err: unknown, effect: Effect) => void;
}

type Source = Pick<EventStream, "onEvent" | "onState">;

function defaultSchedule(cb: () => void): () => void {
  if (typeof requestAnimationFrame === "function") {
    const id = requestAnimationFrame(() => cb());
    return () => cancelAnimationFrame(id);
  }
  const id = setTimeout(cb, 16);
  return () => clearTimeout(id);
}

const keyOf = (e: Effect): string => (e.kind === "fetchSteps" ? `fetchSteps:${e.runId}` : e.kind);

export function startSync(client: CoreClient, stream: Source, opts: SyncOptions = {}): () => void {
  const schedule = opts.schedule ?? defaultSchedule;
  const onError = opts.onError ?? ((err: unknown, eff: Effect) => debug("sync effect failed", eff.kind, err));
  let stopped = false;
  let cancelFrame: (() => void) | null = null;
  let events: MisesEvent[] = [];
  const queued = new Map<string, Effect>();
  const running = new Set<string>();
  const dirty = new Map<string, Effect>();

  const requestFrame = () => {
    if (stopped || cancelFrame) return;
    cancelFrame = schedule(flush);
  };

  const enqueue = (e: Effect) => { queued.set(keyOf(e), e); };

  function flush() {
    cancelFrame = null;
    if (stopped) return;
    if (events.length) {
      let state = readAppState();
      for (const ev of events) {
        const r = reduce(state, ev);
        state = r.state;
        r.effects.forEach(enqueue);
      }
      events = [];
      commitAppState(state);
    }
    const batch = [...queued.values()];
    queued.clear();
    for (const eff of batch) start(eff);
  }

  function start(eff: Effect) {
    const key = keyOf(eff);
    if (running.has(key)) { dirty.set(key, eff); return; }
    running.add(key);
    run(eff).catch((err: unknown) => { if (!stopped) onError(err, eff); }).finally(() => {
      running.delete(key);
      const again = dirty.get(key);
      if (again && !stopped) {
        dirty.delete(key);
        enqueue(again);
        requestFrame();
      }
    });
  }

  async function run(eff: Effect): Promise<void> {
    switch (eff.kind) {
      case "fetchSteps": {
        const have = readAppState().steps.get(eff.runId);
        const after = have && have.length ? have[have.length - 1].id : 0;
        const list = await client.steps(eff.runId, after);
        if (!stopped) commitAppState(applySteps(readAppState(), eff.runId, list));
        return;
      }
      case "fetchRuns": {
        const list = await client.listRuns();
        if (stopped) return;
        const next = applyRuns(readAppState(), list);
        commitAppState(next);
        // runs we have never loaded steps for (new, or first boot)
        let added = false;
        for (const id of next.runs.keys()) if (!next.steps.has(id)) { enqueue({ kind: "fetchSteps", runId: id }); added = true; }
        if (added) requestFrame();
        return;
      }
      case "fetchApprovals": {
        const list = await client.pendingApprovals();
        if (!stopped) approvals.value = mergeApprovals(approvals.peek(), list);
        return;
      }
      case "fetchParts": {
        const list = await client.parts();
        if (!stopped) parts.value = list;
        return;
      }
      case "fetchStatus": {
        const s = await client.status();
        if (!stopped) coreStatus.value = s;
        return;
      }
    }
  }

  const offState = stream.onState((s) => {
    if (stopped) return;
    conn.value = s;
    if (s !== "open") return;
    enqueue({ kind: "fetchRuns" });
    enqueue({ kind: "fetchApprovals" });
    enqueue({ kind: "fetchParts" });
    enqueue({ kind: "fetchStatus" });
    for (const id of readAppState().runs.keys()) enqueue({ kind: "fetchSteps", runId: id });
    requestFrame();
  });

  const offEvent = stream.onEvent((e) => {
    if (stopped) return;
    events.push(e);
    requestFrame();
  });

  return () => {
    stopped = true;
    offState();
    offEvent();
    cancelFrame?.();
    cancelFrame = null;
    events = [];
    queued.clear();
    dirty.clear();
  };
}
