import type { JSX } from "preact";
import { isAutoNumbered, partLabel } from "../../lib/names";
import { selectedPartId } from "../../state/store";

/** "bracket_v2" → "Part · bracket": trailing numbers are dropped, never shown (design §5.1). */
function crumbLabel(partId: string): string | null {
  let id = partId;
  while (id && isAutoNumbered(partLabel(id))) {
    const next = id.replace(/[\s_-]*v?\d+\s*$/i, "");
    if (next === id) return null;
    id = next;
  }
  const label = id ? partLabel(id) : "";
  return label && !isAutoNumbered(label) ? label : null;
}

export function Breadcrumb(): JSX.Element {
  const id = selectedPartId.value;
  const label = id ? crumbLabel(id) : null;
  return (
    <nav class="crumb" aria-label="location">
      <span class="crumb-root">◇ LOCAL</span>
      <span class="crumb-sep"> / </span>
      {label ? <span class="crumb-part" data-crumb="part">{label}</span> : <span class="crumb-none" data-crumb="part">no part</span>}
    </nav>
  );
}
