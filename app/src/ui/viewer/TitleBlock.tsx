import type { JSX } from "preact";
import type { PartSummary } from "../../api/types";
import { fmtGrams, fmtSize } from "../../lib/format";
import { partLabel } from "../../lib/names";

export interface TitleBlockProps {
  part: PartSummary | null;
  /** Run that last changed the part (e.g. "designer · stiffen upright"); null → "you". */
  runLabel: string | null;
  /** Shown in red when the model failed to load. */
  error?: string | null;
}

const DASH = "—";

/** Engineering-drawing title block, bottom-right of the sheet. */
export function TitleBlock({ part, runLabel, error = null }: TitleBlockProps): JSX.Element {
  const m = part?.metrics ?? null;
  const cells: [string, string][] = [
    ["PART", part ? partLabel(part.part_id) : DASH],
    ["SIZE mm", m ? fmtSize(m.bbox.size) : DASH],
    ["MASS", m ? fmtGrams(m.mass_g) : DASH],
    ["BY", part ? (runLabel ?? "you") : DASH],
  ];
  return (
    <div class="title-block" data-testid="title-block">
      <div class="tb-cells">
        {cells.map(([k, v]) => (
          <div class="tb-cell" data-cell={k} key={k}>
            <span class="label">{k}</span>
            <span class="tb-value" title={v}>{v}</span>
          </div>
        ))}
      </div>
      {error && <div class="tb-error" role="alert">{`GLB failed: ${error}`}</div>}
    </div>
  );
}
