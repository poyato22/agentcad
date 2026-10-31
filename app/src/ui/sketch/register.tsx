// The concept stage's one hook into the agent dock: the card for a `concept_sketch` or
// `concept_pitch` announcement (core concept/tools.py announce cards, View stream). The dock asks
// `conceptCardFor(announcement)` before its generic card; null means "not ours".
// Plan 9 U-B's View stream wires this in when it lands; until then the thread's AnnouncementCard
// asks it. The full-window review mounts once at the app root (SketchReviewRoot).
import type { JSX } from "preact";
import { PitchCards } from "./PitchCards";
import { SketchCard } from "./SketchCard";

export const CONCEPT_KINDS: ReadonlySet<string> = new Set(["concept_sketch", "concept_pitch"]);
/** The stream a concept card belongs to (U-B's three-stream dock). */
export const CONCEPT_STREAM = "view" as const;

export interface ConceptAnnouncement {
  kind: string;
  ref: { sketch_id?: string | null; pitch_id?: string | null };
  projectId?: string | null;
}

export function conceptCardFor(a: ConceptAnnouncement): JSX.Element | null {
  if (a.kind === "concept_sketch") return <div class="cv-card sk-slot" data-row data-kind="concept_sketch"><SketchCard projectId={a.projectId ?? null} sketchId={a.ref.sketch_id ?? null} /></div>;
  if (a.kind === "concept_pitch") return <div class="cv-card sk-slot" data-row data-kind="concept_pitch"><PitchCards projectId={a.projectId ?? null} pitchId={a.ref.pitch_id ?? null} /></div>;
  return null;
}

export { SketchReviewRoot } from "./SketchReview";
export { SketchCard } from "./SketchCard";
export { PitchCards } from "./PitchCards";
