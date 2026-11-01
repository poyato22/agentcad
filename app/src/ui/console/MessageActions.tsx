// P10 E9 / E3: what you can do with a message in the thread. Your own messages get Edit (the words
// back in the composer); an error or a stalled turn gets Retry (the same words again, to the same
// agent); a message still queued for a busy lead gets Cancel (core C1 — hidden for good once the
// core says it has no such route). The error card says what happened, the likely cause and what to
// do, all as plain wrapped text (the message is untrusted and never markup).
import { signal } from "@preact/signals";
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import type { AgentRun } from "../../api/types";
import { activityByRun } from "../../state/activity";
import { actions, steps } from "../../state/store";
import { Button } from "../common/Button";
import { editMessage, isStalled, sendChat, STALL_S } from "./chat";
import { explainError } from "./errorText";
import { startErrorText } from "./startError";

/** The words go back to the composer; sending them marks this row edited. */
export function EditButton({ text, rowKey }: { text: string; rowKey: string }): JSX.Element {
  return (
    <button type="button" class="cv-copy cv-edit" aria-label="Edit and resend" title="Edit and resend" data-edit-message
      onClick={() => editMessage(text, rowKey)}>
      Edit
    </button>
  );
}

/** Send `text` again to `run` (its lead, or that sub-agent). A stalled run is stopped first. */
export async function retry(text: string, run: AgentRun | null, stalled = false): Promise<void> {
  if (stalled && run) {
    try { await actions.client().cancel(run.id); } catch { /* already over: just send */ }
  }
  await sendChat(text, run && run.role !== "lead" ? run : null);
}

export function RetryButton({ text, run, stalled = false, label = "Retry" }: { text: string; run: AgentRun | null; stalled?: boolean; label?: string }): JSX.Element {
  const [state, setState] = useState<{ busy: boolean; error: string | null; sent: boolean }>({ busy: false, error: null, sent: false });
  const go = async () => {
    setState({ busy: true, error: null, sent: false });
    try {
      await retry(text, run, stalled);
      setState({ busy: false, error: null, sent: true });
    } catch (e) {
      setState({ busy: false, error: startErrorText(e), sent: false });
    }
  };
  return (
    <span class="cv-retry">
      <Button variant="default" class="cv-retry-btn" disabled={state.busy} data-retry onClick={() => void go()}
        title={`Send "${text.length > 60 ? `${text.slice(0, 60)}…` : text}" again`}>
        {state.busy ? "Sending…" : label}
      </Button>
      {state.sent && <span class="faint" role="status">sent again</span>}
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

/** An error in the thread: what happened, the likely cause, what to do, and Retry when there is a
 *  message of yours to send again. */
export function ErrorCard({ item, retryText, run }: {
  item: { code: string; message: string; hint: string | null; cause?: string | null };
  retryText: string | null; run: AgentRun | null;
}): JSX.Element {
  const v = explainError(item);
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
      {retryText && <div class="cv-err-actions"><RetryButton text={retryText} run={run} /></div>}
    </div>
  );
}

/** Under a working run's thread: after STALL_S with no sign of life, say so and offer Retry. */
export function StallNote({ run, lastText }: { run: AgentRun; lastText: string | null }): JSX.Element | null {
  const [now, setNow] = useState(() => Date.now());
  const running = run.status === "running";
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(t);
  }, [running]);
  const list = steps.value.get(run.id);
  const lastTs = list && list.length ? list[list.length - 1].ts : null;
  if (!isStalled(run, activityByRun.value.get(run.id) ?? null, lastTs, now)) return null;
  const quiet = Math.floor(now / 1000 - Math.max(run.updated_at ?? 0, lastTs ?? 0, activityByRun.value.get(run.id)?.since ?? 0));
  return (
    <div class="cv-stall" data-stalled role="status">
      <span class="text">{`No sign of the agent for ${quiet >= 120 ? `${Math.floor(quiet / 60)} min` : `${Math.max(quiet, STALL_S)} s`}. It may be stuck.`}</span>
      {lastText && <RetryButton text={lastText} run={run} stalled label="Stop and retry" />}
    </div>
  );
}

/** Tests. */
export function resetMessageActions(): void {
  queuedCancelUnsupported.value = false;
  cancelledQueued.value = new Set();
}
