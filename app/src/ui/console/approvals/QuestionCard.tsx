// Question card: an agent's question (from question_asked). Option buttons answer at once; free
// text is sent only by an explicit "Send" click (Enter in the box does nothing). The card leaves
// the store on success, and again on question_answered (reduce.ts). Text renders as text only.
// An agent can phrase a decision as a question ("merge now?"), so answering follows the card
// click rules: a trusted click, at least ARM_MS after the question appeared or changed.
import type { JSX } from "preact";
import { useRef, useState } from "preact/hooks";
import type { Question } from "../../../api/types";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import { clickArmed, isTrustedActivation } from "./approvalKeys";
import { InterviewCard } from "../questions/InterviewCard";
import "./approvals.css";

/** A batched question (ui_ask_user `questions`, the engineer's interview) gets the ONE interview
 *  card (console/questions, A2), wherever the question is shown (thread, step log, spec flow). */
export function QuestionCard(props: { question: Question; runLabel: string }): JSX.Element {
  return props.question.items?.length
    ? <InterviewCard question={props.question} runLabel={props.runLabel} />
    : <SingleQuestionCard {...props} />;
}

function SingleQuestionCard(props: { question: Question; runLabel: string }): JSX.Element {
  const q = props.question;
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const shownKey = `${q.question_id}\n${q.question}\n${q.options.join("\n")}`;
  const arm = useRef({ key: shownKey, at: performance.now() });
  if (arm.current.key !== shownKey) arm.current = { key: shownKey, at: performance.now() };

  const onAnswer = (answer: () => string) => (e: MouseEvent) => {
    if (!isTrustedActivation(e) || !clickArmed(e, arm.current.at, performance.now())) return;
    void send(answer());
  };

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
          {q.options.map((o, i) => (
            <Button key={`${i}:${o}`} disabled={busy} onClick={onAnswer(() => o)}><span class="text">{o}</span></Button>
          ))}
        </div>
      )}
      <div class="qcard-free">
        <input type="text" aria-label="Answer" placeholder="or type an answer" value={text} disabled={busy}
          onInput={(e) => setText((e.currentTarget as HTMLInputElement).value)}
          onKeyDown={(e) => { if (e.key === "Enter") e.preventDefault(); }} />
        <Button disabled={busy || !text.trim()} onClick={onAnswer(() => text)}>Send</Button>
      </div>
      {error && <div class="qcard-error tone-red text" data-error>{error}</div>}
    </div>
  );
}
