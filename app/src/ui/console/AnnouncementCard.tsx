// A clear card in the thread for each thing an agent did (R16): created a part, started an agent,
// added to the assembly or the parts list, showed a part, imported a drawing, animated the
// assembly. The card names what happened and carries a "jump to" link to the referenced item when
// the app can open it. Title and detail are untrusted model/tool text and render as text.
import type { JSX } from "preact";
import type { Announcement } from "../../api/types";
import { actions } from "../../state/store";

interface Meta { glyph: string; label: string }

/** What each announcement kind reads as (glyph + a plain label). Unknown kinds fall back. */
const META: Record<string, Meta> = {
  part_created: { glyph: "◆", label: "Created a part" },
  agent_spawned: { glyph: "⑂", label: "Started an agent" },
  assembly_added: { glyph: "⊕", label: "Added to the assembly" },
  bom_added: { glyph: "▤", label: "Added to the parts list" },
  part_shown: { glyph: "◎", label: "Showed a part" },
  drawing_imported: { glyph: "▦", label: "Imported a drawing" },
  assembly_animated: { glyph: "▶", label: "Animated the assembly" },
};
const FALLBACK: Meta = { glyph: "•", label: "Update" };

/** Where the card jumps, when the app can open the referenced item. */
function jumpFor(a: Announcement): { label: string; onClick: () => void } | null {
  const r = a.ref;
  if (a.kind === "agent_spawned" && r.run_id) return { label: "Open agent", onClick: () => actions.selectRun(r.run_id as string) };
  if (r.part_id) return { label: "Show part", onClick: () => actions.selectPart(r.part_id as string) };
  if (r.run_id) return { label: "Open agent", onClick: () => actions.selectRun(r.run_id as string) };
  return null;
}

export function AnnouncementCard({ announcement }: { announcement: Announcement }): JSX.Element {
  const a = announcement;
  const meta = META[a.kind] ?? FALLBACK;
  const jump = jumpFor(a);
  return (
    <div class="cv-announce" data-announce={a.kind} data-row data-kind="announce">
      <span class="cv-announce-glyph" aria-hidden="true">{meta.glyph}</span>
      <div class="cv-announce-body">
        <div class="cv-announce-head">
          <span class="cv-announce-label">{meta.label}</span>
          <span class="cv-announce-title text">{a.title}</span>
        </div>
        {a.detail && <div class="cv-announce-detail faint text">{a.detail}</div>}
      </div>
      {jump && <button type="button" class="cv-announce-jump" onClick={jump.onClick}>{jump.label}</button>}
    </div>
  );
}
