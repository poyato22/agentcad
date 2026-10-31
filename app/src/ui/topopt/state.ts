// Generative design state: which part's panel is open, the runs it knows (by topopt_id) with
// their live progress from `topopt_progress` events, and the actions (start, cancel, accept).
// One controller per app (module level): a run keeps going when the panel closes, and its card
// in the Simulations stream follows it. Events drive progress; polling every POLL_MS fills gaps
// (a dropped socket, a run started by an agent before the app opened).
import { signal, type Signal } from "@preact/signals";
import { CoreError, type CoreClient } from "../../api/client";
import { topoptEventSchema, type TopoptAcceptBody, type TopoptAcceptResult, type TopoptRecord } from "../../api/topopt";
import type { MisesEvent } from "../../api/types";
import { applyEvent, isRunning, merged, toBody, validate, type FormError, type GdForm, type LiveRun } from "./model";

export type TopoptClient = Pick<CoreClient, "topoptStart" | "topoptGet" | "topoptList" | "topoptCancel" | "topoptAccept" | "topoptGlb" | "topoptRender" | "partGlb">;

export const POLL_MS = 1500;

export interface Problem { message: string; hint: string | null }
const problemOf = (e: unknown): Problem => e instanceof CoreError
  ? { message: e.message, hint: e.hint }
  : { message: e instanceof Error ? e.message : String(e), hint: null };

export class TopoptController {
  /** The panel: the part it is for, and the run it shows (null: the setup form). */
  readonly panel: Signal<{ partId: string; topoptId: string | null } | null> = signal(null);
  readonly runs: Signal<ReadonlyMap<string, TopoptRecord>> = signal(new Map());
  readonly live: Signal<ReadonlyMap<string, LiveRun>> = signal(new Map());
  readonly errors: Signal<FormError[]> = signal([]);
  readonly problem: Signal<Problem | null> = signal(null);
  readonly busy: Signal<boolean> = signal(false);
  readonly accepted: Signal<TopoptAcceptResult | null> = signal(null);
  private timers = new Map<string, ReturnType<typeof setTimeout>>();

  readonly client: () => TopoptClient;

  constructor(client: () => TopoptClient) { this.client = client; }

  open(partId: string, topoptId: string | null = null): void {
    this.problem.value = null; this.errors.value = []; this.accepted.value = null;
    this.panel.value = { partId, topoptId };
    if (topoptId) void this.refresh(topoptId);
  }

  close(): void { this.panel.value = null; }

  /** The record with live progress laid over it. */
  view(topoptId: string): TopoptRecord | null {
    const rec = this.runs.value.get(topoptId);
    return rec ? merged(rec, this.live.value.get(topoptId)) : null;
  }

  private remember(rec: TopoptRecord): void {
    const next = new Map(this.runs.peek());
    next.set(rec.topopt_id, rec);
    this.runs.value = next;
    if (isRunning(rec)) this.schedule(rec.topopt_id);
  }

  private schedule(id: string): void {
    if (this.timers.has(id)) return;
    this.timers.set(id, setTimeout(() => { this.timers.delete(id); void this.refresh(id); }, POLL_MS));
  }

  async refresh(id: string): Promise<TopoptRecord | null> {
    try {
      const rec = await this.client().topoptGet(id);
      this.remember(rec);
      return rec;
    } catch (e) {
      this.problem.value = problemOf(e);
      return null;
    }
  }

  async start(form: GdForm): Promise<string | null> {
    const errs = validate(form);
    this.errors.value = errs;
    if (errs.length) return null;
    this.busy.value = true; this.problem.value = null;
    try {
      const rec = await this.client().topoptStart(toBody(form));
      this.remember(rec);
      this.panel.value = { partId: form.partId, topoptId: rec.topopt_id };
      return rec.topopt_id;
    } catch (e) {
      this.problem.value = problemOf(e);
      return null;
    } finally {
      this.busy.value = false;
    }
  }

  async cancel(id: string): Promise<void> {
    try { await this.client().topoptCancel(id); } catch (e) { this.problem.value = problemOf(e); }
    await this.refresh(id);
  }

  async accept(id: string, body: TopoptAcceptBody): Promise<TopoptAcceptResult | null> {
    this.busy.value = true; this.problem.value = null;
    try {
      const r = await this.client().topoptAccept(id, body);
      this.accepted.value = r;
      await this.refresh(id);
      return r;
    } catch (e) {
      this.problem.value = problemOf(e);
      return null;
    } finally {
      this.busy.value = false;
    }
  }

  onEvent(ev: MisesEvent): void {
    if (ev.type !== "topopt_progress" && ev.type !== "topopt_finished") return;
    const r = topoptEventSchema.safeParse(ev.data);
    if (!r.success) return;
    if (ev.type === "topopt_progress") this.live.value = applyEvent(this.live.peek(), ev.type, r.data);
    // a finish, or a run we have not seen yet (an agent started it): read the record
    if (ev.type === "topopt_finished" || !this.runs.peek().has(r.data.topopt_id)) void this.refresh(r.data.topopt_id);
  }

  /** Tests: forget everything. */
  reset(): void {
    for (const t of this.timers.values()) clearTimeout(t);
    this.timers.clear();
    this.panel.value = null; this.runs.value = new Map(); this.live.value = new Map();
    this.errors.value = []; this.problem.value = null; this.busy.value = false; this.accepted.value = null;
  }
}
