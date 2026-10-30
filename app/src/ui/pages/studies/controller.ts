// Studies page state: study types and their settings schemas, solver status, materials and
// templates, the part's study history, the shown study (record + charts), compare, and the study
// being run. A study runs in the core as a "studies" run (POST /sims {type, part_id, settings} →
// 202); the page follows it by its `sim_progress {sim_id, phase, pct, detail}` events and by
// polling GET /sims?run_id=, and stops it with POST /sims/{sim_id}/cancel (or the run's cancel
// before the study exists). One controller per app (module level, see index.tsx): a study keeps
// running when the user leaves the page; polling runs only while the page is mounted.
import { computed, signal, type ReadonlySignal, type Signal } from "@preact/signals";
import { CoreError, type CoreClient } from "../../../api/client";
import {
  studyProgressSchema, type CustomMaterialBody, type StudyCharts, type StudyCompare, type StudyMaterials, type StudyRecord,
  type StudySolvers, type StudySummary, type StudyTemplate, type StudyType, type StudyTypeInfo,
} from "../../../api/studies";
import type { Calibration } from "../../../api/accuracy";
import type { MisesEvent } from "../../../api/types";
import { runBuild } from "../../../state/builds";
import { askPrompt } from "./model";
import { validate, type FieldError } from "./formSchema";

export type StudiesClient = Pick<CoreClient,
  | "studyTypes" | "studySolvers" | "listStudies" | "getStudy" | "startStudy" | "cancelStudy" | "studyCharts" | "compareStudies"
  | "studyTemplates" | "addStudyTemplate" | "deleteStudyTemplate" | "studyMaterials" | "addStudyMaterial" | "deleteStudyMaterial"
  | "cancel" | "getRun" | "steps" | "createRun"> & Partial<Pick<CoreClient, "calibrations">>;

export interface ActiveStudy {
  runId: string; partId: string; type: StudyType; startedAt: number;
  /** null until the core has stored the study record. */
  simId: string | null; phase: string | null; pct: number | null; detail: string | null; cancelling: boolean;
}

export interface Problem { message: string; hint: string | null }

export const POLL_MS = 600;
const MAX_POLL_FAILURES = 20;

const messageOf = (e: unknown): Problem => e instanceof CoreError
  ? { message: e.message, hint: e.hint }
  : { message: e instanceof Error ? e.message : String(e), hint: null };

export class StudiesController {
  readonly types: Signal<readonly StudyTypeInfo[]> = signal([]);
  readonly typesError: Signal<string | null> = signal(null);
  readonly solvers: Signal<StudySolvers | null> = signal(null);
  readonly solversError: Signal<string | null> = signal(null);
  readonly ccxMissing: ReadonlySignal<boolean> = computed(() => this.solvers.value?.ccx.found === false);
  readonly materials: Signal<StudyMaterials | null> = signal(null);
  readonly templates: Signal<readonly StudyTemplate[]> = signal([]);
  /** Saved material calibrations (S3): which material + printer pairs studies run calibrated. */
  readonly calibrations: Signal<readonly Calibration[]> = signal([]);
  readonly libraryError: Signal<string | null> = signal(null);
  readonly historyPart: Signal<string | null> = signal(null);
  readonly history: Signal<readonly StudySummary[]> = signal([]);
  readonly historyError: Signal<string | null> = signal(null);
  readonly selectedSimId: Signal<string | null> = signal(null);
  readonly record: Signal<StudyRecord | null> = signal(null);
  readonly charts: Signal<StudyCharts | null> = signal(null);
  readonly recordError: Signal<string | null> = signal(null);
  readonly active: Signal<ActiveStudy | null> = signal(null);
  readonly runError: Signal<Problem | null> = signal(null);
  /** Field problems of the last Run press (the form shows them at their fields). */
  readonly fieldErrors: Signal<readonly FieldError[]> = signal([]);
  readonly compareIds: Signal<readonly string[]> = signal([]);
  readonly compare: Signal<StudyCompare | null> = signal(null);
  readonly compareError: Signal<string | null> = signal(null);
  readonly asked: Signal<{ simId: string; runId: string | null; error: string | null } | null> = signal(null);
  /** The study this page ran just finished and is shown (`n` counts, so each finish is news). */
  readonly finished: Signal<{ simId: string; n: number } | null> = signal(null);

  private readonly client: () => StudiesClient;
  private readonly pollMs: number;
  private selectSeq = 0;
  private historySeq = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private disposed = false;
  private paused = false;
  private failures = 0;

  constructor(client: () => StudiesClient, opts: { pollMs?: number } = {}) {
    this.client = client;
    this.pollMs = opts.pollMs ?? POLL_MS;
  }

  typeInfo(t: StudyType): StudyTypeInfo | undefined { return this.types.peek().find((x) => x.id === t); }

  async loadTypes(): Promise<void> {
    try { this.types.value = await this.client().studyTypes(); this.typesError.value = null; } catch (e) { this.typesError.value = messageOf(e).message; }
  }

  async loadSolvers(): Promise<void> {
    try { this.solvers.value = await this.client().studySolvers(); this.solversError.value = null; } catch (e) { this.solversError.value = messageOf(e).message; }
  }

