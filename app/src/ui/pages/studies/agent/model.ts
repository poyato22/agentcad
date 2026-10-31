// The Studies page's agent pane (Plan 9 U-D, D19): the studies agent's build context and proposed
// next studies (GET /studies/context), and its conversation. The studies agent is the core's
// builtin `simulation` agent, one run per build, started outside the lead's tree (POST /runs with
// `agent: "simulation"`): the newest such run of the build is "the" studies agent; a message goes
// to it, or starts it when there is none (or it can no longer take messages). Proposals reload
// when the agent saves new ones (`studies_proposed`) and whenever a run of the build finishes
// (a study ended, so the rules' proposals change).
import { signal, type Signal } from "@preact/signals";
import { CoreError, type CoreClient } from "../../../../api/client";
import type { StudiesContext, StudyProposal } from "../../../../api/studiesAgent";
import type { AgentRun, MisesEvent } from "../../../../api/types";

export const STUDIES_AGENT = "simulation";

export type StudiesAgentClient = Pick<CoreClient, "studiesContext" | "createRun" | "message">;

/** The first message when the user asks for a plan without typing one. */
export const PLAN_PROMPT = "Look at this build and propose the next studies to run, with why.";

/** The build's studies agent: its newest non-archived `simulation` run. */
export function studiesAgentRun(runs: Iterable<AgentRun>, projectId: string | null): AgentRun | null {
  if (!projectId) return null;
  let best: AgentRun | null = null;
  for (const r of runs) {
    if (r.agent !== STUDIES_AGENT || r.archived || r.parent_id !== null) continue;
    if ((r.project_id ?? null) !== projectId) continue;
    if (!best || r.created_at > best.created_at) best = r;
  }
  return best;
}

export const isBusy = (r: AgentRun | null): boolean => r !== null && (r.status === "running" || r.status === "queued");

const TYPE_WORD: Record<string, string> = { static: "Stress", modal: "Modes", buckling: "Buckling", thermal: "Thermal", drop: "Drop", wind: "Wind" };
export const typeWord = (t: string): string => TYPE_WORD[t] ?? t;

const RAN_KEY = "mises.studies.ran.";

function readRan(projectId: string): { id: string; partId: string; at: number } | null {
  try {
    const v = JSON.parse(localStorage.getItem(RAN_KEY + projectId) ?? "null") as unknown;
    if (v && typeof v === "object") {
      const o = v as Record<string, unknown>;
      if (typeof o.id === "string" && typeof o.partId === "string" && typeof o.at === "number") return { id: o.id, partId: o.partId, at: o.at };
    }
  } catch { /* none */ }
  return null;
}

/** A message error after which a new studies agent may replace the old run. */
export function canReplace(e: unknown): boolean {
  if (!(e instanceof CoreError)) return false;
  if (/still working/i.test(e.message) || e.status === 409 || e.status === 429) return false;
  return e.code === "not_found" || /external|cannot take messages|not running in this process/i.test(e.message);
}

const messageOf = (e: unknown): string => e instanceof CoreError
  ? (e.hint ? `${e.message} (${e.hint})` : e.message)
  : e instanceof Error ? e.message : String(e);

export class StudiesAgentController {
  readonly context: Signal<StudiesContext | null> = signal(null);
  readonly contextError: Signal<string | null> = signal(null);
  readonly loading: Signal<boolean> = signal(false);
  readonly sendError: Signal<string | null> = signal(null);
  readonly sending: Signal<boolean> = signal(false);
  /** The proposal whose Run was pressed last (its card shows the study that started). */
  readonly ranProposal: Signal<{ id: string; partId: string; at: number } | null> = signal(null);

  private readonly client: () => StudiesAgentClient;
  private projectId: string | null = null;
  private seq = 0;
  private reloadTimer: ReturnType<typeof setTimeout> | null = null;
  /** A proposal whose Run was pressed while another study ran: it starts when that one ends. */
  readonly queued: Signal<StudyProposal | null> = signal(null);
  /** The studies agent a send just started, per build, until the runs list has it (M3). */
  private readonly started = new Map<string, string>();

  constructor(client: () => StudiesAgentClient) { this.client = client; }

  /** Loads the build's context; a stale answer for another build is dropped. */
  async load(projectId: string | null): Promise<void> {
    const seq = ++this.seq;
    if (projectId !== this.projectId) { this.context.value = null; this.contextError.value = null; this.ranProposal.value = projectId ? readRan(projectId) : null; }
    this.projectId = projectId;
    if (!projectId) return;
    this.loading.value = true;
    try {
      const ctx = await this.client().studiesContext(projectId);
      if (seq !== this.seq) return;
      this.context.value = ctx;
      this.contextError.value = null;
    } catch (e) {
      if (seq === this.seq) this.contextError.value = messageOf(e);
    } finally {
      if (seq === this.seq) this.loading.value = false;
    }
  }

  /** Sends to the build's studies agent, or starts it. Returns the run id, or null on failure. */
  async send(text: string, run: AgentRun | null): Promise<string | null> {
    const t = text.trim();
    if (!t || !this.projectId || this.sending.peek()) return null;
    this.sending.value = true;
    this.sendError.value = null;
    try {
      if (run && !isBusy(run)) {
        try {
          await this.client().message(run.id, t);
          return run.id;
        } catch (e) {
          // only a run this core can no longer continue (gone after a restart, or one that never
          // takes messages) is replaced by a fresh studies agent; "still working" and anything
          // else is said, never a second studies agent for the build (M3)
          if (!canReplace(e)) throw e;
        }
      } else if (run) {
        throw new Error("The studies agent is still working. Wait for it to finish, or stop it.");
      } else if (this.started.has(this.projectId)) {
        throw new Error("The studies agent is starting. Send again once it answers.");
      }
      const { run_id } = await this.client().createRun({ agent: STUDIES_AGENT, prompt: t, project_id: this.projectId });
      this.started.set(this.projectId, run_id);
      return run_id;
    } catch (e) {
      this.sendError.value = messageOf(e);
      return null;
    } finally {
      this.sending.value = false;
    }
  }

  /** The runs list now has the run a send started (or a newer one): sends go to it. */
  noteRun(run: AgentRun | null): void {
    if (run && this.projectId && this.started.has(this.projectId)) this.started.delete(this.projectId);
  }

  /** Remembered per build (localStorage), so the card and "Run again" survive a reload. */
  noteRan(p: StudyProposal): void {
    const ran = { id: p.id, partId: p.part_id, at: Date.now() / 1000 };
    this.ranProposal.value = ran;
    if (this.projectId) { try { localStorage.setItem(RAN_KEY + this.projectId, JSON.stringify(ran)); } catch { /* per session only */ } }
  }

  onEvent(ev: MisesEvent, runs: ReadonlyMap<string, AgentRun>): void {
    const pid = this.projectId;
    if (!pid) return;
    if (ev.type === "studies_proposed" && ev.data.project_id === pid) { this.reloadSoon(0); return; }
    if (ev.type === "run_finished" && ev.run_id) {
      const r = runs.get(ev.run_id);
      if (!r || (r.project_id ?? pid) === pid) this.reloadSoon(250);
    }
  }

  dispose(): void { if (this.reloadTimer) clearTimeout(this.reloadTimer); this.reloadTimer = null; }

  private reloadSoon(ms: number): void {
    if (this.reloadTimer) clearTimeout(this.reloadTimer);
    this.reloadTimer = setTimeout(() => { this.reloadTimer = null; void this.load(this.projectId); }, ms);
  }
}
