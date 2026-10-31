import type { JSX } from "preact";
import type { PartSummary } from "../../api/types";
import { fmtSize } from "../../lib/format";
import { partMassText } from "../../lib/mass";
import { partName } from "../../lib/names";

export interface TitleBlockProps {
  part: PartSummary | null;
  /** Run that last changed the part (e.g. "designer · stiffen upright"); null → "you". */
  runLabel: string | null;
  /** Shown in red when the model failed to load. */
  error?: string | null;
}

const DASH = "—";

/** The open part's facts, a small floating card bottom-right of the view. */
export function TitleBlock({ part, runLabel, error = null }: TitleBlockProps): JSX.Element {
  const m = part?.metrics ?? null;
  // [cell id, label (sentence case, D20), value]
  const cells: [string, string, string][] = [
    ["PART", "Part", part ? `Part · ${partName(part)}` : DASH],
    ["SIZE mm", "Size, mm", m ? fmtSize(m.bbox.size) : DASH],
    ["MASS", "Mass", part ? partMassText(part, 2) : DASH],
    ["BY", "By", part ? (runLabel ?? "you") : DASH],
  ];
  return (
    <div class="title-block" data-testid="title-block">
      <div class="tb-cells">
        {cells.map(([k, label, v]) => (
          <div class="tb-cell" data-cell={k} key={k}>
            <span class="label">{label}</span>
            <span class={`tb-value${k === "SIZE mm" || k === "MASS" ? " num" : ""}`} title={v}>{v}</span>
          </div>
        ))}
      </div>
      {error && <div class="tb-error" role="alert">{`GLB failed: ${error}`}</div>}
    </div>
  );
}
