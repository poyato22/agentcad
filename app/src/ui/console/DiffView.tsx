// Diff box under a mirror_part_set_vars call (mockup .call): "diff  Part · mount plate", removed
// values in red and added values in green (add/remove markers only, as in the mockup), then the
// mass / volume delta. All names and values render as text.
import type { JSX } from "preact";
import { fmtMm, fmtSigned } from "../../lib/format";
import { partLabel } from "../../lib/names";
import type { LogItem } from "./logModel";

export function DiffView(props: { item: Extract<LogItem, { kind: "diff" }> }): JSX.Element {
  const { partId, vars, delta } = props.item;
  const lines: JSX.Element[] = [];
  for (const v of vars) {
    if (v.prev !== null && v.prev !== v.now) lines.push(<span class="del text" key={`-${v.name}`}>{`- ${v.name} = ${fmtMm(v.prev)}`}</span>);
    if (v.prev === v.now) lines.push(<span class="dim text" key={`=${v.name}`}>{`  ${v.name} = ${fmtMm(v.now)}`}</span>);
    else lines.push(<span class="add text" key={`+${v.name}`}>{`+ ${v.name} = ${fmtMm(v.now)}`}</span>);
  }
  const parts: string[] = [];
  if (typeof delta.mass_g === "number") parts.push(`mass ${fmtSigned(delta.mass_g, 2, " g")}`);
  if (typeof delta.volume_mm3 === "number") parts.push(`volume ${fmtSigned(delta.volume_mm3, 0, " mm³")}`);
  return (
    <div class="con-box con-diff" data-diff>
      <div class="con-box-hd"><b>diff</b><span class="text">{partId ? partLabel(partId) : "Part"}</span></div>
      <div class="con-box-bd">
        {lines}
        {parts.length > 0 && <span class="dim">{`Δ ${parts.join(" · ")}`}</span>}
      </div>
    </div>
  );
}
