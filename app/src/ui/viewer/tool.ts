// The viewer's click tool (Plan 7 U2a): Select (a click shows what it hit), Measure (M) or
// Comment (pin feedback for the agent). Feature ops are an explicit "Modify…" (the part's menu,
// or the selection label), never a callout on every click.
import { signal, type Signal } from "@preact/signals";

export type ViewerTool = "select" | "measure" | "comment";

export const viewerTool: Signal<ViewerTool> = signal<ViewerTool>("select");

/** "Modify…" asked for this part (from the part tree's menu); the viewer opens the ops form. */
export const modifyRequest: Signal<{ partId: string; seq: number } | null> = signal<{ partId: string; seq: number } | null>(null);

let seq = 0;
let handled = 0;
export function requestModify(partId: string): void { modifyRequest.value = { partId, seq: ++seq }; }
/** The pending request, once (a viewer mounted later never replays an old one). */
export function takeModify(): { partId: string; seq: number } | null {
  const r = modifyRequest.peek();
  if (!r || r.seq <= handled) return null;
  handled = r.seq;
  return r;
}
