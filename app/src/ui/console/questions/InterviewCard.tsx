// Interview card: a batched ui_ask_user (C3) shown as ONE card in the thread — an intro line, then
// each sub-question with option chips (single / multi / rank) and an optional free-text field.
// "Send answers" posts the whole batch as a JSON object ({id: value}); a single trusted click,
// armed like the approval cards (an agent can phrase a decision as a question). Small and reusable
// so A7's spec UI can mount it too. Option and prompt text render as text only.
import type { JSX } from "preact";
import { useRef, useState } from "preact/hooks";
import type { AskItem, Question } from "../../../api/types";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import { clickArmed, isTrustedActivation } from "../approvals/approvalKeys";
import {
  batchAnswer, emptyAnswers, introOf, isComplete, type ItemAnswer, togglePick,
} from "./interview";
import "./interview.css";

export function InterviewCard({ question, runLabel }: { question: Question; runLabel: string }): JSX.Element {
  const items = question.items ?? [];
  const [answers, setAnswers] = useState<Record<string, ItemAnswer>>(() => emptyAnswers(items));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const shownKey = `${question.question_id}\n${items.map((q) => q.id).join(",")}`;
  const arm = useRef({ key: shownKey, at: performance.now() });
  if (arm.current.key !== shownKey) arm.current = { key: shownKey, at: performance.now() };

  const complete = isComplete(items, answers);

  const setItem = (id: string, next: ItemAnswer) => setAnswers((a) => ({ ...a, [id]: next }));
  const onToggle = (item: AskItem, option: string) => {
    const cur = answers[item.id] ?? { picks: [], text: "" };
    setItem(item.id, { ...cur, picks: togglePick(item, cur.picks, option) });
  };
  const onText = (item: AskItem, text: string) => {
    const cur = answers[item.id] ?? { picks: [], text: "" };
    setItem(item.id, { ...cur, text });
  };

  const onSend = (e: MouseEvent) => {
    if (!isTrustedActivation(e) || !clickArmed(e, arm.current.at, performance.now())) return;
    if (busy || !complete) return;
    setBusy(true);
    setError(null);
    void (async () => {
      try {
        await actions.client().answer(question.question_id, JSON.stringify(batchAnswer(items, answers)));
        actions.removeQuestion(question.question_id);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        setBusy(false);
      }
    })();
  };

  return (
    <div data-card data-interview={question.question_id} class="icard" role="group" aria-label={`Questions from ${runLabel}`}>
      <div class="icard-head">
        <span class="icard-flag">? INTERVIEW</span>
        <span class="faint text icard-run">{runLabel}</span>
      </div>
      {introOf(question.question) !== "" && <div class="icard-intro text">{introOf(question.question)}</div>}
      <ol class="icard-list">
        {items.map((item) => {
          const a = answers[item.id] ?? { picks: [], text: "" };
          return (
            <li key={item.id} class="icard-q" data-q={item.id}>
              <div class="icard-prompt text">
                {item.prompt}
                {item.multi && <span class="icard-hint faint"> · pick any</span>}
                {item.rank && <span class="icard-hint faint"> · tap in order of importance</span>}
              </div>
              {item.options.length > 0 && (
                <div class="icard-opts" role={item.multi || item.rank ? "group" : "radiogroup"} aria-label={item.prompt}>
                  {item.options.map((o) => {
                    const idx = a.picks.indexOf(o);
                    const on = idx >= 0;
                    return (
                      <button type="button" key={o} class={`icard-opt${on ? " on" : ""}`} data-on={on ? "" : undefined}
                        aria-pressed={on} disabled={busy} onClick={() => onToggle(item, o)}>
                        {item.rank && on && <span class="icard-rank" aria-hidden="true">{idx + 1}</span>}
                        <span class="text">{o}</span>
                      </button>
                    );
                  })}
                </div>
              )}
              {item.allow_text && (
                <input type="text" class="icard-text" aria-label={`Your answer to: ${item.prompt}`}
                  placeholder={item.options.length ? "or type your own" : "type your answer"}
                  value={a.text} disabled={busy}
                  onInput={(e) => onText(item, (e.currentTarget as HTMLInputElement).value)}
                  onKeyDown={(e) => { if (e.key === "Enter") e.preventDefault(); }} />
              )}
            </li>
          );
        })}
      </ol>
      <div class="icard-foot">
        <Button variant="primary" disabled={busy || !complete} onClick={onSend}>Send answers</Button>
        {!complete && <span class="faint text">Answer every question to send.</span>}
      </div>
      {error && <div class="icard-error tone-red text" data-error>{error}</div>}
    </div>
  );
}
