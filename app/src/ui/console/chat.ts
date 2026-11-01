// P10 E24 / E9: one chat per build. The composer always talks to the build's lead: no run picker,
// no "New run". The first message of a build with no lead creates it on the default model —
// through the core's POST /projects/{id}/chat (C1), or on an older core POST /runs with the
// default model the app picks (the profile's when set, else Claude Code when signed in). Viewing a
// sub-agent's thread (from its card) is read-only until the user asks to "Message this agent".
// Also here: Edit (your words back in the composer; the old message is marked edited once the new
// one is sent), Retry (the same words again) and the stall rule (90 s with no sign of life).
import { computed, signal, type ReadonlySignal, type Signal } from "@preact/signals";
import { CoreError, type ChatImageIn } from "../../api/client";
import type { AgentActivity, AgentRun, CreateRunResult } from "../../api/types";
import { agentChips, chipsPromptLine, clearAgentChips } from "../../state/agentChips";
import { activeBuildId } from "../../state/builds";
import { actions, runs, selectedRunId } from "../../state/store";
import { catalog, refreshCatalog } from "../dialogs/catalog";
import { buildModelGroups, newRunModel } from "../dialogs/modelGroups";
import { followLead, NEW_LEAD_NOTE } from "./leadFollow";
import { noteRun } from "./queued";
import { showRoutedNotes } from "./routedNotes";

/** The build's live lead (newest, not archived), or null before its first message. */
export function leadOf(all: Iterable<AgentRun>, buildId: string | null): AgentRun | null {
  if (!buildId) return null;
  return [...all].filter((r) => r.project_id === buildId && r.role === "lead" && !r.archived)
    .sort((a, b) => b.created_at - a.created_at)[0] ?? null;
}

export const buildLead: ReadonlySignal<AgentRun | null> = computed(() => leadOf(runs.value.values(), activeBuildId.value));

/** A sub-agent the user chose to message from its thread ("Message this agent"); null: the lead. */
export const messageTarget: Signal<string | null> = signal<string | null>(null);

/** Text for the composer to take (Edit), with the thread row it edits. `n` makes each request new. */
export interface Draft { text: string; editOf: string | null; n: number }
export const composerDraft: Signal<Draft | null> = signal<Draft | null>(null);
let drafts = 0;
export function editMessage(text: string, key: string): void { composerDraft.value = { text, editOf: key, n: ++drafts }; }

const EDITED_KEY = "mises.chat.edited";
function readEdited(): ReadonlySet<string> {
  try {
    const raw = JSON.parse(globalThis.localStorage?.getItem(EDITED_KEY) ?? "[]") as unknown;
    return new Set(Array.isArray(raw) ? raw.filter((k): k is string => typeof k === "string").slice(-500) : []);
  } catch { return new Set(); }
}
/** Thread rows (run-scoped keys) whose words were edited and sent again. */
export const editedKeys: Signal<ReadonlySet<string>> = signal<ReadonlySet<string>>(readEdited());
export function markEdited(key: string): void {
  const next = new Set(editedKeys.peek());
  next.add(key);
  editedKeys.value = next;
  try { globalThis.localStorage?.setItem(EDITED_KEY, JSON.stringify([...next].slice(-500))); } catch { /* not remembered */ }
}

/** The model a build's first lead starts on when the core can't choose (an older core): the
 *  profile's default when the user set one (an explicit choice wins), else Claude Code when it is
 *  signed in (sonnet, else its "best", else any usable alias), else the best usable model. */
export function defaultChatModel(cat: typeof catalog.value): string | null {
  if (!cat.models) return null;
  if (cat.profileDefault) return cat.profileDefault;
  const groups = buildModelGroups(cat.models, { local: cat.local, profile: { default_model: cat.profileDefault } });
  const claude = (groups.find((g) => g.id === "claude")?.choices ?? []).filter((c) => c.usable);
  const pick = claude.find((c) => c.ref === "claude-code/sonnet") ?? claude.find((c) => c.recommended === "best") ?? claude[0];
  return pick?.ref ?? newRunModel(cat);
}

async function firstLeadModel(): Promise<string | null> {
  // fresh: the profile default or the sign-in may have changed since the catalog loaded
  await refreshCatalog();
  return defaultChatModel(catalog.peek());
}

export interface Sent { runId: string; note: string | null; res: CreateRunResult | null }

/** Send `text` to the build's lead (or to `target`, a sub-agent the user chose). "Use with
 *  agent" chips ride along, and `images` (P11 F7: the composer's attachments). Throws the core's
 *  error (the composer shows it, the text and the images are kept). */
