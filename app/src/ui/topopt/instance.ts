// The app's one generative-design controller, wired to the core's event stream, and its entry in
// every part's menu.
import { onCoreEvent } from "../../api/events";
import { actions, parts } from "../../state/store";
import { addPartMenuItem } from "../left/partMenuHooks";
import { TopoptController } from "./state";

export const topopt = new TopoptController(() => actions.client());
onCoreEvent((ev) => topopt.onEvent(ev));

/** Opens the Generative design panel for a part (optionally on one of its runs). The hook the
 *  part context menu and the Studies page call. */
export function openGenerative(partId: string, topoptId: string | null = null): void {
  topopt.open(partId, topoptId);
}

const built = (id: string): boolean => parts.peek().find((p) => p.part_id === id)?.metrics != null;

addPartMenuItem({
  label: "Generative design…", single: true,
  run: ([id]) => { if (id) openGenerative(id); },
  disabled: ([id]) => !id || !built(id),
});
