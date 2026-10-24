import type { JSX } from "preact";
import { partLabel } from "../../lib/names";
import { selectedPartId } from "../../state/store";

export function Breadcrumb(): JSX.Element {
  const id = selectedPartId.value;
  return (
    <nav class="crumb" aria-label="location">
      <span class="crumb-root">◇ LOCAL</span>
      <span class="crumb-sep"> / </span>
      {id ? <span class="crumb-part" data-crumb="part">{partLabel(id)}</span> : <span class="crumb-none" data-crumb="part">no part</span>}
    </nav>
  );
}
