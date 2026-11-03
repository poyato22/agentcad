// Plan 13 H4: the Studies page's "Whole assembly" target. Which study fits the build (GET
// /assembly_studies/kinds: the studies agent's rule, the recommended one first), the build's
// assembly studies, the shown one (checks, metrics, charts, playback, part stresses), and the one
// being run: POST /assembly_studies → 202 {run_id}; followed by `assembly_study_progress` events
// (by run id) and by polling GET /assembly_studies?project_id= until its record ends. Cancel: POST
// /jobs/{study_id}/cancel (B3), or the run's cancel before the study exists. One per app (module
// level): a study keeps running when the user leaves the page.
import { signal, type Signal } from "@preact/signals";
import {
  assemblyProgressSchema, type AssemblyKind, type AssemblyStudyKind, type AssemblyStudyRecord, type AssemblyStudySummary,
} from "../../../../api/assemblyStudies";
import { CoreError, type CoreClient } from "../../../../api/client";
import type { MisesEvent } from "../../../../api/types";
import type { AnimateStep } from "../../../../api/types";
import { presentOnAssembly } from "../../../assembly/state";

export type AssemblyStudiesClient = Pick<CoreClient,
  "assemblyStudyKinds" | "listAssemblyStudies" | "getAssemblyStudy" | "startAssemblyStudy" | "assemblyStudyFem" | "cancelJob" | "cancel">;

export interface ActiveAssemblyStudy {
  runId: string; projectId: string; kind: AssemblyStudyKind; startedAt: number;
  studyId: string | null; phase: string | null; pct: number | null; detail: string | null; cancelling: boolean;
}

const POLL_MS = 700;
const msg = (e: unknown): string => (e instanceof CoreError ? (e.hint ? `${e.message} (${e.hint})` : e.message) : e instanceof Error ? e.message : String(e));
const TERMINAL = new Set(["done", "failed", "cancelled", "interrupted"]);

export class AssemblyStudiesController {
  readonly projectId: Signal<string | null> = signal(null);
  readonly kinds: Signal<readonly AssemblyKind[]> = signal([]);
  readonly kindsError: Signal<string | null> = signal(null);
  readonly history: Signal<readonly AssemblyStudySummary[]> = signal([]);
  readonly record: Signal<AssemblyStudyRecord | null> = signal(null);
  readonly recordError: Signal<string | null> = signal(null);
  readonly active: Signal<ActiveAssemblyStudy | null> = signal(null);
  readonly runError: Signal<string | null> = signal(null);
  readonly femError: Signal<string | null> = signal(null);

  private readonly client: () => AssemblyStudiesClient;
  private readonly pollMs: number;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private femTimer: ReturnType<typeof setTimeout> | null = null;
  private seq = 0;

  constructor(client: () => AssemblyStudiesClient, opts: { pollMs?: number } = {}) {
    this.client = client;
    this.pollMs = opts.pollMs ?? POLL_MS;
  }

  /** The build's kinds and studies; shows the newest finished one unless one of this build is shown. */
  async load(projectId: string | null): Promise<void> {
    const seq = ++this.seq;
    if (projectId !== this.projectId.peek()) {
      this.projectId.value = projectId;
      this.kinds.value = []; this.history.value = []; this.record.value = null; this.recordError.value = null;
    }
    if (!projectId) return;
    try {
      const [kinds, history] = await Promise.all([this.client().assemblyStudyKinds(projectId), this.client().listAssemblyStudies(projectId)]);
      if (seq !== this.seq) return;
      this.kinds.value = kinds; this.kindsError.value = null; this.history.value = history;
      const rec = this.record.peek();
      if (!rec || rec.project_id !== projectId) {
        const pick = history.find((h) => h.status === "done");
        if (pick) void this.select(pick.study_id);
      }
      const running = history.find((h) => h.status === "running" && h.run_id);
      if (running?.run_id && !this.active.peek()) {
        this.active.value = { runId: running.run_id, projectId, kind: running.kind, startedAt: running.created_at, studyId: running.study_id, phase: running.phase ?? null, pct: running.pct ?? null, detail: null, cancelling: false };
        this.schedule();
      }
    } catch (e) {
      if (seq === this.seq) this.kindsError.value = msg(e);
    }
  }

  async select(studyId: string): Promise<void> {
    this.recordError.value = null;
    try { this.record.value = await this.client().getAssemblyStudy(studyId); } catch (e) { this.recordError.value = msg(e); }
  }

