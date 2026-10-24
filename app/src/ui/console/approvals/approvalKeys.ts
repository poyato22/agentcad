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

export function keyToAction(e: KeyboardEvent, card: HTMLElement, armedAt: number, now: number): ApprovalAction | null {
  if (!isTrustedKey(e)) return null;
  if (e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return null;
  const focused = card.ownerDocument.activeElement;
  if (!focused || !card.contains(focused)) return null;
  if (now - armedAt < ARM_MS) return null;
  if (msSinceLastPaste() < PASTE_QUIET_MS) return null;
  if (isTextInput(e.target) || isTextInput(focused)) return null;
  return typeof e.key === "string" ? KEY_ACTIONS[e.key.toLowerCase()] ?? null : null;
}

/** A real user activation (mouse click, or Enter/Space on a focused button): never `el.click()`. */
export function isTrustedActivation(e: Event): boolean {
  return e.isTrusted === true;
}

/**
 * Timing half of the click rule: armed, and not the 2nd+ click of a multi-click (so a double
 * click can't land its second click on a card that just refreshed). The card also requires
 * isTrustedActivation(e).
 */
export function clickArmed(e: MouseEvent, armedAt: number, now: number): boolean {
  if (now - armedAt < ARM_MS) return false;
  return !(e.detail > 1);
}
