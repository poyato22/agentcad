// Question card: an agent's question (from question_asked). Option buttons answer at once; free
// text is sent only by an explicit "Send" click (Enter in the box does nothing). The card leaves
// the store on success, and again on question_answered (reduce.ts). Text renders as text only.
import type { JSX } from "preact";
import { useState } from "preact/hooks";
import type { Question } from "../../../api/types";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import "./approvals.css";

export function QuestionCard(props: { question: Question; runLabel: string }): JSX.Element {
  const q = props.question;
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const send = async (answer: string) => {
    if (busy || !answer.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await actions.client().answer(q.question_id, answer);
      actions.removeQuestion(q.question_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-card data-question={q.question_id} class="qcard" role="group" aria-label={`Question from ${props.runLabel}`}>
      <div class="qcard-head">
        <span class="qcard-flag">? QUESTION</span>
        <span class="faint text acard-run">{props.runLabel}</span>
      </div>
      <div class="qcard-question text">{q.question}</div>
      {q.options.length > 0 && (
        <div class="qcard-options">
          {q.options.map((o) => (
            <Button key={o} disabled={busy} onClick={() => void send(o)}><span class="text">{o}</span></Button>
          ))}
        </div>
      )}
      <div class="qcard-free">
        <input type="text" aria-label="Answer" placeholder="or type an answer" value={text} disabled={busy}
          onInput={(e) => setText((e.currentTarget as HTMLInputElement).value)}
          onKeyDown={(e) => { if (e.key === "Enter") e.preventDefault(); }} />
        <Button disabled={busy || !text.trim()} onClick={() => void send(text)}>Send</Button>
      </div>
      {error && <div class="qcard-error tone-red text" data-error>{error}</div>}
    </div>
  );
}
