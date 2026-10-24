// Approval safety: which input may decide an approval card.
// A card decides only on (a) a trusted click on one of its buttons, or (b) a trusted, non-repeat,
// unmodified key pressed while focus is inside that card. Never a paste, never a global shortcut,
// never a synthetic event. Both paths wait ARM_MS after the card appears or its payload changes,
// so a card that pops up (or refreshes) under a click or key in progress can't be decided by it.
import { isTrustedKey, msSinceLastPaste } from "../../../lib/keys";

export type ApprovalAction = "approve" | "keep_branch" | "reject" | "diff";

/** A card ignores decisions for this long after it appears or its payload_hash changes. */
export const ARM_MS = 400;
/** Keys are ignored for this long after any paste/drop. */
export const PASTE_QUIET_MS = 1000;

const KEY_ACTIONS: Record<string, ApprovalAction> = {
  m: "approve", a: "approve", k: "keep_branch", r: "reject", d: "diff",
};

const TEXT_INPUT_TYPES = new Set(["", "text", "search", "url", "tel", "email", "password", "number"]);

/** True for elements that take typed text (keys there are text, never commands). */
export function isTextInput(el: EventTarget | null): boolean {
  if (!(el instanceof Element)) return false;
  if (el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement) return true;
  if (el instanceof HTMLInputElement) return TEXT_INPUT_TYPES.has(el.type.toLowerCase());
  return el instanceof HTMLElement && el.isContentEditable;
}

/** Every key rule except the key map: trusted, not repeat, unmodified, focus in card, armed, paste-quiet, not typing. */
export function keyGuardsPass(e: KeyboardEvent, card: HTMLElement, armedAt: number, now: number): boolean {
  if (!isTrustedKey(e)) return false;
  if (e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return false;
  const focused = card.ownerDocument.activeElement;
  if (!focused || !card.contains(focused)) return false;
  if (now - armedAt < ARM_MS) return false;
  if (msSinceLastPaste() < PASTE_QUIET_MS) return false;
  return !(isTextInput(e.target) || isTextInput(focused));
}

export function keyToAction(e: KeyboardEvent, card: HTMLElement, armedAt: number, now: number): ApprovalAction | null {
  if (!keyGuardsPass(e, card, armedAt, now)) return null;
  return typeof e.key === "string" ? KEY_ACTIONS[e.key.toLowerCase()] ?? null : null;
}

/** Enter / Space: the keys that natively activate a focused button. */
export function isActivationKey(e: KeyboardEvent): boolean {
  return e.key === "Enter" || e.key === " " || e.key === "Spacebar";
}

/** A real user activation: never `el.click()` or a dispatched event. */
export function isTrustedActivation(e: Event): boolean {
  return e.isTrusted === true;
}

/**
 * Timing half of the click rule: armed, and not the 2nd+ click of a multi-click (so a double
 * click can't land its second click on a card that just refreshed). Callers also require
 * isTrustedActivation(e).
 */
export function clickArmed(e: MouseEvent, armedAt: number, now: number): boolean {
  if (now - armedAt < ARM_MS) return false;
  return !(e.detail > 1);
}

/**
 * The full rule for a decision button's click: armed, exactly one mouse click (native keyboard
 * activation has detail 0; Enter/Space are handled at keydown by keyGuardsPass instead), and
 * its pointerdown happened after the card armed for the current hash, so a press that started
 * before the card changed never counts. Callers also require isTrustedActivation(e).
 */
export function decisionClickCounts(e: MouseEvent, pointerDownAt: number | null, armedAt: number, now: number): boolean {
  if (!clickArmed(e, armedAt, now)) return false;
  if (e.detail !== 1) return false;
  return pointerDownAt !== null && pointerDownAt >= armedAt;
}
