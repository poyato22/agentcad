// Build overview (R17): a zoomed-out "All parts" grid of the active build, like Onshape's
// overview. Each card shows the part's thumbnail, name, material and mass; clicking one focuses
// it in the viewer and closes the overview. It overlays the viewer and closes on Escape.
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import type { PartSummary } from "../../api/types";
import { fmtGrams } from "../../lib/format";
import { partName } from "../../lib/names";
import { closeOverview } from "../../state/home";
import { actions, parts, selectedPartId } from "../../state/store";
import { Thumb } from "../common/Thumb";
import "./overview.css";

/** Parts that have a built mesh (so a thumbnail and metrics exist); groups and catalog rows don't. */
function meshParts(all: readonly PartSummary[]): PartSummary[] {
  return all.filter((p) => (p.kind ?? "part") === "part" && p.metrics !== null);
}

export function Overview(): JSX.Element {
  const [material, setMaterial] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    // one cheap read for the build's material (parts don't carry their own yet — see the report)
    actions.client().getProfile().then((p) => { if (live) setMaterial(p.default_material); }).catch(() => {});
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.preventDefault(); closeOverview(); } };
    window.addEventListener("keydown", onKey);
    return () => { live = false; window.removeEventListener("keydown", onKey); };
  }, []);

  const shown = meshParts(parts.value);
  return (
    <div class="overview" data-testid="overview" role="dialog" aria-label="All parts">
      <header class="overview-bar">
        <h2 class="overview-title">All parts <span class="dim">· {shown.length}</span></h2>
        <button type="button" class="overview-close" aria-label="Close all-parts view" title="Close (Esc)"
          onClick={() => closeOverview()}>Done</button>
      </header>
      {shown.length === 0
        ? <p class="overview-empty dim">No parts yet. Ask an agent to make one, or import a drawing.</p>
        : <div class="overview-grid" role="list">
            {shown.map((p) => (
              <button key={p.part_id} type="button" class={selectedPartId.value === p.part_id ? "pcard on" : "pcard"}
                role="listitem" data-part={p.part_id} title={`Focus ${partName(p)}`}
                onClick={() => { actions.selectPart(p.part_id); closeOverview(); }}>
                <div class="pcard-thumb">
                  <Thumb class="pcard-img" alt={`${partName(p)} preview`} cacheKey={`${p.part_id}:${p.version ?? 0}:${p.mtime}`}
                    load={() => actions.client().partThumb(p.part_id)} />
                </div>
                <div class="pcard-body">
                  <span class="pcard-name">{partName(p)}</span>
                  <span class="pcard-meta dim">
                    {material ? `${material} · ` : ""}{p.metrics ? fmtGrams(p.metrics.mass_g) : "—"}
                  </span>
                </div>
              </button>
            ))}
          </div>}
    </div>
  );
}
