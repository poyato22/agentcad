// Studies page state: solver status, the part's study history, the shown study, and the study
// being run. A study runs in the core as a "studies" run (POST /sims → 202); the page follows it
// by polling GET /sims?run_id= and by its `sim_progress` events (onEvent: the stage shows at once,
// and a terminal stage polls now instead of on the next tick); it stops it with POST /runs/{id}/cancel.
// One controller per app (module level, see index.tsx): a study keeps running in the core when the
// user leaves the page, and `active` remembers it. Polling only runs while the page is mounted:
// the page calls pause() on unmount and resume() on mount, which polls at once and picks the
// study up where it is (or shows its result if it finished meanwhile).
import { computed, signal, type ReadonlySignal, type Signal } from "@preact/signals";
import { CoreError, type CoreClient } from "../../../api/client";
import { simProgressSchema, type SimRecord, type SimStage, type SimSolvers, type SimSummary } from "../../../api/sims";
import type { MisesEvent } from "../../../api/types";
import { askPrompt, setupProblems, startBody, type Setup } from "./model";

export type StudiesClient = Pick<CoreClient, "simSolvers" | "listSims" | "getSim" | "startSim" | "cancel" | "getRun" | "steps" | "createRun">;

export interface ActiveStudy {
  runId: string; partId: string; startedAt: number;
  /** null until the core has stored the study record. */
  stage: SimStage | null; simId: string | null; cancelling: boolean;
}

export interface Problem { message: string; hint: string | null }

export const POLL_MS = 600;
/** Network blips while following a study are retried this many times in a row before giving up. */
const MAX_POLL_FAILURES = 20;

const messageOf = (e: unknown): Problem => e instanceof CoreError
  ? { message: e.message, hint: e.hint }
  : { message: e instanceof Error ? e.message : String(e), hint: null };

export class StudiesController {
  readonly solvers: Signal<SimSolvers | null> = signal(null);
  readonly solversError: Signal<string | null> = signal(null);
  readonly ccxMissing: ReadonlySignal<boolean> = computed(() => this.solvers.value?.ccx.found === false);
  readonly historyPart: Signal<string | null> = signal(null);
  readonly history: Signal<readonly SimSummary[]> = signal([]);
  readonly historyError: Signal<string | null> = signal(null);
  readonly selectedSimId: Signal<string | null> = signal(null);
  readonly record: Signal<SimRecord | null> = signal(null);
  readonly recordError: Signal<string | null> = signal(null);
  readonly active: Signal<ActiveStudy | null> = signal(null);
  /** Why the last start did not produce a study (bad setup, refused, failed before a record). */
  readonly runError: Signal<Problem | null> = signal(null);
  readonly asked: Signal<{ simId: string; runId: string | null; error: string | null } | null> = signal(null);

  private readonly client: () => StudiesClient;
  private readonly pollMs: number;
  private selectSeq = 0;
  private historySeq = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private disposed = false;
  private paused = false;

  constructor(client: () => StudiesClient, opts: { pollMs?: number } = {}) {
    this.client = client;
    this.pollMs = opts.pollMs ?? POLL_MS;
  }

  async loadSolvers(): Promise<void> {
    try {
      this.solvers.value = await this.client().simSolvers();
      this.solversError.value = null;
    } catch (e) {
      this.solversError.value = messageOf(e).message;
    }
  }

  /** Loads the part's studies; shows the newest finished one unless one of this part is shown. */
  async loadHistory(partId: string): Promise<void> {
    const seq = ++this.historySeq;
    if (this.historyPart.peek() !== partId) {
      this.historyPart.value = partId;
      this.history.value = [];
      this.selectedSimId.value = null;
      this.record.value = null;
      this.asked.value = null;
    }
    let list: SimSummary[];
    try {
      list = await this.client().listSims({ partId });
    } catch (e) {
      if (seq === this.historySeq) this.historyError.value = messageOf(e).message;
      return;
    }
    if (seq !== this.historySeq) return;
    this.history.value = list;
    this.historyError.value = null;
    const sel = this.selectedSimId.peek();
    const shown = sel === null ? undefined : list.find((s) => s.sim_id === sel);
    if (!shown) {
      const pick = list.find((s) => s.status === "done") ?? list.find((s) => s.status !== "running") ?? null;
      if (pick) void this.select(pick.sim_id);
    } else {
      // called again when the part's mtime changes: re-read the shown study so its stale flag
      // (geometry changed since) and status are current without the user reselecting it
      const rec = this.record.peek();
      if (rec && rec.sim_id === shown.sim_id && (rec.stale !== shown.stale || rec.status !== shown.status)) void this.select(shown.sim_id);
    }
    // a study of this part still running (started before the page opened): follow it
    const running = list.find((s) => s.status === "running" && s.run_id);
    if (running?.run_id && this.active.peek() === null) {
      this.active.value = { runId: running.run_id, partId, startedAt: running.created_at, stage: running.stage, simId: running.sim_id, cancelling: false };
      this.schedule(running.run_id);
    }
  }

  async select(simId: string): Promise<void> {
    const seq = ++this.selectSeq;
    this.selectedSimId.value = simId;
    if (this.record.peek()?.sim_id !== simId) this.record.value = null;
    this.recordError.value = null;
    try {
      const rec = await this.client().getSim(simId);
      if (seq === this.selectSeq) this.record.value = rec;
    } catch (e) {
      if (seq === this.selectSeq) this.recordError.value = messageOf(e).message;
    }
  }

