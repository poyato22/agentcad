import type { JSX } from "preact";
import { fmtGrams } from "../../lib/format";
import { partLabel } from "../../lib/names";
import { actions, parts } from "../../state/store";
import { activePart, changingParts } from "./derived";

export function PartList(): JSX.Element {
  const list = parts.value;
  const sel = activePart.value?.part_id ?? null;
  const changing = changingParts.value;
  if (list.length === 0) return <div class="pane-empty">no parts yet</div>;
  return (
    <ul class="left-list">
      {list.map((p) => {
        const busy = changing.has(p.part_id);
        const cls = ["left-row", p.part_id === sel ? "sel" : "", busy ? "changing" : ""].filter(Boolean).join(" ");
        return (
          <li key={p.part_id}>
            <button type="button" class={cls} aria-pressed={p.part_id === sel} onClick={() => actions.selectPart(p.part_id)}>
              <span class="row-name" data-label>{partLabel(p.part_id)}</span>
              {busy && <span class="tag-changing">changing</span>}
              <span class="row-right faint">{p.metrics ? fmtGrams(p.metrics.mass_g, 1) : ""}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
