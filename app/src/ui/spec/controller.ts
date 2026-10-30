// The spec panel's state for the active build: its spec, the latest spec_check of a part, its
// design studies (live while one runs), and the "Use this variant" action. One instance per app
// (module state), shared by the console panel and the Studies page. Events drive refreshes:
// spec_changed reloads the spec, study_progress the studies (throttled), a part change the check.
import { batch, signal } from "@preact/signals";
import type { CoreClient } from "../../api/client";
import { CoreError } from "../../api/client";
import { specChangedSchema, studyProgressSchema, type Spec, type SpecCheck, type Study } from "../../api/spec";
import type { MisesEvent } from "../../api/types";

export const STUDY_REFRESH_MS = 400;
/** spec checks wait this long, so a burst of changes (and both mounted views) check once. */
export const CHECK_DEBOUNCE_MS = 150;

/** What a save did: saved, refused because the spec changed under the edit, or failed. */
export type SaveResult = { ok: true } | { ok: false; conflict: boolean; error: string };

export interface Progress { n: number; total: number }

/** The words shown for a refused or failed call (never the raw code alone). */
export function errorWords(e: unknown): string {
  if (e instanceof CoreError) {
    if (e.code === "stale_version") return `The part changed since this study (${e.message}). Check the part, then choose again.`;
    if (e.code === "network") return "The core is not answering. Try again in a moment.";
    return e.hint ? `${e.message} — ${e.hint}` : e.message;
  }
  return e instanceof Error ? e.message : String(e);
}

export class SpecController {
  readonly projectId = signal<string | null>(null);
  readonly spec = signal<Spec | null>(null);
  /** The spec's `updated_at` (null: never written): the base an edit is saved against. */
  readonly updatedAt = signal<number | null>(null);
  readonly check = signal<SpecCheck | null>(null);
  readonly studies = signal<readonly Study[]>([]);
  /** Live study_progress by study id (ahead of the next GET). */
  readonly progress = signal<ReadonlyMap<string, Progress>>(new Map());
  readonly loadError = signal<string | null>(null);
  readonly checkError = signal<string | null>(null);
  readonly selectedStudyId = signal<string | null>(null);
  /** The previewed iteration n of the shown study. */
  readonly selectedN = signal<number | null>(null);
  readonly applying = signal<{ studyId: string; n: number } | null>(null);
  readonly applied = signal<{ studyId: string; n: number; summary: string } | null>(null);
  readonly applyError = signal<string | null>(null);

  private readonly client: () => CoreClient;
  private studyTimer: ReturnType<typeof setTimeout> | null = null;
  private checkedPart: string | null = null;
  private checkedMtime: number | null = null;
  private checkKey: string | null = null;
  private checkTimer: ReturnType<typeof setTimeout> | null = null;
  private loads = 0;

  constructor(client: () => CoreClient) {
    this.client = client;
  }

  /** Resolve the build (unless given) and load its spec and studies. */
  async load(projectId?: string | null): Promise<void> {
    const seq = ++this.loads;
    try {
      const pid = projectId ?? this.projectId.peek() ?? await this.client().activeProjectId();
      if (seq !== this.loads) return;
      if (pid !== this.projectId.peek()) {
        batch(() => {
          this.projectId.value = pid;
          this.spec.value = null;
          this.updatedAt.value = null;
          this.check.value = null;
          this.studies.value = [];
          this.selectedStudyId.value = null;
          this.selectedN.value = null;
        });
        this.checkedPart = null;
        this.checkKey = null;
      }
      if (!pid) return;
      const [doc, studies] = await Promise.all([this.client().getSpec(pid), this.client().listDesignStudies(pid)]);
      if (seq !== this.loads) return;
      batch(() => {
        this.spec.value = doc.spec;
        this.updatedAt.value = doc.updated_at;
        this.setStudies(studies);
        this.loadError.value = null;
      });
    } catch (e) {
      if (seq === this.loads) this.loadError.value = errorWords(e);
    }
  }

  async refreshStudies(): Promise<void> {
    const pid = this.projectId.peek();
    if (!pid) return;
    try {
      this.setStudies(await this.client().listDesignStudies(pid));
    } catch (e) {
      this.loadError.value = errorWords(e);
    }
  }

  /** Check `partId` once per (build, part, part mtime, spec version), debounced: the console
   *  strip and the Studies page both ask, and a save or a rebuild asks again. */
  requestCheck(partId: string | null, mtime: number | null = null): void {
    this.checkedPart = partId;
    this.checkedMtime = mtime;
    const key = `${this.projectId.peek()}|${partId}|${mtime}|${this.updatedAt.peek()}|${this.spec.peek() ? "s" : "-"}`;
    if (key === this.checkKey) return;
    this.checkKey = key;
    if (this.checkTimer !== null) clearTimeout(this.checkTimer);
    this.checkTimer = setTimeout(() => {
      this.checkTimer = null;
      void this.runCheck(partId);
    }, CHECK_DEBOUNCE_MS);
  }