  async start(partId: string, setup: Setup): Promise<void> {
    if (this.active.peek()) return; // one study at a time from the page
    const problems = setupProblems(setup);
    this.runError.value = problems.length ? { message: problems.join("; "), hint: null } : null;
    if (problems.length) return;
    let runId: string;
    try {
      ({ run_id: runId } = await this.client().startSim(startBody(partId, setup)));
    } catch (e) {
      this.runError.value = messageOf(e);
      return;
    }
    this.active.value = { runId, partId, startedAt: Date.now() / 1000, stage: null, simId: null, cancelling: false };
    this.schedule(runId);
  }

  async cancel(): Promise<void> {
    const a = this.active.peek();
    if (!a || a.cancelling) return;
    this.active.value = { ...a, cancelling: true };
    try {
      await this.client().cancel(a.runId);
    } catch (e) {
      const cur = this.active.peek();
      if (cur?.runId === a.runId) this.active.value = { ...cur, cancelling: false };
      this.runError.value = messageOf(e);
    }
    this.schedule(a.runId, 0);
  }

  /** "Ask agent to fix": a designer run, allowed to edit only this part, with the result as context. */
  async ask(rec: SimRecord): Promise<string | null> {
    const prev = this.asked.peek();
    if (prev && prev.simId === rec.sim_id && prev.error === null) return prev.runId; // sending or sent: once per study
    this.asked.value = { simId: rec.sim_id, runId: null, error: null };
    try {
      const { run_id } = await this.client().createRun({ agent: "designer", prompt: askPrompt(rec), edit_scope: [rec.part_id] });
      this.asked.value = { simId: rec.sim_id, runId: run_id, error: null };
      return run_id;
    } catch (e) {
      const p = messageOf(e);
      this.asked.value = { simId: rec.sim_id, runId: null, error: p.hint ? `${p.message} (${p.hint})` : p.message };
      return null;
    }
  }

  /** A core event: `sim_progress` of the followed study's run updates its stage now; a terminal
   *  stage (done / failed / cancelled) polls at once for the result. Other events are ignored. */
  onEvent(ev: MisesEvent): void {
    if (ev.type !== "sim_progress" || !ev.run_id) return;
    const a = this.active.peek();
    if (!a || a.runId !== ev.run_id) return;
    const p = simProgressSchema.safeParse(ev.data);
    if (!p.success || p.data.part_id !== a.partId) return;
    if (a.stage !== p.data.stage || a.simId !== p.data.sim_id) this.active.value = { ...a, stage: p.data.stage, simId: p.data.sim_id };
    if (p.data.stage === "done" || p.data.stage === "failed" || p.data.stage === "cancelled") this.schedule(a.runId, 0);
  }

  /** Stop polling (the page is hidden). The followed study stays in `active`. */
  pause(): void {
    this.paused = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  /** Poll again (the page is shown): at once, if a study is being followed. */
  resume(): void {
    this.paused = false;
    const a = this.active.peek();
    if (a) this.schedule(a.runId, 0);
  }

  dispose(): void {
    this.disposed = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  // ---- following a running study

  private failures = 0;

  private schedule(runId: string, ms = this.pollMs): void {
    if (this.disposed || this.paused) return;
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => { this.timer = null; void this.poll(runId); }, ms);
  }

  private async poll(runId: string): Promise<void> {
    const a = this.active.peek();
    if (!a || a.runId !== runId || this.disposed || this.paused) return;
    try {
      const [s] = await this.client().listSims({ runId });
      const cur = this.active.peek();
      if (!cur || cur.runId !== runId) return;
      this.failures = 0;
      if (s && s.status === "running") {
        if (cur.stage !== s.stage || cur.simId !== s.sim_id) this.active.value = { ...cur, stage: s.stage, simId: s.sim_id };
        this.schedule(runId);
        return;
      }
      if (s) { await this.finish(cur, s); return; }
      // no study stored: the tool refused the setup before solving, or the run is still starting
      const run = await this.client().getRun(runId);
      if (run.status === "running") { this.schedule(runId); return; }
      this.runError.value = await this.runFailure(runId, run.status);
      this.active.value = null;
      void this.loadHistory(cur.partId);
    } catch (e) {
      if (++this.failures >= MAX_POLL_FAILURES || !(e instanceof CoreError && (e.code === "network" || e.retryable))) {
        this.runError.value = { message: `lost track of the study: ${messageOf(e).message}`, hint: "it may still be running; reopen the page to check" };
        this.active.value = null;
        return;
      }
      this.schedule(runId);
    }
  }

  private async finish(a: ActiveStudy, s: SimSummary): Promise<void> {
    this.active.value = null;
    if (s.status === "failed" && /ccx|calculix/i.test(`${s.error ?? ""} ${s.hint ?? ""}`)) void this.loadSolvers();
    if (this.historyPart.peek() === a.partId) {
      this.selectedSimId.value = s.sim_id;
      await this.loadHistory(a.partId);
      await this.select(s.sim_id);
    }
  }

  private async runFailure(runId: string, status: string): Promise<Problem> {
    try {
      const steps = await this.client().steps(runId);
      const err = [...steps].reverse().find((st) => st.kind === "error");
      const p = err?.payload as { message?: unknown; hint?: unknown } | undefined;
      if (p && typeof p.message === "string") return { message: p.message, hint: typeof p.hint === "string" ? p.hint : null };
    } catch { /* fall through */ }
    return { message: `the study run ${status} before the solver started`, hint: null };
  }
}