export async function sendChat(text: string, target: AgentRun | null = null, images: readonly ChatImageIn[] = []): Promise<Sent> {
  const chipLine = chipsPromptLine(agentChips.peek());
  const body = chipLine ? `${text}\n\n${chipLine}` : text;
  const c = actions.client();
  let fellBack = false;
  if (target && target.role !== "lead") {
    try {
      await (images.length ? c.message(target.id, body, images) : c.message(target.id, body));
      if (chipLine) clearAgentChips();
      return { runId: target.id, note: null, res: null };
    } catch (e) {
      // a run of an earlier core process is gone: the build's lead takes the message instead (I2)
      if (!(e instanceof CoreError && e.code === "not_found" && /unknown run/i.test(e.message) && target.project_id)) throw e;
      messageTarget.value = null;
      fellBack = true;
    }
  }
  const buildId = target?.project_id ?? activeBuildId.peek();
  if (!buildId) throw new CoreError("invalid_input", "Open a build first", { hint: "the chat belongs to a build" });
  const lead = leadOf(runs.peek().values(), buildId);
  let res = await (images.length ? c.chat(buildId, body, images) : c.chat(buildId, body));
  if (res === null) {
    // an older core: POST /runs routes to the lead, or starts one (on the default model we pick)
    const model = lead ? null : await firstLeadModel();
    res = await c.createRun({ prompt: body, project_id: buildId, ...(model ? { model_ref: model } : {}) });
  }
  showRoutedNotes(res);
  if (chipLine) clearAgentChips();
  // the lead the message was meant for (an archived one too: then a new lead took over)
  const prior = lead ?? [...runs.peek().values()].filter((r) => r.project_id === buildId && r.role === "lead")
    .sort((a, b) => b.created_at - a.created_at)[0] ?? null;
  let note = prior ? followLead(prior, res) : null;   // a new lead took over: NEW_LEAD_NOTE
  if (selectedRunId.peek() !== res.run_id) {
    // the thread your words went to shows (the build's first lead, or the lead from a sub-agent's
    // read-only thread)
    actions.selectRun(res.run_id);
    noteRun(res.run_id);
    actions.setFollow(true);
  }
  if (fellBack && !note) note = res.new_lead ? NEW_LEAD_NOTE : "that run ended with the earlier Mises session · sent to the build's lead";
  return { runId: res.run_id, note, res };
}

/** P10 E9: with no sign of life for this long, a working run counts as stalled (Retry shows). */
export const STALL_S = 90;

/** A running run with no new step or activity for STALL_S. A run waiting for you (a pending
 *  approval or question: `waiting`), or running a tool (its line counts the seconds, and a study
 *  may take minutes), is not stalled. The quiet time never starts before `seenRunningS`, when
 *  this window first saw the run running (an old run's stale timestamps don't count). */
export function isStalled(run: Pick<AgentRun, "status" | "updated_at">, activity: AgentActivity | null, lastStepTs: number | null, nowMs: number = Date.now(),
  opts: { waiting?: boolean; seenRunningS?: number | null } = {}): boolean {
  if (run.status !== "running" || opts.waiting) return false;
  if (activity && (activity.state === "tool" || activity.state === "waiting_approval" || activity.state === "waiting_user")) return false;
  const last = quietSince(run, activity, lastStepTs, opts.seenRunningS ?? null);
  return last > 0 && nowMs / 1000 - last >= STALL_S;
}

/** When the run last showed life (s): its last step or activity, never before this window saw it running. */
export function quietSince(run: Pick<AgentRun, "updated_at">, activity: AgentActivity | null, lastStepTs: number | null, seenRunningS: number | null): number {
  return Math.max(run.updated_at ?? 0, lastStepTs ?? 0, activity?.since ?? 0, seenRunningS ?? 0);
}

const seenRunning = new Map<string, number>();
/** When this window first saw `runId` running (s); `running` false forgets it. */
export function seenRunningAt(runId: string, running: boolean, nowMs: number = Date.now()): number | null {
  if (!running) { seenRunning.delete(runId); return null; }
  let t = seenRunning.get(runId);
  if (t === undefined) { t = nowMs / 1000; seenRunning.set(runId, t); }
  return t;
}

/** Tests. */
export function resetChat(): void {
  seenRunning.clear();
  messageTarget.value = null;
  composerDraft.value = null;
  editedKeys.value = new Set();
}