  async loadLibrary(): Promise<void> {
    try {
      const [m, t] = await Promise.all([this.client().studyMaterials(), this.client().studyTemplates()]);
      this.materials.value = m;
      this.templates.value = t;
      this.libraryError.value = null;
    } catch (e) { this.libraryError.value = messageOf(e).message; }
  }

  /** A core without the calibration routes (or a failed read) leaves the list empty: every material reads "datasheet / estimate". */
  async loadCalibrations(): Promise<void> {
    const cl = this.client();
    if (!cl.calibrations) return;
    try { this.calibrations.value = await cl.calibrations(); } catch { this.calibrations.value = []; }
  }

  async saveTemplate(name: string, type: StudyType, settings: Record<string, unknown>): Promise<Problem | null> {
    try {
      const t = await this.client().addStudyTemplate({ name, type, settings });
      this.templates.value = [...this.templates.peek(), t];
      return null;
    } catch (e) { return messageOf(e); }
  }

  async deleteTemplate(id: string): Promise<Problem | null> {
    try { await this.client().deleteStudyTemplate(id); this.templates.value = this.templates.peek().filter((t) => t.id !== id); return null; } catch (e) { return messageOf(e); }
  }

  async saveMaterial(m: CustomMaterialBody): Promise<Problem | null> {
    try {
      const saved = await this.client().addStudyMaterial(m);
      const cur = this.materials.peek() ?? { builtin: [], custom: [] };
      this.materials.value = { ...cur, custom: [...cur.custom.filter((c) => c.name.toLowerCase() !== saved.name.toLowerCase()), saved] };
      return null;
    } catch (e) { return messageOf(e); }
  }

  async deleteMaterial(name: string): Promise<Problem | null> {
    try {
      await this.client().deleteStudyMaterial(name);
      const cur = this.materials.peek();
      if (cur) this.materials.value = { ...cur, custom: cur.custom.filter((c) => c.name !== name) };
      return null;
    } catch (e) { return messageOf(e); }
  }

  /** Loads the part's studies; shows the newest finished one unless one of this part is shown. */
  async loadHistory(partId: string): Promise<void> {
    const seq = ++this.historySeq;
    if (this.historyPart.peek() !== partId) {
      this.historyPart.value = partId;
      this.history.value = [];
      this.selectedSimId.value = null;
      this.record.value = null;
      this.charts.value = null;
      this.asked.value = null;
      this.compareIds.value = [];
      this.compare.value = null;
    }
    let list: StudySummary[];
    try { list = await this.client().listStudies({ partId }); } catch (e) {
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
      const rec = this.record.peek();
      if (rec && rec.sim_id === shown.sim_id && (rec.stale !== shown.stale || rec.status !== shown.status)) void this.select(shown.sim_id);
    }
    const running = list.find((s) => s.status === "running" && s.run_id);
    if (running?.run_id && this.active.peek() === null) {
      this.active.value = {
        runId: running.run_id, partId, type: (running.type ?? "static") as StudyType, startedAt: running.created_at,
        simId: running.sim_id, phase: running.phase ?? null, pct: running.pct ?? null, detail: null, cancelling: false,
      };
      this.schedule(running.run_id);
    }
  }

  /** m9: the study type changed in the setup — the result shown follows it: this part's newest
   *  finished study of that type, else none (never another type's result under this setup). */
  showType(type: string): void {
    if (this.record.peek()?.type === type) return;
    const latest = [...this.history.peek()].filter((s) => s.type === type && s.status === "done").sort((a, b) => b.created_at - a.created_at)[0];
    if (latest) { void this.select(latest.sim_id); return; }
    this.selectSeq++;
    this.selectedSimId.value = null;
    this.record.value = null;
    this.charts.value = null;
  }

  async select(simId: string): Promise<void> {
    const seq = ++this.selectSeq;
    this.selectedSimId.value = simId;
    if (this.record.peek()?.sim_id !== simId) { this.record.value = null; this.charts.value = null; }
    this.recordError.value = null;
    try {
      const rec = await this.client().getStudy(simId);
      if (seq !== this.selectSeq) return;
      this.record.value = rec;
      if (rec.status === "done") {
        try {
          const ch = await this.client().studyCharts(simId);
          if (seq === this.selectSeq) this.charts.value = ch;
        } catch { if (seq === this.selectSeq) this.charts.value = null; }
      }
    } catch (e) {
      if (seq === this.selectSeq) this.recordError.value = messageOf(e).message;
    }
  }

  /** Field problems the core would refuse, from the type's schema. */
  problems(type: StudyType, settings: Record<string, unknown>): FieldError[] {
    const info = this.typeInfo(type);
    return info ? validate(settings, info.schema, info.schema) : [];
  }

