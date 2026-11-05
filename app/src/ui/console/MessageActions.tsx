// P10 E9 / E3: what you can do with a message in the thread. Your own messages get Edit (the words
// back in the composer); an error the run did not get past gets Continue (the core carries the run
// on with an internal continuation: never your message again, never as your words); a message
// still queued for a busy lead gets Cancel (core C1 — hidden for good once the core says it has
// no such route). The error card says what happened, the likely cause and what to
// do, all as plain wrapped text (the message is untrusted and never markup).
import { signal } from "@preact/signals";
import type { JSX } from "preact";
import { useState } from "preact/hooks";
import type { AgentRun } from "../../api/types";
import { actions } from "../../state/store";
import { Button } from "../common/Button";
import { editMessage } from "./chat";
import { explainError } from "./errorText";
import { openProfile } from "../profile/open";
import { startErrorText } from "./startError";

/** The words go back to the composer; sending them marks this row edited. */
export function EditButton({ text, rowKey }: { text: string; rowKey: string }): JSX.Element {
  return (
    <button type="button" class="cv-copy cv-edit" aria-label="Edit message" title="Put these words back in the composer to change them" data-edit-message
      onClick={() => editMessage(text, rowKey)}>
      Edit
    </button>
  );
}

/** Continue: the core carries `run` on from where it stopped with an internal continuation
 *  (shown as "AgentCAD continued after: <cause>"). Never re-sends your message. */
export function ContinueButton({ run, cause }: { run: AgentRun | null; cause: string | null }): JSX.Element | null {
  const [state, setState] = useState<{ busy: boolean; error: string | null; sent: boolean }>({ busy: false, error: null, sent: false });
  if (!run) return null;
  const go = async () => {
    setState({ busy: true, error: null, sent: false });
    try {
      await actions.client().continueRun(run.id, cause);
      setState({ busy: false, error: null, sent: true });
    } catch (e) {
      setState({ busy: false, error: startErrorText(e), sent: false });
    }
  };
  return (
    <span class="cv-retry">
      <Button variant="default" class="cv-retry-btn" disabled={state.busy || state.sent} data-continue onClick={() => void go()}
        title="AgentCAD carries the run on from where it stopped">
        {state.busy ? "Continuing…" : "Continue"}
      </Button>
      {state.sent && <span class="faint" role="status">continuing</span>}
      {state.error && <span class="cv-fail text" role="alert">{state.error}</span>}
    </span>
  );
}

/** A core with no cancel-a-queued-message route: Cancel is not offered again. */
export const queuedCancelUnsupported = signal(false);
/** Messages taken back (until the core's "dropped" settles the row). */
export const cancelledQueued = signal<ReadonlySet<string>>(new Set());

export function CancelQueuedButton({ runId, messageId }: { runId: string; messageId: string }): JSX.Element | null {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (queuedCancelUnsupported.value) return null;
  const go = async () => {
    setBusy(true);
    setError(null);
    try {
      const ok = await actions.client().cancelQueued(runId, messageId);
      if (!ok) { queuedCancelUnsupported.value = true; return; }
      cancelledQueued.value = new Set([...cancelledQueued.peek(), messageId]);
    } catch (e) {
      setError(startErrorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <button type="button" class="cv-copy cv-cancel-q" data-cancel-queued={messageId} disabled={busy}
        aria-label="Cancel this queued message" onClick={() => void go()}>
        {busy ? "Cancelling…" : "Cancel"}
      </button>
      {error && <span class="cv-fail text" role="alert">{error}</span>}
    </>
  );
}

/** An error in the thread: what happened, the likely cause, what to do, and Continue when the run
 *  did not get past it (`stopped`). */
export function ErrorCard({ item, stopped, run }: {
  item: { code: string; message: string; hint: string | null; cause?: string | null; recovery?: { tried: number } | null };
  stopped: boolean; run: AgentRun | null;
}): JSX.Element {
  const v = explainError(item);
  // P11 F9: the run recovered by itself until its budget ran out: say what was tried, and offer
  // Continue (Mises carries it on; your message is never sent again)
  if (item.recovery) {
    const n = item.recovery.tried;
    return (
      <div class="cv-err" data-error data-error-kind="recovery" data-recovery-exhausted data-row data-kind="error" role="group" aria-label="The run stopped">
        <div class="cv-err-head">
          <span class="cv-fail cv-err-glyph" aria-hidden="true">✕</span>
          <span class="cv-err-title">The run stopped</span>
          <span class="cv-err-code faint" title="error code">{v.code}</span>
        </div>
        <p class="cv-err-msg text" data-error-message>{item.message}</p>
        <p class="cv-err-line text"><span class="cv-err-l">Tried</span>{`restarting the session ${n} time${n === 1 ? "" : "s"}`}</p>
        <div class="cv-err-actions">
          <ContinueButton run={run} cause={item.message || null} />
        </div>
      </div>
    );
  }
  return (
    <div class="cv-err" data-error data-error-kind={v.kind} data-row data-kind="error" role="group" aria-label={v.title}>
      <div class="cv-err-head">
        <span class="cv-fail cv-err-glyph" aria-hidden="true">✕</span>
        <span class="cv-err-title">{v.title}</span>
        <span class="cv-err-code faint" title="error code">{v.code}</span>
      </div>
      {v.message && <p class="cv-err-msg text" data-error-message>{v.message}</p>}
      {v.cause && <p class="cv-err-line text"><span class="cv-err-l">Likely cause</span>{v.cause}</p>}
      {v.hint && <p class="cv-err-line text" data-error-hint><span class="cv-err-l">What to do</span>{v.hint}</p>}
      {((stopped && run) || v.kind === "tls" || v.kind === "network") && (
        <div class="cv-err-actions">
          {stopped && <ContinueButton run={run} cause={item.message || null} />}
          {/* the CA bundle / proxy live in Profile & settings → Claude Code → Network */}
          {(v.kind === "tls" || v.kind === "network") && (
            <Button variant="ghost" data-network-settings onClick={() => openProfile("claude")}>Network settings</Button>
          )}
        </div>
      )}
    </div>
  );
}

/** Tests. */
export function resetMessageActions(): void {
  queuedCancelUnsupported.value = false;
  cancelledQueued.value = new Set();
}
