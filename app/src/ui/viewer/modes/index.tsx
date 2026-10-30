// The Design sheet's view modes (its hook into Viewer.tsx): the strip, bound to designModes.
import type { JSX } from "preact";
import { useEffect, useRef } from "preact/hooks";
import type { ModeHost } from "../scene";
import { designModes } from "./state";
import { ViewModes } from "./ViewModes";

export function DesignViewModes({ host, focusId, partIds }: { host: ModeHost | null; focusId: string | null; partIds: readonly string[] }): JSX.Element {
  // another part (or the build view): a plane placed for the last one goes back to the middle
  const shown = partIds.join("|");
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    const m = designModes.peek();
    if (m.section.at !== null) designModes.value = { ...m, section: { ...m.section, at: null } };
  }, [focusId, shown]);
  return <ViewModes host={host} modes={designModes.value} onModes={(m) => { designModes.value = m; }} focusId={focusId} partIds={partIds} />;
}
