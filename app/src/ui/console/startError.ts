// How starting a run (or messaging the build's lead) failed, in plain words, for the composer
// and the New run dialog. A 429 is the lead's full message queue (U3c lead.QUEUE_MAX): nothing
// was lost, the user waits or stops the lead.
import { CoreError } from "../../api/client";

export function startErrorText(e: unknown): string {
  if (e instanceof CoreError && e.status === 429) {
    return "The lead agent already has a full queue of messages waiting. Wait for it to work through them, or stop it, then send this again — your text is kept.";
  }
  if (e instanceof CoreError) return e.hint ? `${e.message} — ${e.hint}` : e.message;
  return e instanceof Error ? e.message : String(e);
}
