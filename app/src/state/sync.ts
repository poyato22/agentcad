// Effect runner: feeds core events through reduce(), at most once per animation frame, and runs
// the requested refetches (deduped per frame, one in flight per key). Fetch results are written
// in the next frame too (together with that frame's events, in one signals batch), so a burst
// never renders more than once per frame. On every (re)open of the event stream it resyncs
// runs, approvals, parts, status and the steps after the last known id of every known run, so a
// dropped socket, a core restart or bus overflow never loses or duplicates log rows (steps are
// merged by id).
import { batch } from "@preact/signals";
import type { CoreClient } from "../api/client";
import type { EventStream } from "../api/events";
import type { MisesEvent } from "../api/types";
import { debug } from "../lib/log";
import { applyRuns, applySteps, mergeApprovals, reduce, type Effect } from "./reduce";
import { approvals, commitAppState, conn, coreStatus, noteRunStarted, parts, readAppState } from "./store";

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

/** Writes a fetch result into the store; runs inside a frame. */
type Apply = () => void;

export function startSync(client: CoreClient, stream: Source, opts: SyncOptions = {}): () => void {
  const schedule = opts.schedule ?? defaultSchedule;
  const onError = opts.onError ?? ((err: unknown, eff: Effect) => debug("sync effect failed", eff.kind, err));
  let stopped = false;
  let cancelFrame: (() => void) | null = null;
  let events: MisesEvent[] = [];
  let results: { key: string; apply: Apply }[] = [];
  const queued = new Map<string, Effect>();
  const running = new Set<string>();   // in flight or waiting for its frame (keeps the steps cursor right)
  const dirty = new Map<string, Effect>();

  const requestFrame = () => {
    if (stopped || cancelFrame) return;
    cancelFrame = schedule(flush);
  };

  const enqueue = (e: Effect) => { queued.set(keyOf(e), e); };

  function release(key: string) {
    running.delete(key);
    const again = dirty.get(key);
    if (again && !stopped) {
      dirty.delete(key);
      enqueue(again);
      requestFrame();
    }
  }

  function flush() {
    cancelFrame = null;
    if (stopped) return;
    batch(() => {
      const done = results;
      results = [];
      for (const r of done) {
        try { r.apply(); } catch (e) { debug("sync apply failed", r.key, e); }
        release(r.key);
      }
      if (events.length) {
        let state = readAppState();
        for (const ev of events) {
          if (ev.type === "run_started" && ev.run_id && ev.data.resumed !== true) noteRunStarted(ev.run_id);
          const r = reduce(state, ev);
          state = r.state;
          r.effects.forEach(enqueue);
        }
        events = [];
        commitAppState(state);
      }
    });
    const toStart = [...queued.values()];
    queued.clear();
    for (const eff of toStart) start(eff);
  }

  function start(eff: Effect) {
    const key = keyOf(eff);
    if (running.has(key)) { dirty.set(key, eff); return; }
    running.add(key);
    run(eff).then((apply) => {
      if (stopped) return;
      results.push({ key, apply });
      requestFrame();
    }, (err: unknown) => {
      if (stopped) return;
      onError(err, eff);
      release(key);
    });
  }

  async function run(eff: Effect): Promise<Apply> {
    switch (eff.kind) {
      case "fetchSteps": {
        const have = readAppState().steps.get(eff.runId);
        const after = have && have.length ? have[have.length - 1].id : 0;
        const list = await client.steps(eff.runId, after);
        return () => commitAppState(applySteps(readAppState(), eff.runId, list));
      }
      case "fetchRuns": {
        const list = await client.listRuns();
        return () => {
          const next = applyRuns(readAppState(), list);
          commitAppState(next);
          // runs we have never loaded steps for (new, or first boot); started later in this frame
          for (const id of next.runs.keys()) if (!next.steps.has(id)) enqueue({ kind: "fetchSteps", runId: id });
        };
      }
      case "fetchApprovals": {
        const list = await client.pendingApprovals();
        return () => { approvals.value = mergeApprovals(approvals.peek(), list); };
      }
      case "fetchParts": {
        const list = await client.parts();
        return () => { parts.value = list; };
      }
      case "fetchStatus": {
        const s = await client.status();
        return () => { coreStatus.value = s; };
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
    results = [];
    queued.clear();
    dirty.clear();
  };
}
