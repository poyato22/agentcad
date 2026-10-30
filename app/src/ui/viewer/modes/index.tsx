// The Design sheet's view modes (its hook into Viewer.tsx): the strip, bound to designModes.
import type { JSX } from "preact";
import type { ModeHost } from "../scene";
import { designModes } from "./state";
import { ViewModes } from "./ViewModes";

export function DesignViewModes({ host, focusId, partIds }: { host: ModeHost | null; focusId: string | null; partIds: readonly string[] }): JSX.Element {
  return <ViewModes host={host} modes={designModes.value} onModes={(m) => { designModes.value = m; }} focusId={focusId} partIds={partIds} />;
}
