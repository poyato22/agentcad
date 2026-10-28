// Part picker shared by the Drawing and Print pages. It shows and sets the app-wide current part
// (the same one the Design page's part list selects), labelled "Part · mount plate".
import type { JSX } from "preact";
import { partLabel } from "../../../lib/names";
import { activePart } from "../../../state/selection";
import { actions, parts } from "../../../state/store";

export function PartPicker({ disabled }: { disabled?: boolean }): JSX.Element {
  const list = [...parts.value].sort((a, b) => a.part_id.localeCompare(b.part_id));
  const current = activePart.value;
  return (
    <label class="pg-field">
      <span class="label">Part</span>
      <select aria-label="Part" disabled={disabled || list.length === 0} value={current?.part_id ?? ""}
        onChange={(e) => actions.selectPart(e.currentTarget.value || null)}>
        {list.length === 0 && <option value="">no parts</option>}
        {list.map((p) => <option key={p.part_id} value={p.part_id}>{partLabel(p.part_id)}</option>)}
      </select>
    </label>
  );
}