  /** The part measured against the spec (a plain read). Skipped without a spec or requirements. */
  async runCheck(partId: string | null): Promise<void> {
    const pid = this.projectId.peek();
    const spec = this.spec.peek();
    this.checkedPart = partId;
    if (!pid || !partId || !spec || spec.requirements.length === 0) {
      this.check.value = null;
      return;
    }
    try {
      const c = await this.client().specCheck(pid, partId);
      if (this.checkedPart !== partId) return;
      batch(() => { this.check.value = c; this.checkError.value = null; });
    } catch (e) {
      if (this.checkedPart !== partId) return;
      batch(() => {
        this.check.value = null;
        this.checkError.value = e instanceof CoreError && e.code === "not_found" ? null : errorWords(e);
      });
    }
  }

  /** The user's edit (PUT spec). `base`: the `updated_at` the edit started from; a spec written
   *  since (the agent's spec_set) is refused as a conflict. `undefined` saves regardless. */
  async save(spec: Spec, base?: number | null): Promise<SaveResult> {
    const pid = this.projectId.peek();
    if (!pid) return { ok: false, conflict: false, error: "No build is open." };
    try {
      const doc = await this.client().putSpec(pid, spec, base);
      batch(() => { this.spec.value = doc.spec; this.updatedAt.value = doc.updated_at; });
      this.requestCheck(this.checkedPart, this.checkedMtime);
      return { ok: true };
    } catch (e) {
      if (e instanceof CoreError && e.code === "stale_version") {
        void this.load();
        return { ok: false, conflict: true, error: "The spec changed while you were editing (the agent updated it)." };
      }
      return { ok: false, conflict: false, error: errorWords(e) };
    }
  }

  selectStudy(id: string | null): void {
    if (id === this.selectedStudyId.peek()) return;
    batch(() => {
      this.selectedStudyId.value = id;
      this.selectedN.value = null;
      this.applyError.value = null;
    });
  }

  selectIteration(n: number | null): void {
    batch(() => { this.selectedN.value = n; this.applyError.value = null; });
  }

  /** "Use this variant" (study_apply). Infeasible variants only with `allowInfeasible`. */
  async apply(study: Study, n: number, opts: { allowInfeasible?: boolean; baseVersion?: number } = {}): Promise<boolean> {
    const pid = this.projectId.peek();
    if (!pid || this.applying.peek()) return false;
    batch(() => { this.applying.value = { studyId: study.id, n }; this.applyError.value = null; });
    try {
      const out = await this.client().applyStudy(pid, study.id, {
        iteration: n, ...(opts.allowInfeasible ? { allow_infeasible: true } : {}),
        ...(opts.baseVersion !== undefined ? { base_version: opts.baseVersion } : {}),
      });
      this.applied.value = { studyId: study.id, n, summary: out.summary };
      return true;
    } catch (e) {
      this.applyError.value = errorWords(e);
      return false;
    } finally {
      this.applying.value = null;
    }
  }

  onEvent(ev: MisesEvent): void {
    if (ev.type === "spec_changed") {
      const d = specChangedSchema.safeParse(ev.data);
      if (d.success && d.data.project_id === this.projectId.peek()) void this.load().then(() => this.requestCheck(this.checkedPart, this.checkedMtime));
    } else if (ev.type === "study_progress") {
      const d = studyProgressSchema.safeParse(ev.data);
      if (!d.success) return;
      const m = new Map(this.progress.peek());
      m.set(d.data.study_id, { n: d.data.n, total: d.data.total });
      this.progress.value = m;
      this.scheduleStudies();
    } else if (ev.type === "run_finished") {
      // a study's run ended (done, cancelled, out of budget): its final record is in the store
      if (this.studies.peek().some((s) => s.run_id === ev.run_id && s.status === "running")) this.scheduleStudies();
    }
  }

  /** Tests and page teardown. */
  dispose(): void {
    if (this.studyTimer !== null) clearTimeout(this.studyTimer);
    if (this.checkTimer !== null) clearTimeout(this.checkTimer);
    this.studyTimer = null;
    this.checkTimer = null;
  }

  private scheduleStudies(): void {
    if (this.studyTimer !== null) return;
    this.studyTimer = setTimeout(() => {
      this.studyTimer = null;
      void this.refreshStudies();
    }, STUDY_REFRESH_MS);
  }

  private setStudies(list: readonly Study[]): void {
    const sorted = [...list].sort((a, b) => b.created_at - a.created_at);
    this.studies.value = sorted;
    const sel = this.selectedStudyId.peek();
    if (sel !== null && !sorted.some((s) => s.id === sel)) this.selectedStudyId.value = null;
  }
}
