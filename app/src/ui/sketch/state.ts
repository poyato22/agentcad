// The concept stage on the app side: each build's concept view (GET /projects/{id}/concept),
// refreshed when the core says it changed (concept_sketch / concept_pitch / concept_updated /
// concept_feedback events), the draft marks per sketch, and the open review (full window).
import { signal, type Signal } from "@preact/signals";
import type { ChooseBody, ConceptView } from "../../api/concept";
import { onCoreEvent } from "../../api/events";
import type { MisesEvent } from "../../api/types";
import { activeBuildId } from "../../state/builds";
import { actions } from "../../state/store";
import { canSend, feedbackBody, type DraftMark } from "./marks";

export const concepts: Signal<Record<string, ConceptView>> = signal({});
const loading = new Map<string, Promise<ConceptView | null>>();

/** Fetch (or refetch) a build's concept view. Failures leave the last good view in place. */
export function loadConcept(projectId: string): Promise<ConceptView | null> {
  const running = loading.get(projectId);
  if (running) return running;
  const p = (async () => {
    try {
      const v = await actions.client().concept(projectId);
      const before = concepts.value[projectId];
      // a new version (or a new pitch) answers the last action: its notice is done
      if (before && (v.sketches.length > before.sketches.length || v.pitches.length > before.pitches.length)) {
        const { [projectId]: _done, ...rest } = notices.value;
        notices.value = rest;
      }
      concepts.value = { ...concepts.value, [projectId]: v };
      return v;
    } catch { return null; } finally { loading.delete(projectId); }
  })();
  loading.set(projectId, p);
  return p;
}

export const CONCEPT_EVENTS: ReadonlySet<string> = new Set(["concept_sketch", "concept_pitch", "concept_updated", "concept_feedback"]);

/** The build a concept event is about (events carry project_id; a record event has it nested). */
export function eventProject(ev: MisesEvent): string | null {
  if (!CONCEPT_EVENTS.has(ev.type)) return null;
  const d = ev.data;
  const pid = typeof d.project_id === "string" ? d.project_id : null;
  return pid && /^[A-Za-z0-9_-]{1,64}$/.test(pid) ? pid : null;
}

onCoreEvent((ev) => {
  const pid = eventProject(ev);
  if (pid && (pid in concepts.value || pid === activeBuildId.value)) void loadConcept(pid);
});

// ---- drafts (per sketch version)

export interface Draft { marks: DraftMark[]; comment: string }
export const drafts: Signal<Record<string, Draft>> = signal({});
export const EMPTY: Draft = { marks: [], comment: "" };

export function draftOf(sketchId: string): Draft { return drafts.value[sketchId] ?? EMPTY; }
export function setDraft(sketchId: string, d: Draft): void { drafts.value = { ...drafts.value, [sketchId]: d }; }

// ---- the review (full window)

export interface Review { projectId: string; sketchId: string; image: number }
export const review: Signal<Review | null> = signal(null);
export function openReview(r: Review): void { review.value = r; }
export function closeReview(): void { review.value = null; }

// ---- actions (each refetches the view; the core's event refetches too)

/** What the last action said, per build: shown under the card (sent, approved, an error). */
export const notices: Signal<Record<string, { text: string; error: boolean }>> = signal({});
function notice(projectId: string, text: string, error = false): void {
  notices.value = { ...notices.value, [projectId]: { text, error } };
}
const why = (e: unknown): string => (e instanceof Error ? e.message : String(e));

export async function sendFeedback(projectId: string, sketchId: string): Promise<boolean> {
  const d = draftOf(sketchId);
  if (!canSend(d.marks, d.comment)) return false;
  try {
    const out = await actions.client().sketchFeedback(sketchId, feedbackBody(d.marks, d.comment));
    setDraft(sketchId, EMPTY);
    notice(projectId, out.queued ? `${out.text} queued for the agent` : `${out.text} sent to the agent`);
    void loadConcept(projectId);
    return true;
  } catch (e) { notice(projectId, `Could not send the feedback: ${why(e)}`, true); return false; }
}

export async function approve(projectId: string, sketchId: string | null): Promise<boolean> {
  try {
    await actions.client().approveConcept(projectId, sketchId);
    notice(projectId, "Approved. The agent starts building.");
    void loadConcept(projectId);
    return true;
  } catch (e) { notice(projectId, `Could not approve: ${why(e)}`, true); return false; }
}

export async function skip(projectId: string): Promise<boolean> {
  try {
    await actions.client().skipConcept(projectId);
    notice(projectId, "Sketch skipped. The agent starts building.");
    void loadConcept(projectId);
    return true;
  } catch (e) { notice(projectId, `Could not skip: ${why(e)}`, true); return false; }
}

export async function choose(projectId: string, pitchId: string, body: ChooseBody): Promise<boolean> {
  try {
    const out = await actions.client().choosePitch(pitchId, body);
    notice(projectId, `Chose ${out.chosen.label}. The agent sketches it next.`);
    void loadConcept(projectId);
    return true;
  } catch (e) { notice(projectId, `Could not send the choice: ${why(e)}`, true); return false; }
}

/** Tests: forget every build's view, draft and notice. */
export function resetConceptState(): void {
  concepts.value = {}; drafts.value = {}; notices.value = {}; review.value = null; loading.clear();
}
