// Pure model for the interview card: the per-question answer state and how it serializes into the
// JSON object the core's ui_ask_user expects (`{id: value}`, parsed by core _batch_answers). A
// single-select answers with the chosen string; multi-select with an array; a ranked question with
// the chosen options in order; a free-text answer overrides when the user typed something.
import type { AskItem } from "../../../api/types";

/** What the user has entered for one sub-question. */
export interface ItemAnswer {
  /** Picked options, in click order (rank uses the order; single keeps the last one only). */
  picks: string[];
  /** Free-text answer (used when non-empty, or when the question has no options). */
  text: string;
}

export function emptyAnswers(items: AskItem[]): Record<string, ItemAnswer> {
  return Object.fromEntries(items.map((q) => [q.id, { picks: [], text: "" }]));
}

/** Toggle an option for a sub-question, honouring single vs multi vs rank. */
export function togglePick(item: AskItem, current: string[], option: string): string[] {
  const has = current.includes(option);
  if (!item.multi && !item.rank) return has ? [] : [option];     // single-select
  if (has) return current.filter((o) => o !== option);           // deselect (multi / rank)
  return [...current, option];                                   // add (order matters for rank)
}

/** The value sent for one sub-question, or undefined when it has no answer yet. */
export function itemValue(item: AskItem, a: ItemAnswer): string | string[] | undefined {
  const text = a.text.trim();
  if (text !== "") return text;                 // free text always wins
  if (a.picks.length === 0) return undefined;
  if (item.multi || item.rank) return a.picks;  // array (ranked = ordered)
  return a.picks[0];                            // single
}

/** The JSON-serializable answer object for the whole batch ({id: value}). */
export function batchAnswer(items: AskItem[], answers: Record<string, ItemAnswer>): Record<string, string | string[]> {
  const out: Record<string, string | string[]> = {};
  for (const q of items) {
    const v = itemValue(q, answers[q.id] ?? { picks: [], text: "" });
    if (v !== undefined) out[q.id] = v;
  }
  return out;
}

/** True once every sub-question that must be answered has an answer. A question with options but
 *  no free text must be picked; an open (text-only) question must be typed. */
export function isComplete(items: AskItem[], answers: Record<string, ItemAnswer>): boolean {
  return items.every((q) => itemValue(q, answers[q.id] ?? { picks: [], text: "" }) !== undefined);
}
