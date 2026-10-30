// A clear card in the thread for each thing an agent did (R16): created a part, started an agent,
// added to the assembly or the parts list, showed a part, imported a drawing, presented a view,
// ran a critique round. The card names what happened and jumps to the referenced item where the
// app can open it (announceJumps). Title and detail are untrusted model/tool text: rendered as text.
import type { JSX } from "preact";
import type { Announcement } from "../../api/types";
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
};
const FALLBACK: Meta = { glyph: "•", label: "Update" };
const firstWord = (s: string) => s.trim().split(/\s+/)[0]?.toLowerCase() ?? "";

export function AnnouncementCard({ announcement }: { announcement: Announcement & { projectId?: string | null; jointId?: string | null } }): JSX.Element {
  const a = announcement;
  const meta = ANNOUNCE_META[a.kind] ?? FALLBACK;
  // m6: "Created a part · Created part arm_link" said it twice — the label is left out when the
  // title already starts with its verb (the glyph still marks the kind)
  const echoes = firstWord(a.title) === firstWord(meta.label);
  const jump = jumpFor({ kind: a.kind, ref: a.ref, projectId: a.projectId ?? null, jointId: a.jointId ?? null });
  return (
    <div class="cv-announce" data-announce={a.kind} data-row data-kind="announce">
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
  );
}
