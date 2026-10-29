// Context chips for the next agent run (Plan 5 A6: the parts directory's "Use with agent").
// A chip names a parts-directory item by id. The composer shows the chips; on Start it adds one
// line naming the ids to the task, so the agent reads each item with `catalog_get`, and the chips
// also ride along in the UI context (PUT /context `picks`) that `mises_context_get` returns.
// Only ids go to the agent: an imported item's name is page text (untrusted), so it is shown to
// the user on the chip but never written into the prompt.
import { signal, type Signal } from "@preact/signals";

/** Same as core catalog ID_RE. */
export const CATALOG_ID_RE = /^[a-z][a-z0-9_]{1,63}$/;
export const MAX_CHIPS = 12;

export interface AgentChip { kind: "catalog_item"; id: string; label: string }

export const agentChips: Signal<readonly AgentChip[]> = signal<readonly AgentChip[]>([]);

/** Adds a parts-directory item (once). Returns false for an id the core would never issue. */
export function addCatalogChip(id: string, label: string): boolean {
  if (!CATALOG_ID_RE.test(id)) return false;
  const cur = agentChips.peek();
  if (cur.some((c) => c.id === id)) return true;
  if (cur.length >= MAX_CHIPS) return false;
  agentChips.value = [...cur, { kind: "catalog_item", id, label: label.slice(0, 80) || id }];
  return true;
}

export function removeAgentChip(id: string): void {
  agentChips.value = agentChips.peek().filter((c) => c.id !== id);
}

export function clearAgentChips(): void { agentChips.value = []; }

/** The line appended to a new run's task, or "" without chips. Ids only. */
export function chipsPromptLine(chips: readonly AgentChip[]): string {
  const ids = chips.filter((c) => CATALOG_ID_RE.test(c.id)).map((c) => c.id);
  return ids.length ? `Parts directory items to use (read each with catalog_get): ${ids.join(", ")}` : "";
}

/** The chips as UI-context picks (PUT /context). */
export function chipPicks(chips: readonly AgentChip[]): { kind: "catalog_item"; item_id: string }[] {
  return chips.filter((c) => CATALOG_ID_RE.test(c.id)).map((c) => ({ kind: "catalog_item", item_id: c.id }));
}
