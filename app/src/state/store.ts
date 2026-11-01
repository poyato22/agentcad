// The only mutable app state: Preact signals plus a small set of actions.
// Event-driven writes come from state/sync.ts (batched once per animation frame). UI code reads
// signals and calls `actions`; derived state belongs in `computed()` next to the component.
import { batch, signal, type Signal } from "@preact/signals";
import type { CoreClient } from "../api/client";
import type { ShellCoreStatus } from "../api/connection";
import type { StreamState } from "../api/events";
import type { AgentRun, Approval, CoreStatusInfo, PartSummary, Question, Step } from "../api/types";
import { forgetActivity } from "./activity";
import { forgetRuns, type AppState, type InflightCall } from "./reduce";

export type LogFilter = { scope: "run" | "all"; kind: "everything" | "decisions" | "tool_calls" | "files" | "errors" };

export const DEFAULT_FILTER: LogFilter = { scope: "run", kind: "everything" };

export const conn: Signal<StreamState> = signal<StreamState>("connecting");
export const shellStatus: Signal<ShellCoreStatus> = signal<ShellCoreStatus>({ state: "starting" });

export const runs: Signal<ReadonlyMap<string, AgentRun>> = signal<ReadonlyMap<string, AgentRun>>(new Map());
export const steps: Signal<ReadonlyMap<string, readonly Step[]>> = signal<ReadonlyMap<string, readonly Step[]>>(new Map());
/** text_delta buffer per run; cleared when an assistant message step lands or the run finishes. */
export const liveText: Signal<ReadonlyMap<string, string>> = signal<ReadonlyMap<string, string>>(new Map());
/** Tool calls started but without a result yet, by call_id. */
export const inflight: Signal<ReadonlyMap<string, InflightCall>> = signal<ReadonlyMap<string, InflightCall>>(new Map());
export const approvals: Signal<ReadonlyMap<string, Approval>> = signal<ReadonlyMap<string, Approval>>(new Map());
export const questions: Signal<ReadonlyMap<string, Question>> = signal<ReadonlyMap<string, Question>>(new Map());
/** Approval cards whose proposal a 409 showed was replaced: they can no longer be decided and no
 *  longer count as "needs you", even before the next /approvals sync drops them. */
export const retiredApprovals: Signal<ReadonlySet<string>> = signal<ReadonlySet<string>>(new Set());
export const parts: Signal<readonly PartSummary[]> = signal<readonly PartSummary[]>([]);
export const coreStatus: Signal<CoreStatusInfo | null> = signal<CoreStatusInfo | null>(null);

export const selectedRunId: Signal<string | null> = signal<string | null>(null);
export const selectedPartId: Signal<string | null> = signal<string | null>(null);
export const focusedStepId: Signal<number | null> = signal<number | null>(null);
export const filter: Signal<LogFilter> = signal<LogFilter>(DEFAULT_FILTER);
export const follow: Signal<boolean> = signal<boolean>(true);
/** The core process pid when the shell knows it (Tauri: server.json); null in dev/test. */
export const corePid: Signal<number | null> = signal<number | null>(null);
/** Runs this UI saw start (run_started, not resumed) since it connected to the current core. */
export const sessionRuns: Signal<ReadonlySet<string>> = signal<ReadonlySet<string>>(new Set());

let boundClient: CoreClient | null = null;

/** Called once at boot (main.tsx) and by tests. */
export function bindClient(client: CoreClient | null): void {
  boundClient = client;
}

export const actions = {
  selectRun(id: string | null): void { selectedRunId.value = id; },
  selectPart(id: string | null): void { selectedPartId.value = id; },
  focusStep(id: number): void { focusedStepId.value = id; },
  setFilter(f: LogFilter): void { filter.value = f; },
  setFollow(on: boolean): void { follow.value = on; },
  upsertApproval(a: Approval): void {
    const m = new Map(approvals.value);
    m.set(a.id, a);
    approvals.value = m;
  },
  removeQuestion(id: string): void {
    if (!questions.value.has(id)) return;
    const m = new Map(questions.value);
    m.delete(id);
    questions.value = m;
  },
  /** A run the app just changed (PATCH rename / pin / archive): every label shows it at once. */
  upsertRun(run: AgentRun): void {
    const known = runs.value.get(run.id);
    if (!known) return;
    const m = new Map(runs.value);
    m.set(run.id, { ...known, ...run });
    runs.value = m;
  },
  /** Runs the core deleted (DELETE /runs/{id}, Clean up, or a `run_deleted` event): they leave
   *  History, the switcher and the command line, and can no longer be opened or messaged (C-6). */
  forgetRuns(ids: readonly string[]): void { commitForgottenRuns(ids); },
  client(): CoreClient {
    if (!boundClient) throw new Error("core client not ready");
    return boundClient;
  },
};

// ---- used by state/sync.ts and state/session.ts (and tests); UI code should not call these

/** A different core (new token / address): forget which runs started in the previous one. */
export function beginCoreSession(pid: number | null): void {
  batch(() => {
    corePid.value = pid;
    sessionRuns.value = new Set();
  });
}

/** A run_started event (not a resume) for this core session. */
export function noteRunStarted(runId: string): void {
  if (sessionRuns.peek().has(runId)) return;
  sessionRuns.value = new Set([...sessionRuns.peek(), runId]);
}

/** Forget deleted runs everywhere the app holds them (C-6). */
export function commitForgottenRuns(ids: readonly string[]): void {
  if (ids.length === 0) return;
  batch(() => {
    commitAppState(forgetRuns(readAppState(), ids));
    dropRunRefs(ids);
  });
}

/** The selection, this session's runs and live activity no longer point at deleted runs (the
 *  state maps are cleaned by reduce.forgetRuns). */
export function dropRunRefs(ids: readonly string[]): void {
  const gone = new Set(ids);
  batch(() => {
    const sel = selectedRunId.peek();
    if (sel !== null && gone.has(sel)) selectedRunId.value = null;
    if (ids.some((id) => sessionRuns.peek().has(id))) sessionRuns.value = new Set([...sessionRuns.peek()].filter((id) => !gone.has(id)));
    forgetActivity(ids);
  });
}

export function readAppState(): AppState {
  return { runs: runs.value, steps: steps.value, liveText: liveText.value, inflight: inflight.value, approvals: approvals.value, questions: questions.value };
}

/** Writes only the maps that changed (by reference), in one batch. */
export function commitAppState(s: AppState): void {
  batch(() => {
    if (runs.peek() !== s.runs) runs.value = s.runs;
    if (steps.peek() !== s.steps) steps.value = s.steps;
    if (liveText.peek() !== s.liveText) liveText.value = s.liveText;
    if (inflight.peek() !== s.inflight) inflight.value = s.inflight;
    if (approvals.peek() !== s.approvals) approvals.value = s.approvals;
    if (questions.peek() !== s.questions) questions.value = s.questions;
  });
}

/** Back to boot state (tests). */
export function resetStore(): void {
  batch(() => {
    conn.value = "connecting";
    shellStatus.value = { state: "starting" };
    runs.value = new Map();
    steps.value = new Map();
    liveText.value = new Map();
    inflight.value = new Map();
    approvals.value = new Map();
    questions.value = new Map();
    retiredApprovals.value = new Set();
    parts.value = [];
    coreStatus.value = null;
    selectedRunId.value = null;
    selectedPartId.value = null;
    focusedStepId.value = null;
    filter.value = DEFAULT_FILTER;
    follow.value = true;
    corePid.value = null;
    sessionRuns.value = new Set();
  });
  boundClient = null;
}