  async start(partId: string, type: StudyType, settings: Record<string, unknown>): Promise<void> {
    if (this.active.peek()) return; // one study at a time from the page (the core runs one at a time)
    const errs = this.problems(type, settings);
    this.fieldErrors.value = errs;
    this.runError.value = errs.length ? { message: `${errs.length} setting${errs.length === 1 ? "" : "s"} to fix: ${errs.slice(0, 3).map((e) => `${e.path || "settings"} ${e.message}`).join("; ")}`, hint: null } : null;
    if (errs.length) return;
    let runId: string;
    try {
      ({ run_id: runId } = await this.client().startStudy({ type, part_id: partId, settings }));
    } catch (e) {
      this.runError.value = messageOf(e);
      return;
    }
    this.active.value = { runId, partId, type, startedAt: Date.now() / 1000, simId: null, phase: "queued", pct: 0, detail: null, cancelling: false };
    this.schedule(runId);
  }

  async cancel(): Promise<void> {
    const a = this.active.peek();
    if (!a || a.cancelling) return;
    this.active.value = { ...a, cancelling: true };
    try {
      if (a.simId) await this.client().cancelStudy(a.simId);
      else await this.client().cancel(a.runId);
    } catch (e) {
      // not running any more (it just finished), or the study is not stored yet: cancel the run
      try { await this.client().cancel(a.runId); } catch {
        const cur = this.active.peek();
        if (cur?.runId === a.runId) this.active.value = { ...cur, cancelling: false };
        this.runError.value = messageOf(e);
      }
    }
    this.schedule(a.runId, 0);
  }

  toggleCompare(simId: string): void {
    const cur = this.compareIds.peek();
    this.compareIds.value = cur.includes(simId) ? cur.filter((x) => x !== simId) : [...cur, simId].slice(-8);
    if (this.compareIds.peek().length < 2) this.compare.value = null;
  }

  async runCompare(): Promise<void> {
    const ids = this.compareIds.peek();
    if (ids.length < 2) return;
    try { this.compare.value = await this.client().compareStudies(ids); this.compareError.value = null; } catch (e) { this.compareError.value = messageOf(e).message; }
  }

  /** "Ask agent to fix": a designer run, allowed to edit only this part, with the full settings and result as data. */
  async ask(rec: StudyRecord): Promise<string | null> {
    const prev = this.asked.peek();
    if (prev && prev.simId === rec.sim_id && prev.error === null) return prev.runId;
    this.asked.value = { simId: rec.sim_id, runId: null, error: null };
    try {
      const { run_id } = await this.client().createRun({ agent: "designer", prompt: askPrompt(rec), edit_scope: [rec.part_id], ...runBuild() });
      this.asked.value = { simId: rec.sim_id, runId: run_id, error: null };
      return run_id;
    } catch (e) {
      const p = messageOf(e);
      this.asked.value = { simId: rec.sim_id, runId: null, error: p.hint ? `${p.message} (${p.hint})` : p.message };
      return null;
    }
  }

  onEvent(ev: MisesEvent): void {
    if (ev.type !== "sim_progress" || !ev.run_id) return;
    const a = this.active.peek();
    if (!a || a.runId !== ev.run_id) return;
    const p = studyProgressSchema.safeParse(ev.data);
    if (!p.success || p.data.part_id !== a.partId) return;
    this.active.value = { ...a, simId: p.data.sim_id, phase: p.data.phase ?? a.phase, pct: p.data.pct ?? a.pct, detail: p.data.detail ?? a.detail };
    if (["done", "failed", "cancelled"].includes(p.data.stage)) this.schedule(a.runId, 0);
  }

  pause(): void {
    this.paused = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

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

  private schedule(runId: string, ms = this.pollMs): void {
    if (this.disposed || this.paused) return;
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => { this.timer = null; void this.poll(runId); }, ms);
  }

  private async poll(runId: string): Promise<void> {
    const a = this.active.peek();
    if (!a || a.runId !== runId || this.disposed || this.paused) return;
    try {
      const [s] = await this.client().listStudies({ runId });
      const cur = this.active.peek();
      if (!cur || cur.runId !== runId) return;
      this.failures = 0;
      if (s && s.status === "running") {
        if (cur.simId !== s.sim_id || cur.phase !== (s.phase ?? cur.phase)) {
          this.active.value = { ...cur, simId: s.sim_id, phase: s.phase ?? cur.phase, pct: s.pct ?? cur.pct };
        }
        this.schedule(runId);
        return;
      }
      if (s) { await this.finish(cur, s); return; }
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
      // network blips: back off (the poll interval doubling, at most 8 s)
      this.schedule(runId, Math.min(8000, this.pollMs * 2 ** Math.min(this.failures, 4)));
    }
  }

  private async finish(a: ActiveStudy, s: StudySummary): Promise<void> {
    this.active.value = null;
    if (s.status === "failed" && /ccx|calculix/i.test(`${s.error ?? ""} ${s.hint ?? ""}`)) void this.loadSolvers();
    if (this.historyPart.peek() === a.partId) {
      this.selectedSimId.value = s.sim_id;
      await this.loadHistory(a.partId);
      await this.select(s.sim_id);
      // the page's viewer switches from the setup to this result (B1-2)
      if (this.selectedSimId.peek() === s.sim_id) this.finished.value = { simId: s.sim_id, n: (this.finished.peek()?.n ?? 0) + 1 };
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
