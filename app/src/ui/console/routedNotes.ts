// POST /runs answers a routed prompt with `notes` (core lead._notes): what the build's lead did
// not take from the request, e.g. "model_ref ignored: the build's lead runs on claude-code:opus"
// or "scope ignored: ...". They are shown, as text, in the top bar's toast so the user sees why
// their model or scope choice had no effect.
import type { CreateRunResult } from "../../api/types";
import { showToast } from "../topbar/stopAll";

export function routedNotesText(r: Pick<CreateRunResult, "notes">): string | null {
  const notes = (r.notes ?? []).map((n) => n.trim()).filter(Boolean);
  return notes.length ? `Sent to the build's lead · ${notes.join(" · ")}` : null;
}

export function showRoutedNotes(r: Pick<CreateRunResult, "notes">): void {
  const t = routedNotesText(r);
  if (t) showToast(t, 8000);
}
