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
import { activeBuildId, isActiveBuild } from "./builds";
import { seedActivity } from "./activity";
import { applyRuns, applySteps, deletedRunIds, forgetRuns, mergeApprovals, reduce, type Effect } from "./reduce";
import { approvals, commitAppState, conn, coreStatus, dropRunRefs, noteRunStarted, parts, readAppState } from "./store";

export interface SyncOptions {
  /** Schedules one flush; returns a cancel function. Default: requestAnimationFrame. */
  schedule?: (cb: () => void) => () => void;
  onError?: (err: unknown, effect: Effect) => void;
  /** Status poll period while a run is running or an approval is pending locally. Default 10 s. */
  pollMs?: number;
  /** Where window focus / tab visibility come from (default: the global window / document). */
  win?: Pick<EventTarget, "addEventListener" | "removeEventListener">;
  doc?: Pick<EventTarget, "addEventListener" | "removeEventListener"> & { visibilityState?: string };
}

export const STATUS_POLL_MS = 10_000;
/** GET /runs window the app loads (client.listRuns default). */
const RUNS_WINDOW = 50;

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
  /** The last fetchRuns returned a full window: the core may have running runs we never loaded. */
  let runsWindowFull = false;
  /** Counts (core vs ours) of the last mismatch we refetched for: the same again isn't refetched. */
  let lastMismatch: string | null = null;
  /** The stream (re)opened and the next runs list should seed live activity (C-3). */
  let seedPending = false;

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
          if (ev.type === "run_deleted") dropRunRefs(deletedRunIds(ev));
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
      case "fetchActivity": {
        // one GET per working run (a handful); a run that fails to answer keeps what it had
        const snaps = new Map<string, unknown>();
        await Promise.all(eff.runIds.map(async (id) => {
          try { snaps.set(id, await client.runActivity(id)); } catch (e) { debug("activity seed failed", id, e); }
        }));
        return () => seedActivity(snaps, eff.finished);
      }
      case "fetchRuns": {
        const list = await client.listRuns();
        return () => {
          runsWindowFull = list.length >= RUNS_WINDOW;
          let next = applyRuns(readAppState(), list);
          // a window that isn't full is every run the core has (but its own "ui" runs): a run we
          // know that it no longer lists was deleted elsewhere (C-6)
          if (!runsWindowFull) {
            const listed = new Set(list.map((r) => r.id));
            const gone = [...next.runs.values()].filter((r) => r.provider !== "ui" && !listed.has(r.id)).map((r) => r.id);
            if (gone.length) { next = forgetRuns(next, gone); dropRunRefs(gone); }
          }
          commitAppState(next);
          if (seedPending) {
            // the first runs list after the stream (re)opened: seed the pill and the manager (C-3)
            seedPending = false;
            const working = list.filter((r) => r.status === "running" || r.status === "queued").map((r) => r.id);
            const finished = list.filter((r) => r.status !== "running" && r.status !== "queued").map((r) => r.id);
            enqueue({ kind: "fetchActivity", runIds: working, finished });
          }
          // runs we have never loaded steps for (new, or first boot); started later in this frame
          for (const id of next.runs.keys()) if (!next.steps.has(id)) enqueue({ kind: "fetchSteps", runId: id });
        };
      }
      case "fetchApprovals": {
        const list = await client.pendingApprovals();
        return () => { approvals.value = mergeApprovals(approvals.peek(), list); };
      }
      case "fetchParts": {
        // the active build's tree; an answer for a build the user has since left is dropped
        const build = activeBuildId.peek();
        const list = await client.parts(build);
        return () => { if (isActiveBuild(build)) parts.value = list; };
      }
      case "fetchStatus": {
        const s = await client.status();
        return () => {
          coreStatus.value = s;
          // A dropped event (bus overflow drops the oldest at 1000) shows up as counts that
          // disagree with ours: reload approvals and runs. Skipped while either is already
          // being fetched (its answer is newer than this comparison could be).
          if (["fetchRuns", "fetchApprovals"].some((k) => running.has(k) || queued.has(k))) return;
          const st = readAppState();
          let pending = 0;
          for (const a of st.approvals.values()) if (a.status === "pending") pending++;
          let busy = 0;
          for (const r of st.runs.values()) if (r.status === "running") busy++;
          // with a full window loaded, more running runs on the core than here may be older runs
          // beyond it (e.g. one stuck "running"): only fewer is a sure sign we missed something
          const runsOff = runsWindowFull ? busy > s.runs_running : busy !== s.runs_running;
          if (pending === s.approvals_pending && !runsOff) { lastMismatch = null; return; }
          const sig = `${s.approvals_pending}/${s.runs_running}|${pending}/${busy}`;
          if (sig === lastMismatch) return; // a refetch already ran for exactly this; it can't fix it
          lastMismatch = sig;
          enqueue({ kind: "fetchApprovals" });
          enqueue({ kind: "fetchRuns" });
        };
      }
    }
  }

  const offState = stream.onState((s) => {
    if (stopped) return;
    conn.value = s;
    if (s !== "open") return;
    seedPending = true;
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

  // Events can be lost without a trace (bus overflow) and be followed by silence: while something
  // is in flight, ask /status every pollMs (one GET at a time: effects are deduped per key), and
  // whenever the window regains focus or the tab becomes visible.
  const pollStatus = () => {
    if (stopped || conn.peek() !== "open") return;
    enqueue({ kind: "fetchStatus" });
    requestFrame();
  };
  const busyLocally = () => {
    const st = readAppState();
    for (const r of st.runs.values()) if (r.status === "running") return true;
    for (const a of st.approvals.values()) if (a.status === "pending") return true;
    return false;
  };
  const timer = setInterval(() => { if (busyLocally()) pollStatus(); }, opts.pollMs ?? STATUS_POLL_MS);
  const win = opts.win ?? (typeof window !== "undefined" ? window : undefined);
  const doc = opts.doc ?? (typeof document !== "undefined" ? document : undefined);
  const onVisible = () => { if (!doc || doc.visibilityState === undefined || doc.visibilityState === "visible") pollStatus(); };
  win?.addEventListener("focus", pollStatus);
  doc?.addEventListener("visibilitychange", onVisible);

  return () => {
    stopped = true;
    clearInterval(timer);
    win?.removeEventListener("focus", pollStatus);
    doc?.removeEventListener("visibilitychange", onVisible);
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
