// Tells the core what the user has selected (PUT /context), so a run's mises_context_get tool and
// the context block of a Claude Code run's first prompt see it. Sent once when a core session
// starts (a restarted core has forgotten it) and after every selection change, debounced.
import { effect } from "@preact/signals";
import type { CoreClient } from "../api/client";
import type { UiContext } from "../api/types";
import { debug } from "../lib/log";
import { agentChips, chipPicks } from "./agentChips";
import { selectedPartId, selectedRunId } from "./store";

export const CONTEXT_DEBOUNCE_MS = 150;

export function startContextSync(client: Pick<CoreClient, "putContext">, debounceMs = CONTEXT_DEBOUNCE_MS): () => void {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let sentKey: string | null = null;
  let stopped = false;
  const dispose = effect(() => {
    // picks: the parts-directory chips waiting for the next run ("Use with agent"), ids only
    const ctx: UiContext = { selected_part_id: selectedPartId.value, selected_run_id: selectedRunId.value, picks: chipPicks(agentChips.value) };
    const key = JSON.stringify(ctx);
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      if (stopped || key === sentKey) return;
      sentKey = key;
      Promise.resolve().then(() => client.putContext(ctx)).catch((e: unknown) => {
        if (sentKey === key) sentKey = null; // send it again with the next change
        debug("PUT /context failed", e);
      });
    }, debounceMs);
  });
  return () => {
    stopped = true;
    dispose();
    if (timer) clearTimeout(timer);
  };
}
