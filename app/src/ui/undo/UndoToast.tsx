// Plan 10 E11: the undo stack's keys (⌘Z / ⇧⌘Z, outside text fields and dialogs) and its toast,
// "Undid: Rename Bracket", at the bottom middle of the window for a few seconds. Mounted once at
// the app's root.
import type { JSX } from "preact";
import { useEffect } from "preact/hooks";
import { installUndoKeys, undoToast } from "../../state/undo";
import "./undo.css";

export function UndoToast(): JSX.Element | null {
  useEffect(() => installUndoKeys(), []);
  const t = undoToast.value;
  if (!t) return null;
  return (
    <div class="undo-toast" role="status" aria-live="polite" data-testid="undo-status" data-error={t.error ? "" : undefined} key={t.at}>
      {t.text}
    </div>
  );
}
