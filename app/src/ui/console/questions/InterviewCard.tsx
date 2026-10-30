// Interview card: a batched ui_ask_user (C3) shown as ONE card in the thread — an intro line, then
// each sub-question with option chips (single / multi), a ranked list (rank) and an optional
// free-text field. "Send answers" posts the whole batch as a JSON object ({id: value}); a single
// trusted click, armed like the approval cards (an agent can phrase a decision as a question).
// A7's behaviours live here too (the model is interview.ts): the "n/3 answered" counter, the
// "Answer 1, 3 to send." list, Move up/down and Alt+↑/↓ ranking, multi chips plus text, and
// single-choice chip/text exclusivity. Option and prompt text render as text only.
import type { JSX } from "preact";
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import type { AskItem, Question } from "../../../api/types";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import { ARM_MS, clickArmed, isTrustedActivation } from "../approvals/approvalKeys";
import { answerOf, buildAnswers, emptyDraft, introOf, move, pick, setText, unanswered, type InterviewDraft } from "./interview";
import "./interview.css";

export function InterviewCard(props: {
  question: Question; runLabel: string;
  /** Posts the answer text (tests inject it; default: the core's /questions/{id}/answer). */
  send?: (id: string, text: string) => Promise<unknown>;
}): JSX.Element {
  const { question, runLabel } = props;
  const items = question.items ?? [];
  const [draft, setDraft] = useState<InterviewDraft>(() => emptyDraft(items));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const shownKey = `${question.question_id}\n${JSON.stringify(items)}`;
  const arm = useRef({ key: shownKey, at: performance.now() });
  if (arm.current.key !== shownKey) arm.current = { key: shownKey, at: performance.now() };
  // `data-armed` on the card once Send can count (observable state for tests and styling; the
  // rule itself is clickArmed at click time)
  const [armedKey, setArmedKey] = useState<string | null>(null);
  useEffect(() => {
    const t = setTimeout(() => setArmedKey(shownKey), Math.max(0, ARM_MS + 1 - (performance.now() - arm.current.at)));
    return () => clearTimeout(t);
  }, [shownKey]);

  const open = unanswered(items, draft);
  const done = items.length - open.length;

  const onSend = (e: MouseEvent) => {
    if (!isTrustedActivation(e) || !clickArmed(e, arm.current.at, performance.now())) return;
    if (busy || open.length > 0) return;
    setBusy(true);
    setError(null);
    const post = props.send ?? ((id: string, t: string) => actions.client().answer(id, t));
    void (async () => {
      try {
        await post(question.question_id, JSON.stringify(buildAnswers(items, draft)));
        actions.removeQuestion(question.question_id);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        setBusy(false);
      }
    })();
  };

  return (
    <div data-card data-interview={question.question_id} data-armed={armedKey === shownKey ? "" : undefined} class="icard" role="group" aria-label={`Questions from ${runLabel}`}>
      <div class="icard-head">
        <span class="icard-flag">? INTERVIEW</span>
        <span class="faint text icard-run">{runLabel}</span>
        <span class="icard-count faint" data-count aria-live="polite">{`${done}/${items.length} answered`}</span>
      </div>
      {introOf(question.question) !== "" && <div class="icard-intro text">{introOf(question.question)}</div>}
      <ol class="icard-list">
        {items.map((item, n) => {
          const a = draft[item.id];
          const promptId = `icard-${question.question_id}-${item.id}`;
          return (
            <li key={item.id} class="icard-q" data-q={item.id} data-answered={answerOf(item, a) !== null ? "" : undefined}>
              <div class="icard-prompt text" id={promptId}>
                <span class="icard-n faint" aria-hidden="true">{n + 1}</span>
                {item.prompt}
                {item.multi && <span class="icard-hint faint"> · pick any</span>}
                {item.rank && <span class="icard-hint faint"> · order of importance, first = most</span>}
              </div>
              {item.rank
                ? <RankList item={item} order={a?.order ?? []} disabled={busy} onMove={(o, d) => setDraft((x) => move(x, item, o, d))} />
                : item.options.length > 0 && (
                  <div class="icard-opts" role="group" aria-labelledby={promptId}>
                    {item.options.map((o) => {
                      const on = (a?.picks ?? []).includes(o);
                      return (
                        <button type="button" key={o} class={`icard-opt${on ? " on" : ""}`} data-on={on ? "" : undefined}
                          aria-pressed={on} disabled={busy} onClick={() => setDraft((x) => pick(x, item, o))}>
                          <span class="text">{o}</span>
                        </button>
                      );
                    })}
                  </div>
                )}
              {item.allow_text && (
                <input type="text" class="icard-text" aria-label={`Your own answer: ${item.prompt}`}
                  placeholder={!item.options.length ? "type your answer" : item.rank ? "or describe the order" : item.multi ? "and/or add your own" : "or type your own"}
                  value={a?.text ?? ""} disabled={busy}
                  onInput={(e) => { const v = (e.currentTarget as HTMLInputElement).value; setDraft((x) => setText(x, item, v)); }}
                  onKeyDown={(e) => { if (e.key === "Enter") e.preventDefault(); }} />
              )}
            </li>
          );
        })}
      </ol>
      <div class="icard-foot">
        <Button variant="primary" disabled={busy || open.length > 0} onClick={onSend}>{busy ? "Sending…" : "Send answers"}</Button>
        {open.length > 0 && <span class="faint text" data-open>{`Answer ${open.map((o) => items.indexOf(o) + 1).join(", ")} to send.`}</span>}
      </div>
      {error && <div class="icard-error tone-red text" data-error>{error}</div>}
    </div>
  );
}