  async start(projectId: string, kind: AssemblyStudyKind, settings: Record<string, unknown>): Promise<void> {
    if (this.active.peek()) return;
    this.runError.value = null;
    let runId: string;
    try { ({ run_id: runId } = await this.client().startAssemblyStudy({ project_id: projectId, kind, settings })); } catch (e) { this.runError.value = msg(e); return; }
    this.active.value = { runId, projectId, kind, startedAt: Date.now() / 1000, studyId: null, phase: "queued", pct: 0, detail: null, cancelling: false };
    this.schedule();
  }

  async cancel(): Promise<void> {
    const a = this.active.peek();
    if (!a || a.cancelling) return;
    this.active.value = { ...a, cancelling: true };
    try {
      if (a.studyId) await this.client().cancelJob(a.studyId);
      else await this.client().cancel(a.runId);
    } catch {
      try { await this.client().cancel(a.runId); } catch (e) { this.runError.value = msg(e); }
    }
    this.schedule(0);
  }

  /** "Check part stresses": the shown study's peak loads go to per-part FEM (its own run). */
  async checkStresses(rec: AssemblyStudyRecord): Promise<void> {
    this.femError.value = null;
    try {
      await this.client().assemblyStudyFem(rec.study_id);
      const cur = this.record.peek();
      if (cur?.study_id === rec.study_id) this.record.value = { ...cur, fem: { ...(cur.fem ?? { available: true, rows: [] }), status: "running" } };
      this.followFem(rec.study_id);
    } catch (e) { this.femError.value = msg(e); }
  }

  /** Watch: the study's motion in the assembly's full window (Section / X-ray on its bar). */
  play(rec: AssemblyStudyRecord): boolean {
    const steps = (rec.playback?.steps ?? []) as AnimateStep[];
    if (steps.length === 0) return false;
    presentOnAssembly({ mode: "animation", steps, loop: rec.playback?.loop ?? false, title: `${rec.name} study`, caption: rec.playback?.caption ?? "" });
    return true;
  }

  onEvent(ev: MisesEvent): void {
    if (ev.type !== "assembly_study_progress") return;
    const p = assemblyProgressSchema.safeParse(ev.data);
    if (!p.success) return;
    const a = this.active.peek();
    if (a && ev.run_id === a.runId) {
      this.active.value = { ...a, studyId: p.data.study_id, phase: p.data.phase, pct: p.data.pct ?? a.pct, detail: p.data.detail ?? a.detail };
      if (TERMINAL.has(p.data.phase)) this.schedule(0);
    }
  }

  dispose(): void {
    if (this.timer) clearTimeout(this.timer);
    if (this.femTimer) clearTimeout(this.femTimer);
    this.timer = this.femTimer = null;
  }

  private schedule(ms = this.pollMs): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => { this.timer = null; void this.poll(); }, ms);
  }

  private async poll(): Promise<void> {
    const a = this.active.peek();
    if (!a) return;
    let rows: AssemblyStudySummary[];
    try { rows = await this.client().listAssemblyStudies(a.projectId); } catch { this.schedule(this.pollMs * 3); return; }
    const cur = this.active.peek();
    if (!cur || cur.runId !== a.runId) return;
    const mine = rows.find((r) => r.run_id === a.runId);
    if (this.projectId.peek() === a.projectId) this.history.value = rows;
    if (!mine || mine.status === "running") {
      if (mine && (mine.phase !== cur.phase || mine.study_id !== cur.studyId)) this.active.value = { ...cur, studyId: mine.study_id, phase: mine.phase ?? cur.phase, pct: mine.pct ?? cur.pct };
      // the run ended before any study record (refused by the Gate): stop after ~60 polls
      if (!mine && Date.now() / 1000 - a.startedAt > 60) { this.active.value = null; this.runError.value = "the study did not start"; return; }
      this.schedule();
      return;
    }
    this.active.value = null;
    if (mine.status === "failed") this.runError.value = `${mine.name} study failed: ${mine.error ?? mine.summary ?? ""}${mine.hint ? ` (${mine.hint})` : ""}`;
    if (this.projectId.peek() === a.projectId) await this.select(mine.study_id);
  }

  private followFem(studyId: string): void {
    if (this.femTimer) clearTimeout(this.femTimer);
    this.femTimer = setTimeout(() => {
      this.femTimer = null;
      void this.client().getAssemblyStudy(studyId).then((r) => {
        if (this.record.peek()?.study_id === studyId) this.record.value = r;
        if (r.fem?.status === "running") this.followFem(studyId);
      }, () => this.followFem(studyId));
    }, this.pollMs * 3);
  }
}
