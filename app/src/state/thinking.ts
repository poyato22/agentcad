// Model thinking text per run, from `agent_thinking {run_id, text_delta}` (Plan 5 Activity
// contract). Display-only: the thread's "Show thinking" disclosure renders it in faint type. Kept
// to the last THINKING_MAX characters per run; a run's buffer clears when it goes idle or done.
import { signal, type Signal } from "@preact/signals";
import { onCoreEvent } from "../api/events";
import type { MisesEvent } from "../api/types";

/** Characters kept per run (the core stores at most 4000; the live view keeps a little more). */
export const THINKING_MAX = 6000;

export const thinkingByRun: Signal<ReadonlyMap<string, string>> = signal<ReadonlyMap<string, string>>(new Map());

export function recordThinking(ev: MisesEvent): void {
  const runId = ev.run_id ?? (typeof ev.data.run_id === "string" ? ev.data.run_id : null);
  if (!runId) return;
  if (ev.type === "agent_thinking") {
    const delta = ev.data.text_delta;
    if (typeof delta !== "string" || delta === "") return;
    const m = new Map(thinkingByRun.value);
    const next = (m.get(runId) ?? "") + delta;
    m.set(runId, next.length > THINKING_MAX ? next.slice(next.length - THINKING_MAX) : next);
    thinkingByRun.value = m;
    return;
  }
  // a finished turn: its thinking is stored as a step by the core; drop the live buffer
  const state = ev.type === "agent_activity" ? ev.data.state : null;
  if ((state === "idle" || state === "done" || ev.type === "run_finished") && thinkingByRun.value.has(runId)) {
    const m = new Map(thinkingByRun.value);
    m.delete(runId);
    thinkingByRun.value = m;
  }
}

/** Subscribes to the event stream (default onCoreEvent). Call once at boot; returns an unsubscribe. */
export function startThinking(subscribe: (cb: (e: MisesEvent) => void) => () => void = onCoreEvent): () => void {
  return subscribe(recordThinking);
}

export function resetThinking(): void { thinkingByRun.value = new Map(); }
