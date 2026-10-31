// A clear card in the thread for each thing an agent did (R16): created a part, started an agent,
// added to the assembly or the parts list, showed a part, imported a drawing, presented a view,
// ran a critique round. The card names what happened and jumps to the referenced item where the
// app can open it (announceJumps). A created part shows its render, large (Plan 9 D10 / D13), from
// the part's thumbnail route. Title and detail are untrusted model/tool text: rendered as text.
import type { JSX } from "preact";
import type { Announcement } from "../../api/types";
import { actions, parts } from "../../state/store";
import { Thumb } from "../common/Thumb";
import { conceptCardFor } from "../sketch/register";
import { jumpFor } from "./announceJumps";

interface Meta { glyph: string; label: string }

/** What each announcement kind reads as (glyph + a plain label). Unknown kinds fall back. */
export const ANNOUNCE_META: Readonly<Record<string, Meta>> = {
  part_created: { glyph: "◆", label: "Created a part" },
  agent_spawned: { glyph: "⑂", label: "Started an agent" },
  assembly_added: { glyph: "⊕", label: "Added to the assembly" },
  bom_added: { glyph: "▤", label: "Added to the parts list" },
  part_shown: { glyph: "◎", label: "Showed a part" },
  drawing_imported: { glyph: "▦", label: "Imported a drawing" },
  view_presented: { glyph: "◫", label: "Presented a view" },
  critique_round: { glyph: "✎", label: "Reviewed the geometry" },
  concept_sketch: { glyph: "✐", label: "Sketched the concept" },
  concept_pitch: { glyph: "◇", label: "Pitched directions" },
};
const FALLBACK: Meta = { glyph: "•", label: "Update" };
const firstWord = (s: string) => s.trim().split(/\s+/)[0]?.toLowerCase() ?? "";
/** The render size asked of the core for a created part's card (2× the dock's width). */
export const PART_RENDER_PX = 768;

export function AnnouncementCard({ announcement }: { announcement: Announcement & { projectId?: string | null; jointId?: string | null } }): JSX.Element {
  const a = announcement;
  // Plan 9 G-A: a concept sketch / pitch is a full card of its own (the View stream's, streams.ts)
  const concept = conceptCardFor(a);
  if (concept) return concept;
  const meta = ANNOUNCE_META[a.kind] ?? FALLBACK;
  // m6: "Created a part · Created part arm_link" said it twice — the label is left out when the
  // title already starts with its verb (the glyph still marks the kind)
  const echoes = firstWord(a.title) === firstWord(meta.label);
  const jump = jumpFor({ kind: a.kind, ref: a.ref, projectId: a.projectId ?? null, jointId: a.jointId ?? null });
  const partId = a.kind === "part_created" ? a.ref.part_id ?? null : null;
  return (
    <div class={`cv-announce${partId ? " has-image" : ""}`} data-announce={a.kind} data-row data-kind="announce">
      {partId && <PartRender partId={partId} title={a.title} onOpen={jump?.go} />}
      <div class="cv-announce-row">
        <span class="cv-announce-glyph" aria-hidden="true">{meta.glyph}</span>
        <div class="cv-announce-body">
          <div class="cv-announce-head">
            {echoes ? <span class="sr-only">{meta.label}</span> : <span class="cv-announce-label">{meta.label}</span>}
            <span class="cv-announce-title text">{a.title}</span>
          </div>
          {a.detail && <div class="cv-announce-detail faint text">{a.detail}</div>}
        </div>
        {jump && <button type="button" class="cv-announce-jump" onClick={jump.go}>{jump.label}</button>}
      </div>
    </div>
  );
}

/** The created part's render, the card's full width; re-fetched when the part changes. */
function PartRender({ partId, title, onOpen }: { partId: string; title: string; onOpen?: () => void }): JSX.Element {
  const version = parts.value.find((p) => p.part_id === partId)?.version ?? 0;
  const img = (
    <Thumb class="cv-announce-img" alt={`Render of ${title}`} cacheKey={`${partId}:${version}`} glyph="◆"
      load={() => { try { return actions.client().partThumb(partId, PART_RENDER_PX); } catch (e) { return Promise.reject(e); } }} />
  );
  return onOpen
    ? <button type="button" class="cv-announce-shot" aria-label={`Show ${title} in the assembly`} data-part-render={partId} onClick={onOpen}>{img}</button>
    : <div class="cv-announce-shot" data-part-render={partId}>{img}</div>;
}
