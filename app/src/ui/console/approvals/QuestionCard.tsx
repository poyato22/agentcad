import type { JSX } from "preact";
import type { Question } from "../../../api/types";

// STUB (Task 2). Task 8 replaces this file. Final props signature.
export function QuestionCard(props: { question: Question; runLabel: string }): JSX.Element {
  return (
    <div data-card data-stub="QuestionCard" role="group" aria-label={`Question from ${props.runLabel}`}
      style={{ margin: "6px 8px", padding: "6px 8px" }}>
      <span class="label">question</span> <span class="text">{props.question.question}</span>
    </div>
  );
}
