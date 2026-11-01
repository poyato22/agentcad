// The active build's parts list (BOM), read once for the left surface (its BOM summary and its
// Bought parts group): re-read when the parts change, on `bom_changed` and on a build switch.
import { signal, type Signal } from "@preact/signals";
import { useEffect, useState } from "preact/hooks";
import type { Bom } from "../../api/browser";
import { onCoreEvent } from "../../api/events";
import { activeBuildId } from "../../state/builds";
import { actions, parts } from "../../state/store";

/** The active build's BOM as the core answers it (null: none read yet, or no core). */
export const buildBom: Signal<Bom | null> = signal<Bom | null>(null);

/** Mount once (the left surface): keeps `buildBom` current. */
export function useBuildBomLoader(): void {
  const build = activeBuildId.value;
  const partsNow = parts.value;
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!build) { buildBom.value = null; return; }
    let live = true;
    Promise.resolve().then(() => actions.client().bom(build))
      .then((b) => { if (live) buildBom.value = b; }, () => { if (live) buildBom.value = null; });
    return () => { live = false; };
  }, [build, tick, partsNow]);
  useEffect(() => onCoreEvent((ev) => {
    if (ev.type === "bom_changed" && ev.data.project_id === activeBuildId.peek()) setTick((t) => t + 1);
  }), []);
}
