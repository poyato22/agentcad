// Interview card: an agent's batched question (ui_ask_user with `questions`, the engineer's
// requirements interview). Each question has option chips (one, several, or a ranking) and a
// free-text box; one "Send answers" posts them as a JSON object {id: value} through the existing
// /questions/{id}/answer route (core ui_tools._batch_answers). Text renders as text only. Like the
// question card, sending needs a trusted click at least ARM_MS after the card appeared.
import type { JSX } from "preact";
import { useRef, useState } from "preact/hooks";
import type { AskItem } from "../../api/spec";
import type { Question } from "../../api/types";
import { actions } from "../../state/store";
import { Button } from "../common/Button";
import { clickArmed, isTrustedActivation } from "../console/approvals/approvalKeys";
import { answerOf, buildAnswers, emptyDraft, move, pick, setText, unanswered, type InterviewDraft } from "./model";
import "./spec.css";

/** The intro line: the core appends every prompt to `question` for older UIs; we list them below. */
export function introOf(q: Question): string {
  const text = q.question;
  const listed = text.search(/\n1\. /);
  return (listed >= 0 ? text.slice(0, listed) : text).trim();
}

export function InterviewCard(props: { question: Question; runLabel: string; send?: (id: string, text: string) => Promise<unknown> }): JSX.Element {
  const q = props.question;
  const items = q.questions ?? [];
  const [draft, setDraft] = useState<InterviewDraft>(() => emptyDraft(items));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const shownKey = `${q.question_id}\n${JSON.stringify(items)}`;
  const arm = useRef({ key: shownKey, at: performance.now() });
  if (arm.current.key !== shownKey) arm.current = { key: shownKey, at: performance.now() };

  const open = unanswered(items, draft);
  const done = items.length - open.length;

  const onSend = (e: MouseEvent) => {
    if (!isTrustedActivation(e) || !clickArmed(e, arm.current.at, performance.now())) return;
    void send();
  };

  const send = async () => {
    if (busy || open.length > 0) return;
    setBusy(true);
    setError(null);
    try {
      const text = JSON.stringify(buildAnswers(items, draft));
      await (props.send ?? ((id, t) => actions.client().answer(id, t)))(q.question_id, text);
      actions.removeQuestion(q.question_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-card data-question={q.question_id} data-interview class="qcard spq" role="group" aria-label={`Questions from ${props.runLabel}`}>
      <div class="qcard-head">
        <span class="qcard-flag">? QUESTIONS</span>
        <span class="faint text acard-run">{props.runLabel}</span>
        <span class="spq-count faint" aria-live="polite">{`${done}/${items.length} answered`}</span>
      </div>
      {introOf(q) && <div class="qcard-question text">{introOf(q)}</div>}
      <ol class="spq-list">
        {items.map((item, i) => (
          <li key={item.id} class="spq-item" data-ask={item.id} data-answered={answerOf(item, draft[item.id]) !== null ? "" : undefined}>
            <span class="spq-n faint" aria-hidden="true">{i + 1}</span>
            <div class="spq-body">
              <div class="spq-prompt text" id={`spq-${q.question_id}-${item.id}`}>{item.prompt}</div>
              {item.rank
                ? <RankList item={item} order={draft[item.id]?.order ?? []} disabled={busy} onMove={(o, d) => setDraft((x) => move(x, item, o, d))} />
                : item.options.length > 0 && (
                  <div class="spq-chips" role="group" aria-labelledby={`spq-${q.question_id}-${item.id}`}>
                    {item.options.map((o) => {
                      const on = (draft[item.id]?.picks ?? []).includes(o);
                      return (
                        <button key={o} type="button" class="spq-chip" aria-pressed={on} disabled={busy} onClick={() => setDraft((x) => pick(x, item, o))}>
                          <span class="text">{o}</span>
                        </button>
                      );
                    })}
                  </div>
                )}
              {item.allow_text && (
                <input type="text" class="spq-text" disabled={busy} value={draft[item.id]?.text ?? ""}
                  aria-label={`Your own answer: ${item.prompt}`} placeholder={item.rank ? "or describe the order" : item.multi ? "and/or add your own" : "or type your own"}
                  onInput={(e) => { const v = (e.currentTarget as HTMLInputElement).value; setDraft((x) => setText(x, item, v)); }}
                  onKeyDown={(e) => { if (e.key === "Enter") e.preventDefault(); }} />
              )}
            </div>
          </li>
        ))}
      </ol>
      <div class="spq-foot">
        <Button variant="primary" disabled={busy || open.length > 0} onClick={onSend}>{busy ? "Sending…" : "Send answers"}</Button>
        {open.length > 0 && <span class="faint">{`Answer ${open.map((o) => items.indexOf(o) + 1).join(", ")} to send.`}</span>}
      </div>
      {error && <div class="qcard-error tone-red text" data-error>{error}</div>}
    </div>
  );
}

function RankList(props: { item: AskItem; order: string[]; disabled: boolean; onMove: (option: string, delta: -1 | 1) => void }): JSX.Element {
  const { order } = props;
  const onKey = (o: string) => (e: KeyboardEvent) => {
    if (!e.altKey || (e.key !== "ArrowUp" && e.key !== "ArrowDown")) return;
    e.preventDefault();
    props.onMove(o, e.key === "ArrowUp" ? -1 : 1);
    // keep focus on the moved row
    const list = (e.currentTarget as HTMLElement).parentElement;
    requestAnimationFrame(() => list?.querySelector<HTMLElement>(`[data-rank="${CSS.escape(o)}"]`)?.focus());
  };
  return (
    <ol class="spq-rank" aria-label={`${props.item.prompt} (Alt+↑/↓ to reorder)`}>
      {order.map((o, i) => (
        <li key={o} class="spq-rank-row" tabIndex={0} data-rank={o} onKeyDown={onKey(o)}>
          <span class="spq-rank-n">{i + 1}</span>
          <span class="spq-rank-label text">{o}</span>
          <button type="button" class="spq-mv" aria-label={`Move ${o} up`} disabled={props.disabled || i === 0} onClick={() => props.onMove(o, -1)}>↑</button>
          <button type="button" class="spq-mv" aria-label={`Move ${o} down`} disabled={props.disabled || i === order.length - 1} onClick={() => props.onMove(o, 1)}>↓</button>
        </li>
      ))}
    </ol>
  );
}
