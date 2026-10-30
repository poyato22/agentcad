// Pure model for the interview card: the per-question draft and how it serializes into the JSON
// object the core's ui_ask_user expects (`{id: value}`, parsed by core _batch_answers). Ported from
// A7's spec card (ba9fce9 ui/spec/model.ts):
//   · single — one answer: the chip, or the typed text; whichever the user touched last wins and
//     the other is cleared, so what is shown is what is sent;
//   · multi — the picked chips plus the typed text (both are sent);
//   · rank — every option, pre-filled in the given order and reordered with Move up/down
//     (Alt+↑/↓); the full order is always sent (typed text replaces it).
import type { AskItem } from "../../../api/types";

/** The intro line: the core appends every prompt to `question` for older UIs ("…\n1. …");
 *  the card lists them itself, so the appended listing is dropped (from A7's card). */
export function introOf(question: string): string {
  const listed = question.search(/\n1\. /);
  return (listed >= 0 ? question.slice(0, listed) : question).trim();
}

/** What the user has picked or typed for one question. `order` is the rank order (rank kind). */
export interface AnswerDraft { picks: string[]; text: string; order: string[] }
export type InterviewDraft = Record<string, AnswerDraft>;

const blank = (q: AskItem): AnswerDraft => ({ picks: [], text: "", order: q.rank ? [...q.options] : [] });

export function emptyDraft(items: readonly AskItem[]): InterviewDraft {
  return Object.fromEntries(items.map((q) => [q.id, blank(q)]));
}

/** Toggle an option chip: single replaces (and clears typed text), multi adds/removes. */
export function pick(draft: InterviewDraft, q: AskItem, option: string): InterviewDraft {
  const cur = draft[q.id] ?? blank(q);
  const picks = q.multi
    ? (cur.picks.includes(option) ? cur.picks.filter((p) => p !== option) : [...cur.picks, option])
    : (cur.picks[0] === option ? [] : [option]);
  return { ...draft, [q.id]: { ...cur, picks, text: q.multi ? cur.text : "" } };
}

/** Typed text. For a single question, typing something clears the picked chip. */
export function setText(draft: InterviewDraft, q: AskItem, text: string): InterviewDraft {
  const cur = draft[q.id] ?? blank(q);
  return { ...draft, [q.id]: { ...cur, text, picks: q.multi || q.rank || !text.trim() ? cur.picks : [] } };
}

/** Move a ranked option up (-1) or down (+1). */
export function move(draft: InterviewDraft, q: AskItem, option: string, delta: -1 | 1): InterviewDraft {
  const cur = draft[q.id];
  if (!cur) return draft;
  const i = cur.order.indexOf(option);
  const j = i + delta;
  if (i < 0 || j < 0 || j >= cur.order.length) return draft;
  const order = [...cur.order];
  [order[i], order[j]] = [order[j], order[i]];
  return { ...draft, [q.id]: { ...cur, order } };
}

/** The value sent for one question, or null while it is unanswered. Single: the typed text, else
 *  the chip. Multi: the chips, plus the typed text. Rank: the full order (typed text replaces it). */
export function answerOf(q: AskItem, a: AnswerDraft | undefined): string | string[] | null {
  if (!a) return null;
  const text = q.allow_text ? a.text.trim() : "";
  if (q.rank) return text || (a.order.length === q.options.length && a.order.length > 0 ? a.order : null);
  if (q.multi) {
    const all = text ? [...a.picks, text] : a.picks;
    return all.length ? all : null;
  }
  return text || a.picks[0] || null;
}

/** {id: value} for every answered question (the core's batch answer). */
export function buildAnswers(items: readonly AskItem[], draft: InterviewDraft): Record<string, string | string[]> {
  const out: Record<string, string | string[]> = {};
  for (const q of items) {
    const v = answerOf(q, draft[q.id]);
    if (v !== null) out[q.id] = v;
  }
  return out;
}

/** The questions still unanswered (Send waits for none). */
export function unanswered(items: readonly AskItem[], draft: InterviewDraft): AskItem[] {
  return items.filter((q) => answerOf(q, draft[q.id]) === null);
}