/** A rank question: every option in order, Move up/down buttons, Alt+↑/↓ on a focused row
 *  (focus stays on the moved row). */
function RankList(props: { item: AskItem; order: string[]; disabled: boolean; onMove: (option: string, delta: -1 | 1) => void }): JSX.Element {
  const { order } = props;
  const list = useRef<HTMLOListElement>(null);
  /** The row a keyboard move just moved: re-focused as soon as the new order is in the DOM (a
   *  keyed move can drop focus). Done in the commit, not a later frame, so it can never steal
   *  focus from a row the user has moved to since. */
  const refocus = useRef<string | null>(null);
  useLayoutEffect(() => {
    const o = refocus.current;
    refocus.current = null;
    if (o) list.current?.querySelector<HTMLElement>(`[data-rank="${CSS.escape(o)}"]`)?.focus();
  }, [order.join("\n")]);
  const onKey = (o: string) => (e: KeyboardEvent) => {
    if (!e.altKey || (e.key !== "ArrowUp" && e.key !== "ArrowDown") || props.disabled) return;
    e.preventDefault();
    const i = order.indexOf(o);
    const j = i + (e.key === "ArrowUp" ? -1 : 1);
    if (j < 0 || j >= order.length) return;
    refocus.current = o;
    props.onMove(o, e.key === "ArrowUp" ? -1 : 1);
  };
  return (
    <ol class="icard-rankl" ref={list} aria-label={`${props.item.prompt} (Alt+↑/↓ to reorder)`}>
      {order.map((o, i) => (
        <li key={o} class="icard-rank-row" tabIndex={0} data-rank={o} onKeyDown={onKey(o)}>
          <span class="icard-rank-n">{i + 1}</span>
          <span class="icard-rank-label text">{o}</span>
          <button type="button" class="icard-mv" aria-label={`Move ${o} up`} disabled={props.disabled || i === 0} onClick={() => props.onMove(o, -1)}>↑</button>
          <button type="button" class="icard-mv" aria-label={`Move ${o} down`} disabled={props.disabled || i === order.length - 1} onClick={() => props.onMove(o, 1)}>↓</button>
        </li>
      ))}
    </ol>
  );
}
