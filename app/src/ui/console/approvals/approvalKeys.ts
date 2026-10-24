// Approval safety: which input may decide an approval card.
// A card decides only on (a) a trusted click on one of its buttons, or (b) a trusted, non-repeat,
// unmodified key pressed while focus is inside that card. Never a paste, never a global shortcut,
// never a synthetic event. Both paths wait ARM_MS after the card appears or its payload changes,
// so a card that pops up (or refreshes) under a click or key in progress can't be decided by it.
// The letter shortcuts m/a/k/r also need focus to have entered the card by KEYBOARD (Tab /
// :focus-visible) at least ARM_MS ago; a pointer press inside the card turns them off until the
// card is focused by keyboard again, so clicking card text and then typing can never decide.
// Leaving the window (Cmd-Tab) turns them off too, and the focus the browser re-fires on the
// same element when the window comes back is not keyboard focus (the Meta of Cmd-Tab is a key).
import { isTrustedKey, msSinceLastPaste } from "../../../lib/keys";

export type ApprovalAction = "approve" | "keep_branch" | "reject" | "diff";

/** A card ignores decisions for this long after it appears or its payload_hash changes. */
export const ARM_MS = 400;
/** Keys are ignored for this long after any paste/drop. */
export const PASTE_QUIET_MS = 1000;

const KEY_ACTIONS: Record<string, ApprovalAction> = {
  m: "approve", a: "approve", k: "keep_branch", r: "reject", d: "diff",
};

const DECISION_LETTERS = new Set(["m", "a", "k", "r"]);

type Modality = "keyboard" | "pointer" | null;
let lastModality: Modality = null;
let trackedDoc: Document | null = null;
/** Bumps on every window blur: keyboard focus recorded before it no longer counts. */
let blurEpoch = 0;
/** The element that was focused when the window blurred: its re-fired focusin is a restore. */
let refocusTarget: Element | null = null;
const onTrackedKey = (e: KeyboardEvent) => { if (isTrustedKey(e)) lastModality = "keyboard"; };
const onTrackedPointer = () => { lastModality = "pointer"; };
const onWindowBlur = (e: Event) => {
  if (!trackedDoc || e.target instanceof Node) return; // the window itself, not an element's blur
  blurEpoch++;
  refocusTarget = trackedDoc.activeElement;
};

function untrack(doc: Document): void {
  doc.removeEventListener("keydown", onTrackedKey, true);
  doc.removeEventListener("pointerdown", onTrackedPointer, true);
  doc.removeEventListener("mousedown", onTrackedPointer, true);
  doc.defaultView?.removeEventListener("blur", onWindowBlur);
}

/** Installs (once per document) capture listeners that remember the last input modality. */
export function trackFocusModality(doc: Document): void {
  if (trackedDoc === doc) return;
  if (trackedDoc) untrack(trackedDoc);
  doc.addEventListener("keydown", onTrackedKey, true);
  doc.addEventListener("pointerdown", onTrackedPointer, true);
  doc.addEventListener("mousedown", onTrackedPointer, true);
  doc.defaultView?.addEventListener("blur", onWindowBlur);
  trackedDoc = doc;
}

/** Current window-blur epoch (keyboard focus is valid only within the epoch it was taken in). */
export function windowEpoch(): number {
  return blurEpoch;
}

/**
 * A focusin on `target`: true if it is the browser restoring focus to the element that was
 * active when the window blurred (not keyboard focus). Any focusin ends the restore window.
 */
export function consumeRefocus(target: EventTarget | null): boolean {
  const restore = refocusTarget !== null && target === refocusTarget;
  refocusTarget = null;
  return restore;
}

/** Tests only. */
export function resetFocusModality(): void {
  if (trackedDoc) untrack(trackedDoc);
  trackedDoc = null;
  lastModality = null;
  refocusTarget = null;
}

/**
 * Did focus land on `el` by keyboard? Never when the last input was a pointer press, always when
 * it was a trusted key (Tab); before any input, the browser's own :focus-visible heuristic.
 */
export function focusIsKeyboard(el: Element): boolean {
  if (lastModality !== null) return lastModality === "keyboard";
  try {
    return el.matches(":focus-visible");
  } catch {
    return lastModality === "keyboard";
  }
}

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

/**
 * The card's shortcut for this key, or null. `keyFocusAt` is when focus last entered the card by
 * keyboard (null: it came by pointer, or a pointer pressed inside the card since): the decision
 * letters m/a/k/r need it to be at least ARM_MS old. Enter/Space use keyGuardsPass only.
 */
export function keyToAction(
  e: KeyboardEvent, card: HTMLElement, armedAt: number, now: number, keyFocusAt: number | null,
): ApprovalAction | null {
  if (!keyGuardsPass(e, card, armedAt, now)) return null;
  if (typeof e.key !== "string") return null;
  const k = e.key.toLowerCase();
  if (DECISION_LETTERS.has(k) && (keyFocusAt === null || now - keyFocusAt < ARM_MS)) return null;
  return KEY_ACTIONS[k] ?? null;
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
