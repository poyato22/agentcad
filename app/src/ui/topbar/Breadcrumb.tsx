import type { JSX } from "preact";
import { partLabel } from "../../lib/names";
import { activePart } from "../../state/selection";
import { selectedPartId } from "../../state/store";

/** The part the viewer and Variables show (activePart); a fallback part is shown plain, not as selected. */
export function Breadcrumb(): JSX.Element {
  const part = activePart.value;
  const chosen = part !== null && selectedPartId.value === part.part_id;
  return (
    <nav class="crumb" aria-label="location">
      <span class="crumb-root">◇ LOCAL</span>
      <span class="crumb-sep"> / </span>
      {part === null
        ? <span class="crumb-none" data-crumb="part">no part</span>
        : <span class={chosen ? "crumb-part" : "crumb-fallback"} data-crumb="part" data-fallback={chosen ? undefined : "true"}>
            {partLabel(part.part_id)}
          </span>}
    </nav>
  );
}
