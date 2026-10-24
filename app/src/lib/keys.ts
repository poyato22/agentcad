// Keyboard helpers. Approval safety (Task 8) builds on isTrustedKey + msSinceLastPaste.
// Global shortcuts are registered on window in the capture phase, so they work while focus is in
// a text input and before any element handler can swallow the key.

let lastPaste = -Infinity;

/** Records the time of every paste and drop under `root`. Returns a cleanup function. */
export function trackPastes(root: Document): () => void {
  const mark = () => { lastPaste = performance.now(); };
  root.addEventListener("paste", mark, true);
  root.addEventListener("drop", mark, true);
  return () => {
    root.removeEventListener("paste", mark, true);
    root.removeEventListener("drop", mark, true);
  };
}

/** Milliseconds since the last paste/drop (Infinity if none). */
export function msSinceLastPaste(): number {
  return performance.now() - lastPaste;
}

/** A real key press by the user: trusted, not auto-repeat, not mid-IME composition. */
export function isTrustedKey(e: KeyboardEvent): boolean {
  return e.isTrusted === true && !e.repeat && !e.isComposing;
}

interface Combo { meta: boolean; ctrl: boolean; alt: boolean; shift: boolean; key: string }

const PUNCT_CODES: Record<string, string> = {
  ".": "Period", ",": "Comma", "/": "Slash", ";": "Semicolon", "'": "Quote", "[": "BracketLeft",
  "]": "BracketRight", "\\": "Backslash", "-": "Minus", "=": "Equal", "`": "Backquote",
};

/** "Meta+Shift+." · "Meta+K" · "Ctrl+Alt+Enter". Modifiers: Meta (Cmd, Mod), Ctrl, Alt (Option), Shift. */
function parseCombo(combo: string): Combo {
  const parts = combo.split("+");
  // "Meta++" means the "+" key
  const key = combo.endsWith("++") ? "+" : (parts.pop() ?? "");
  const c: Combo = { meta: false, ctrl: false, alt: false, shift: false, key };
  for (const raw of parts) {
    const m = raw.trim().toLowerCase();
    if (m === "meta" || m === "cmd" || m === "mod") c.meta = true;
    else if (m === "ctrl" || m === "control") c.ctrl = true;
    else if (m === "alt" || m === "option") c.alt = true;
    else if (m === "shift") c.shift = true;
    else if (m) throw new Error(`unknown modifier in shortcut ${combo}`);
  }
  return c;
}

function codeFor(key: string): string | null {
  if (/^[a-z]$/i.test(key)) return `Key${key.toUpperCase()}`;
  if (/^\d$/.test(key)) return `Digit${key}`;
  return PUNCT_CODES[key] ?? null;
}

function matches(c: Combo, e: KeyboardEvent): boolean {
  if (e.metaKey !== c.meta || e.ctrlKey !== c.ctrl || e.altKey !== c.alt || e.shiftKey !== c.shift) return false;
  const code = codeFor(c.key);
  if (code && e.code === code) return true;
  return typeof e.key === "string" && e.key.toLowerCase() === c.key.toLowerCase();
}

const registry = new Set<{ combo: Combo; handler: (e: KeyboardEvent) => void }>();
let installed = false;

function onKeyDown(e: KeyboardEvent) {
  for (const r of [...registry]) {
    if (matches(r.combo, e)) {
      e.preventDefault();
      r.handler(e);
    }
  }
}

/** Registers a global shortcut (capture phase on window, works inside inputs). Returns unregister. */
export function registerShortcut(combo: string, handler: (e: KeyboardEvent) => void): () => void {
  const entry = { combo: parseCombo(combo), handler };
  registry.add(entry);
  if (!installed) {
    window.addEventListener("keydown", onKeyDown, true);
    installed = true;
  }
  return () => {
    registry.delete(entry);
    if (registry.size === 0 && installed) {
      window.removeEventListener("keydown", onKeyDown, true);
      installed = false;
    }
  };
}
