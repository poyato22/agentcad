// Plan 11 F8: the effort a sent message's turn ran at, as a quiet chip on that message
// ("Thinking: Max · open-ended: an egg-drop challenge"); the tooltip lists every reason and what
// the runtime was sent (e.g. claude-code --effort max).
import type { JSX } from "preact";
import { effortChipText, effortChipTitle, type EffortDecision } from "../../../api/think";
import "./think.css";

export function EffortChip({ effort }: { effort: EffortDecision }): JSX.Element {
  return (
    <span class="cv-effort faint" data-testid="effort-chip" data-effort={effort.level} data-effort-source={effort.source} title={effortChipTitle(effort)}>
      {effortChipText(effort)}
    </span>
  );
}
